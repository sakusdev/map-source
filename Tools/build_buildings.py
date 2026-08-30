#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

CENTER_LAT = 35.3606
CENTER_LON = 138.7274
GRID_X = 15
GRID_Y = 14
TILE_SIZE_M = 4000.0
WORLD_ORIGIN_X_M = -30000.0
WORLD_ORIGIN_Z_M = -27500.0
WEB_MERCATOR_Z = 16

PC_MAX_BUILDINGS_PER_TILE = int(os.environ.get("GSTB_PC_MAX", "1800"))
QUEST_MAX_BUILDINGS_PER_TILE = int(os.environ.get("GSTB_QUEST_MAX", "450"))
DEFAULT_HEIGHT_M = 6.0
FLOOR_HEIGHT_M = 3.0
MIN_BUILDING_SIZE_M = 1.0
MAX_BUILDING_HEIGHT_M = 400.0

ROOT = Path(__file__).resolve().parents[1]
OUT_PC = ROOT / "Server/data/buildings/pc"
OUT_QUEST = ROOT / "Server/data/buildings/quest"
DEM_DIR = ROOT / "Server/data/dem"

HEADER_SIZE = 12
RECORD_SIZE = 12


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


def local_to_lonlat(x_m: float, z_m: float) -> tuple[float, float]:
    px = _CENTER_PX[0] + x_m / _MPP
    py = _CENTER_PX[1] - z_m / _MPP
    return global_px_to_lonlat(px, py)


def lonlat_to_local(lon: float, lat: float) -> tuple[float, float]:
    px, py = lonlat_to_global_px(lon, lat)
    x_m = (px - _CENTER_PX[0]) * _MPP
    z_m = (_CENTER_PX[1] - py) * _MPP
    return x_m, z_m


def tile_local_bounds(x: int, y: int) -> tuple[float, float, float, float]:
    west = WORLD_ORIGIN_X_M + x * TILE_SIZE_M
    south = WORLD_ORIGIN_Z_M + y * TILE_SIZE_M
    return west, south, west + TILE_SIZE_M, south + TILE_SIZE_M


def local_bounds_to_lonlat_bbox(bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    west_m, south_m, east_m, north_m = bounds
    west_lon, south_lat = local_to_lonlat(west_m, south_m)
    east_lon, north_lat = local_to_lonlat(east_m, north_m)
    return west_lon, south_lat, east_lon, north_lat


def world_lonlat_bbox() -> tuple[float, float, float, float]:
    return local_bounds_to_lonlat_bbox((
        WORLD_ORIGIN_X_M,
        WORLD_ORIGIN_Z_M,
        WORLD_ORIGIN_X_M + GRID_X * TILE_SIZE_M,
        WORLD_ORIGIN_Z_M + GRID_Y * TILE_SIZE_M,
    ))


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float] | None:
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if coords is None:
        return None
    min_lon = 999.0
    min_lat = 999.0
    max_lon = -999.0
    max_lat = -999.0
    found = False

    def walk(value) -> None:
        nonlocal min_lon, min_lat, max_lon, max_lat, found
        if not isinstance(value, list):
            return
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            lon = float(value[0])
            lat = float(value[1])
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)
            found = True
            return
        for item in value:
            walk(item)

    walk(coords)
    if not found:
        return None
    return min_lon, min_lat, max_lon, max_lat


def feature_bounds(feature: dict) -> tuple[float, float, float, float] | None:
    raw = feature.get("bbox")
    if isinstance(raw, list) and len(raw) >= 4:
        return float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
    if isinstance(raw, dict):
        keys = ("xmin", "ymin", "xmax", "ymax")
        if all(k in raw for k in keys):
            return tuple(float(raw[k]) for k in keys)  # type: ignore[return-value]
    props = feature.get("properties") or {}
    raw = props.get("bbox")
    if isinstance(raw, dict):
        keys = ("xmin", "ymin", "xmax", "ymax")
        if all(k in raw for k in keys):
            return tuple(float(raw[k]) for k in keys)  # type: ignore[return-value]
    return geometry_bounds(feature.get("geometry") or {})


def parse_height(props: dict) -> float:
    raw = props.get("height")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return min(float(raw), MAX_BUILDING_HEIGHT_M)
    floors = props.get("num_floors")
    if isinstance(floors, (int, float)) and float(floors) > 0:
        return min(max(float(floors) * FLOOR_HEIGHT_M, 2.5), MAX_BUILDING_HEIGHT_M)
    return DEFAULT_HEIGHT_M


def decode_gst2(path: Path) -> tuple[int, int, int, tuple[int, ...]]:
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"GST2" or data[4] != 2:
        raise ValueError(f"invalid GST2: {path}")
    grid = struct.unpack_from("<H", data, 6)[0]
    offset_mm, scale_mm = struct.unpack_from("<ii", data, 8)
    expected = 16 + grid * grid * 2
    if len(data) != expected:
        raise ValueError(f"invalid GST2 size: {path}: {len(data)} != {expected}")
    samples = struct.unpack_from("<" + "H" * (grid * grid), data, 16)
    return grid, offset_mm, scale_mm, samples


_dem_cache: dict[tuple[int, int], tuple[int, int, int, tuple[int, ...]]] = {}


def sample_dem(tile_x: int, tile_y: int, local_x: float, local_z: float) -> float:
    key = (tile_x, tile_y)
    if key not in _dem_cache:
        path = DEM_DIR / f"{tile_x:02d}_{tile_y:02d}.gst2"
        if not path.is_file():
            return 0.0
        _dem_cache[key] = decode_gst2(path)
    grid, offset_mm, scale_mm, samples = _dem_cache[key]
    west, south, _, _ = tile_local_bounds(tile_x, tile_y)
    fx = max(0.0, min(1.0, (local_x - west) / TILE_SIZE_M)) * (grid - 1)
    fz = max(0.0, min(1.0, (local_z - south) / TILE_SIZE_M)) * (grid - 1)
    x0 = int(math.floor(fx))
    z0 = int(math.floor(fz))
    x1 = min(x0 + 1, grid - 1)
    z1 = min(z0 + 1, grid - 1)
    tx = fx - x0
    tz = fz - z0

    def h(ix: int, iz: int) -> float:
        sample = samples[iz * grid + ix]
        return (offset_mm + sample * scale_mm) * 0.001

    h00 = h(x0, z0)
    h10 = h(x1, z0)
    h01 = h(x0, z1)
    h11 = h(x1, z1)
    return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz


def download_overture(bbox: tuple[float, float, float, float], output: Path) -> None:
    bbox_text = ",".join(f"{v:.8f}" for v in bbox)
    cmd = [
        "overturemaps", "download",
        f"--bbox={bbox_text}",
        "-f", "geojsonseq",
        "--type=building",
        "-o", str(output),
    ]
    print("running", " ".join(cmd))
    subprocess.run(cmd, check=True)


def iter_geojsonseq(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid GeoJSONSeq at line {line_no}: {exc}") from exc


def tile_for_local(x_m: float, z_m: float) -> tuple[int, int] | None:
    x = math.floor((x_m - WORLD_ORIGIN_X_M) / TILE_SIZE_M)
    y = math.floor((z_m - WORLD_ORIGIN_Z_M) / TILE_SIZE_M)
    if x < 0 or x >= GRID_X or y < 0 or y >= GRID_Y:
        return None
    return int(x), int(y)


def encode_tile(records: list[dict], limit: int) -> bytes:
    selected = sorted(records, key=lambda r: (r["area"], r["height"]), reverse=True)[:limit]
    if not selected:
        return b"GSB1" + bytes((1, 0)) + struct.pack("<Hi", 0, 0)

    min_base_dm = min(int(round(r["base"] * 10.0)) for r in selected)
    payload = bytearray()
    for r in selected:
        cx_dm = max(0, min(65535, int(round(r["cx"] * 10.0))))
        cz_dm = max(0, min(65535, int(round(r["cz"] * 10.0))))
        sx_dm = max(1, min(65535, int(round(r["sx"] * 10.0))))
        sz_dm = max(1, min(65535, int(round(r["sz"] * 10.0))))
        base_dm = int(round(r["base"] * 10.0))
        base_delta_dm = max(0, min(65535, base_dm - min_base_dm))
        height_dm = max(1, min(65535, int(round(r["height"] * 10.0))))
        payload += struct.pack("<HHHHHH", cx_dm, cz_dm, sx_dm, sz_dm, base_delta_dm, height_dm)

    header = b"GSB1" + bytes((1, 0)) + struct.pack("<Hi", len(selected), min_base_dm)
    return header + payload


def build_from_geojson(path: Path, selected_tiles: set[tuple[int, int]] | None = None, overwrite: bool = False) -> None:
    per_tile: dict[tuple[int, int], list[dict]] = {(x, y): [] for y in range(GRID_Y) for x in range(GRID_X)}
    accepted = 0
    skipped = 0

    for feature in iter_geojsonseq(path):
        props = feature.get("properties") or {}
        if props.get("is_underground") is True:
            skipped += 1
            continue
        bounds = feature_bounds(feature)
        if bounds is None:
            skipped += 1
            continue
        min_lon, min_lat, max_lon, max_lat = bounds
        west_m, south_m = lonlat_to_local(min_lon, min_lat)
        east_m, north_m = lonlat_to_local(max_lon, max_lat)
        if east_m < west_m:
            west_m, east_m = east_m, west_m
        if north_m < south_m:
            south_m, north_m = north_m, south_m
        cx_world = (west_m + east_m) * 0.5
        cz_world = (south_m + north_m) * 0.5
        tile = tile_for_local(cx_world, cz_world)
        if tile is None or (selected_tiles is not None and tile not in selected_tiles):
            continue
        tile_x, tile_y = tile
        tile_west, tile_south, _, _ = tile_local_bounds(tile_x, tile_y)
        sx = max(MIN_BUILDING_SIZE_M, east_m - west_m)
        sz = max(MIN_BUILDING_SIZE_M, north_m - south_m)
        # Discard pathological bounding boxes; v1 intentionally represents one building as one box.
        if sx > 600.0 or sz > 600.0:
            skipped += 1
            continue
        base = sample_dem(tile_x, tile_y, cx_world, cz_world)
        height = parse_height(props)
        per_tile[tile].append({
            "cx": cx_world - tile_west,
            "cz": cz_world - tile_south,
            "sx": sx,
            "sz": sz,
            "base": base,
            "height": height,
            "area": sx * sz,
        })
        accepted += 1

    OUT_PC.mkdir(parents=True, exist_ok=True)
    OUT_QUEST.mkdir(parents=True, exist_ok=True)
    tiles = selected_tiles if selected_tiles is not None else set(per_tile.keys())
    for x, y in sorted(tiles, key=lambda p: (p[1], p[0])):
        stem = f"{x:02d}_{y:02d}"
        pc_path = OUT_PC / f"{stem}.gsb1"
        quest_path = OUT_QUEST / f"{stem}.gsb1"
        if not overwrite and pc_path.is_file() and quest_path.is_file() and pc_path.stat().st_size > 0 and quest_path.stat().st_size > 0:
            print("skip buildings", stem)
            continue
        records = per_tile.get((x, y), [])
        pc_payload = encode_tile(records, PC_MAX_BUILDINGS_PER_TILE)
        quest_payload = encode_tile(records, QUEST_MAX_BUILDINGS_PER_TILE)
        pc_path.write_bytes(pc_payload)
        quest_path.write_bytes(quest_payload)
        pc_count = struct.unpack_from("<H", pc_payload, 6)[0]
        quest_count = struct.unpack_from("<H", quest_payload, 6)[0]
        print("wrote buildings", stem, "source", len(records), "pc", pc_count, len(pc_payload), "quest", quest_count, len(quest_payload))

    print("features accepted", accepted, "skipped", skipped)


def verify_outputs(tiles: Iterable[tuple[int, int]]) -> None:
    for x, y in tiles:
        stem = f"{x:02d}_{y:02d}"
        for root in (OUT_PC, OUT_QUEST):
            path = root / f"{stem}.gsb1"
            data = path.read_bytes()
            if len(data) < HEADER_SIZE or data[:4] != b"GSB1" or data[4] != 1:
                raise RuntimeError(f"invalid GSB1 header: {path}")
            count = struct.unpack_from("<H", data, 6)[0]
            expected = HEADER_SIZE + count * RECORD_SIZE
            if len(data) != expected:
                raise RuntimeError(f"invalid GSB1 size: {path}: count={count} bytes={len(data)} expected={expected}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build compact Overture building tiles for GST2 streaming.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="build all 15x14 tiles")
    selection.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"), help="build one tile")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--input", type=Path, help="reuse an existing Overture GeoJSONSeq file instead of downloading")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.all:
        tiles = {(x, y) for y in range(GRID_Y) for x in range(GRID_X)}
        bbox = world_lonlat_bbox()
    else:
        x, y = args.tile
        if x < 0 or x >= GRID_X or y < 0 or y >= GRID_Y:
            raise SystemExit(f"tile outside grid: {x},{y}")
        tiles = {(x, y)}
        bbox = local_bounds_to_lonlat_bbox(tile_local_bounds(x, y))

    if args.input is not None:
        source = args.input
        if not source.is_file():
            raise SystemExit(f"input not found: {source}")
        build_from_geojson(source, tiles, args.overwrite)
    else:
        with tempfile.TemporaryDirectory(prefix="gstb-overture-") as td:
            source = Path(td) / "buildings.geojsonseq"
            download_overture(bbox, source)
            build_from_geojson(source, tiles, args.overwrite)

    verify_outputs(tiles)
    print("complete building tiles", len(tiles), "bbox", ",".join(f"{v:.7f}" for v in bbox))


if __name__ == "__main__":
    main()
