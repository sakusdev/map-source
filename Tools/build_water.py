#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import subprocess
from pathlib import Path
from typing import Iterable

import mapbox_earcut
import numpy as np
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box, shape
from shapely.ops import transform

CENTER_LAT = 36.075
CENTER_LON = 139.675
GRID_X = 60
GRID_Y = 63
TILE_SIZE_M = 4000.0
WORLD_ORIGIN_X_M = -120000.0
WORLD_ORIGIN_Z_M = -126000.0
WEB_MERCATOR_Z = 16

ROOT = Path(__file__).resolve().parents[1]
DEM_DIR = ROOT / "Server/data/regions/kanto/dem"
OUT_PC = ROOT / "Server/data/regions/kanto/water/pc"
OUT_QUEST = ROOT / "Server/data/regions/kanto/water/quest"
CACHE = ROOT / "Tools/.cache/overture-water"

PC_SIMPLIFY_M = 1.5
QUEST_SIMPLIFY_M = 7.0
PC_MAX_VERTICES = 12000
QUEST_MAX_VERTICES = 4000
WATER_OFFSET_M = 0.18
HEADER_SIZE = 16
VERTEX_SIZE = 6

WATER_SUBTYPES = {
    "ocean", "lake", "pond", "reservoir", "river", "stream", "water", "canal", "human_made"
}
LINE_WIDTH_M = {
    "river": 14.0,
    "canal": 8.0,
    "stream": 3.0,
    "water": 5.0,
    "human_made": 5.0,
}


def lonlat_to_global_px(lon: float, lat: float, z: int = WEB_MERCATOR_Z) -> tuple[float, float]:
    n = 256.0 * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def global_px_to_lonlat(x: float, y: float, z: int = WEB_MERCATOR_Z) -> tuple[float, float]:
    n = 256.0 * (1 << z)
    lon = x / n * 360.0 - 180.0
    merc_y = math.pi * (1.0 - 2.0 * y / n)
    lat = math.degrees(math.atan(math.sinh(merc_y)))
    return lon, lat


def ground_resolution_m_per_px(lat: float, z: int = WEB_MERCATOR_Z) -> float:
    return 156543.03392804097 * math.cos(math.radians(lat)) / (1 << z)


_CENTER_PX = lonlat_to_global_px(CENTER_LON, CENTER_LAT)
_MPP = ground_resolution_m_per_px(CENTER_LAT)


def lonlat_to_local(lon: float, lat: float) -> tuple[float, float]:
    px, py = lonlat_to_global_px(lon, lat)
    return (px - _CENTER_PX[0]) * _MPP, (_CENTER_PX[1] - py) * _MPP


def local_to_lonlat(x_m: float, z_m: float) -> tuple[float, float]:
    px = _CENTER_PX[0] + x_m / _MPP
    py = _CENTER_PX[1] - z_m / _MPP
    return global_px_to_lonlat(px, py)


def tile_bounds_world(x: int, y: int) -> tuple[float, float, float, float]:
    west = WORLD_ORIGIN_X_M + x * TILE_SIZE_M
    south = WORLD_ORIGIN_Z_M + y * TILE_SIZE_M
    return west, south, west + TILE_SIZE_M, south + TILE_SIZE_M


def tile_bbox_lonlat(x: int, y: int) -> tuple[float, float, float, float]:
    west, south, east, north = tile_bounds_world(x, y)
    wlon, slat = local_to_lonlat(west, south)
    elon, nlat = local_to_lonlat(east, north)
    return wlon, slat, elon, nlat


def validate_tile(x: int, y: int) -> None:
    if x < 0 or x >= GRID_X or y < 0 or y >= GRID_Y:
        raise SystemExit(f"tile outside Kanto grid: {x},{y}")


def _to_local_xy(x, y, z=None):
    if np.isscalar(x):
        return lonlat_to_local(float(x), float(y))
    xs = []
    zs = []
    for lon, lat in zip(x, y):
        lx, lz = lonlat_to_local(float(lon), float(lat))
        xs.append(lx)
        zs.append(lz)
    return xs, zs


def download_water(x: int, y: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{x:02d}_{y:02d}.geojsonseq"
    if out.is_file() and out.stat().st_size > 0:
        return out
    bbox = tile_bbox_lonlat(x, y)
    bbox_text = ",".join(f"{v:.8f}" for v in bbox)
    cmd = [
        "overturemaps", "download",
        f"--bbox={bbox_text}",
        "-f", "geojsonseq",
        "--type=water",
        "-o", str(out),
    ]
    print("running", " ".join(cmd))
    subprocess.run(cmd, check=True)
    if not out.exists():
        out.write_text("", encoding="utf-8")
    return out


def iter_geojsonseq(path: Path) -> Iterable[dict]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid GeoJSONSeq {path}:{line_no}: {exc}") from exc


def decode_gst2(path: Path) -> tuple[int, int, int, tuple[int, ...]]:
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"GST2" or data[4] != 2:
        raise ValueError(f"invalid GST2 DEM: {path}")
    grid = struct.unpack_from("<H", data, 6)[0]
    offset_mm, scale_mm = struct.unpack_from("<ii", data, 8)
    expected = 16 + grid * grid * 2
    if len(data) != expected:
        raise ValueError(f"invalid GST2 size: {path}")
    samples = struct.unpack_from("<" + "H" * (grid * grid), data, 16)
    return grid, offset_mm, scale_mm, samples


_dem_cache: dict[tuple[int, int], tuple[int, int, int, tuple[int, ...]]] = {}


def sample_dem(tile_x: int, tile_y: int, world_x: float, world_z: float) -> float:
    key = (tile_x, tile_y)
    if key not in _dem_cache:
        path = DEM_DIR / f"{tile_x:02d}_{tile_y:02d}.gst2"
        if not path.is_file():
            return 0.0
        _dem_cache[key] = decode_gst2(path)
    grid, offset_mm, scale_mm, samples = _dem_cache[key]
    west, south, _, _ = tile_bounds_world(tile_x, tile_y)
    fx = max(0.0, min(1.0, (world_x - west) / TILE_SIZE_M)) * (grid - 1)
    fz = max(0.0, min(1.0, (world_z - south) / TILE_SIZE_M)) * (grid - 1)
    x0, z0 = int(math.floor(fx)), int(math.floor(fz))
    x1, z1 = min(x0 + 1, grid - 1), min(z0 + 1, grid - 1)
    tx, tz = fx - x0, fz - z0

    def h(ix: int, iz: int) -> float:
        sample = samples[iz * grid + ix]
        return (offset_mm + sample * scale_mm) * 0.001

    h00, h10 = h(x0, z0), h(x1, z0)
    h01, h11 = h(x0, z1), h(x1, z1)
    return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz


def polygon_level(poly: Polygon, tile_x: int, tile_y: int, subtype: str) -> float:
    if subtype == "ocean":
        return WATER_OFFSET_M
    coords = list(poly.exterior.coords)
    if len(coords) > 24:
        step = max(1, len(coords) // 24)
        coords = coords[::step]
    rp = poly.representative_point()
    coords.append((rp.x, rp.y))
    vals = [sample_dem(tile_x, tile_y, float(x), float(z)) for x, z in coords]
    if not vals:
        return WATER_OFFSET_M
    return float(statistics.median(vals)) + WATER_OFFSET_M


def polygon_parts(geom) -> Iterable[Polygon]:
    if geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, MultiPolygon):
        for p in geom.geoms:
            if not p.is_empty:
                yield p


def water_geometry(feature: dict):
    props = feature.get("properties") or {}
    subtype = str(props.get("subtype") or "water")
    if subtype not in WATER_SUBTYPES:
        return None, subtype
    if props.get("is_intermittent") is True:
        return None, subtype
    raw = feature.get("geometry")
    if not raw:
        return None, subtype
    geom = transform(_to_local_xy, shape(raw))
    if isinstance(geom, (LineString, MultiLineString)):
        width = LINE_WIDTH_M.get(subtype, 5.0)
        geom = geom.buffer(width * 0.5, cap_style=2, join_style=2)
    elif isinstance(geom, Point):
        return None, subtype
    return geom, subtype


def earcut_polygon(poly: Polygon) -> tuple[list[tuple[float, float]], list[int]]:
    rings: list[list[tuple[float, float]]] = []
    ext = [(float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1]]
    if len(ext) < 3:
        return [], []
    rings.append(ext)
    for hole in poly.interiors:
        ring = [(float(x), float(y)) for x, y in list(hole.coords)[:-1]]
        if len(ring) >= 3:
            rings.append(ring)
    vertices = [p for ring in rings for p in ring]
    if len(vertices) < 3:
        return [], []
    ring_ends = []
    total = 0
    for ring in rings:
        total += len(ring)
        ring_ends.append(total)
    arr = np.asarray(vertices, dtype=np.float64)
    ends = np.asarray(ring_ends, dtype=np.uint32)
    idx = mapbox_earcut.triangulate_float64(arr, ends)
    return vertices, [int(v) for v in idx]


def build_mesh_records(features: Iterable[dict], tile_x: int, tile_y: int, simplify_m: float, max_vertices: int):
    west, south, east, north = tile_bounds_world(tile_x, tile_y)
    clip_box = box(west, south, east, north)
    candidates: list[tuple[float, Polygon, str]] = []
    for feature in features:
        geom, subtype = water_geometry(feature)
        if geom is None or geom.is_empty:
            continue
        clipped = geom.intersection(clip_box)
        if clipped.is_empty:
            continue
        if simplify_m > 0:
            clipped = clipped.simplify(simplify_m, preserve_topology=True)
        for poly in polygon_parts(clipped):
            if poly.area >= 4.0:
                candidates.append((float(poly.area), poly, subtype))

    candidates.sort(key=lambda v: v[0], reverse=True)
    out_vertices: list[tuple[float, float, float]] = []
    out_indices: list[int] = []
    accepted_polys = 0
    for _, poly, subtype in candidates:
        verts2, idx = earcut_polygon(poly)
        if not verts2 or not idx:
            continue
        if len(out_vertices) + len(verts2) > max_vertices:
            continue
        level = polygon_level(poly, tile_x, tile_y, subtype)
        base = len(out_vertices)
        for wx, wz in verts2:
            out_vertices.append((wx - west, level, wz - south))
        out_indices.extend(base + i for i in idx)
        accepted_polys += 1
    return out_vertices, out_indices, accepted_polys


def encode_gsw1(vertices: list[tuple[float, float, float]], indices: list[int]) -> bytes:
    if len(vertices) > 65535:
        raise ValueError("GSW1 vertex limit exceeded")
    if any(i < 0 or i >= len(vertices) for i in indices):
        raise ValueError("invalid triangle index")
    if vertices:
        base_dm = int(round(min(v[1] for v in vertices) * 10.0))
    else:
        base_dm = 0
    payload = bytearray()
    for x, y, z in vertices:
        x_dm = max(0, min(65535, int(round(x * 10.0))))
        z_dm = max(0, min(65535, int(round(z * 10.0))))
        dy_dm = max(-32768, min(32767, int(round(y * 10.0)) - base_dm))
        payload += struct.pack("<HhH", x_dm, dy_dm, z_dm)
    for i in indices:
        payload += struct.pack("<H", i)
    header = b"GSW1" + bytes((1, 0)) + struct.pack("<HIi", len(vertices), len(indices), base_dm)
    return header + payload


def build_tile(x: int, y: int, overwrite: bool = False) -> None:
    validate_tile(x, y)
    stem = f"{x:02d}_{y:02d}.gsw1"
    pc_path = OUT_PC / stem
    quest_path = OUT_QUEST / stem
    if not overwrite and pc_path.is_file() and quest_path.is_file() and pc_path.stat().st_size >= HEADER_SIZE and quest_path.stat().st_size >= HEADER_SIZE:
        print("skip water", x, y)
        return
    source = download_water(x, y)
    features = list(iter_geojsonseq(source))
    OUT_PC.mkdir(parents=True, exist_ok=True)
    OUT_QUEST.mkdir(parents=True, exist_ok=True)

    pc_v, pc_i, pc_p = build_mesh_records(features, x, y, PC_SIMPLIFY_M, PC_MAX_VERTICES)
    q_v, q_i, q_p = build_mesh_records(features, x, y, QUEST_SIMPLIFY_M, QUEST_MAX_VERTICES)
    pc_path.write_bytes(encode_gsw1(pc_v, pc_i))
    quest_path.write_bytes(encode_gsw1(q_v, q_i))
    print(
        f"GSW1_TILE_READY tile={x:02d}_{y:02d} features={len(features)} "
        f"pcPolys={pc_p} pcVertices={len(pc_v)} pcTriangles={len(pc_i)//3} pcBytes={pc_path.stat().st_size} "
        f"questPolys={q_p} questVertices={len(q_v)} questTriangles={len(q_i)//3} questBytes={quest_path.stat().st_size}"
    )


def verify(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE or data[:4] != b"GSW1" or data[4] != 1:
        raise ValueError(f"invalid GSW1 header: {path}")
    vc = struct.unpack_from("<H", data, 6)[0]
    ic = struct.unpack_from("<I", data, 8)[0]
    expected = HEADER_SIZE + vc * VERTEX_SIZE + ic * 2
    if len(data) != expected:
        raise ValueError(f"invalid GSW1 size: {path}: {len(data)} != {expected}")
    if ic % 3 != 0:
        raise ValueError(f"GSW1 index count not divisible by 3: {path}")
    return vc, ic // 3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Overture GIS water meshes for Kanto GST2 streaming.")
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"))
    select.add_argument("--row", type=int)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.tile is not None:
        tiles = [tuple(args.tile)]
    else:
        if args.row < 0 or args.row >= GRID_Y:
            raise SystemExit(f"row outside Kanto grid: {args.row}")
        tiles = [(x, args.row) for x in range(GRID_X)]
    for x, y in tiles:
        build_tile(int(x), int(y), overwrite=args.overwrite)
        for root in (OUT_PC, OUT_QUEST):
            vc, tc = verify(root / f"{x:02d}_{y:02d}.gsw1")
            print(f"GSW1_VERIFY path={root.name}/{x:02d}_{y:02d}.gsw1 vertices={vc} triangles={tc}")
    print(f"GSW1_RUN_DONE tiles={len(tiles)}")


if __name__ == "__main__":
    main()
