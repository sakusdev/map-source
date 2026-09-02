#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import build_dataset as base
import build_dem_region as regional

ROOT = Path(__file__).resolve().parents[1]
FORMAT_MAGIC = b"GSV1"
FORMAT_VERSION = 1
HEADER_SIZE = 12
RECORD_SIZE = 10

# Runtime budgets. These are per 4 km tile; only nearby tiles should be live.
PC_MAX_TREES_PER_TILE = 1000
QUEST_MAX_TREES_PER_TILE = 240
PC_DENSITY_PER_KM2 = 180.0
QUEST_KEEP_RATIO = QUEST_MAX_TREES_PER_TILE / PC_MAX_TREES_PER_TILE

# Quaternius Textured LowPoly Trees representative runtime variants.
# 0..2 conifer, 3..5 generic broadleaf, 6..7 birch.
VARIANTS = (
    "Pine_4",
    "Pine_2",
    "Pine_5",
    "Tree_1",
    "Tree_7",
    "Tree_10",
    "Birch_2",
    "Birch_9",
)


def lonlat_to_local(lon: float, lat: float, z: int = 16) -> tuple[float, float]:
    center_x, center_y = base.lonlat_to_global_px(base.CENTER_LON, base.CENTER_LAT, z)
    px, py = base.lonlat_to_global_px(lon, lat, z)
    mpp = base.ground_resolution_m_per_px(base.CENTER_LAT, z)
    return (px - center_x) * mpp, (center_y - py) * mpp


def tile_lonlat_bbox(x: int, y: int) -> tuple[float, float, float, float]:
    left, top, right, bottom = base.tile_global_px_bounds(x, y, 16)
    west, north = regional.global_px_to_lonlat(left, top, 16)
    east, south = regional.global_px_to_lonlat(right, bottom, 16)
    return west, south, east, north


def iter_geojsonseq(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid GeoJSONSeq at line {line_no}: {exc}") from exc


def download_overture_land_cover(x: int, y: int, output: Path) -> None:
    bbox = tile_lonlat_bbox(x, y)
    bbox_text = ",".join(f"{v:.8f}" for v in bbox)
    cmd = [
        "overturemaps",
        "download",
        f"--bbox={bbox_text}",
        "-f",
        "geojsonseq",
        "--type=land_cover",
        "-o",
        str(output),
    ]
    print("running", " ".join(cmd))
    subprocess.run(cmd, check=True)


def polygon_rings_local(geometry: dict) -> list[list[list[tuple[float, float]]]]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return []

    raw_polygons: list = []
    if kind == "Polygon":
        raw_polygons = [coords]
    elif kind == "MultiPolygon":
        raw_polygons = coords
    else:
        return []

    result: list[list[list[tuple[float, float]]]] = []
    for raw_polygon in raw_polygons:
        if not isinstance(raw_polygon, list) or not raw_polygon:
            continue
        rings: list[list[tuple[float, float]]] = []
        for raw_ring in raw_polygon:
            if not isinstance(raw_ring, list) or len(raw_ring) < 3:
                continue
            ring: list[tuple[float, float]] = []
            for point in raw_ring:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                ring.append(lonlat_to_local(float(point[0]), float(point[1])))
            if len(ring) >= 3:
                rings.append(ring)
        if rings:
            result.append(rings)
    return result


def ring_area(ring: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, (x0, z0) in enumerate(ring):
        x1, z1 = ring[(i + 1) % len(ring)]
        total += x0 * z1 - x1 * z0
    return abs(total) * 0.5


def polygon_area(rings: list[list[tuple[float, float]]]) -> float:
    if not rings:
        return 0.0
    return max(0.0, ring_area(rings[0]) - sum(ring_area(r) for r in rings[1:]))


def point_in_ring(x: float, z: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, zi = ring[i]
        xj, zj = ring[j]
        crosses = (zi > z) != (zj > z)
        if crosses:
            denom = (zj - zi)
            if abs(denom) < 1e-12:
                denom = 1e-12
            x_at_z = (xj - xi) * (z - zi) / denom + xi
            if x < x_at_z:
                inside = not inside
        j = i
    return inside


def point_in_polygon(x: float, z: float, rings: list[list[tuple[float, float]]]) -> bool:
    if not rings or not point_in_ring(x, z, rings[0]):
        return False
    return not any(point_in_ring(x, z, hole) for hole in rings[1:])


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


def sample_dem(dem: tuple[int, int, int, tuple[int, ...]], fx01: float, fz01: float) -> float:
    grid, offset_mm, scale_mm, samples = dem
    fx = max(0.0, min(1.0, fx01)) * (grid - 1)
    fz = max(0.0, min(1.0, fz01)) * (grid - 1)
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
    return (h00 * (1.0 - tx) + h10 * tx) * (1.0 - tz) + (h01 * (1.0 - tx) + h11 * tx) * tz


def stable_seed(*parts: object) -> int:
    text = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "little")


def variant_for(rng: random.Random, elevation_m: float) -> int:
    # No species classification is claimed here. This is only a visual mixture
    # inside GIS-derived forest footprints. Higher elevations bias conifer.
    roll = rng.random()
    if elevation_m >= 900.0:
        if roll < 0.68:
            return rng.randrange(0, 3)
        if roll < 0.90:
            return rng.randrange(3, 6)
        return rng.randrange(6, 8)
    if roll < 0.24:
        return rng.randrange(0, 3)
    if roll < 0.88:
        return rng.randrange(3, 6)
    return rng.randrange(6, 8)


def make_record(
    tile_x: int,
    tile_y: int,
    world_x: float,
    world_z: float,
    elevation_m: float,
    rng: random.Random,
) -> dict:
    west, south, _, _ = base.tile_local_bounds(tile_x, tile_y)
    return {
        "x": world_x - west,
        "z": world_z - south,
        "y": elevation_m,
        "variant": variant_for(rng, elevation_m),
        "scale": rng.uniform(0.82, 1.38),
        "yaw": rng.random() * 360.0,
        "rank": rng.random(),
    }


def sample_forest_records(tile_x: int, tile_y: int, features: Iterable[dict], dem) -> tuple[list[dict], int, float]:
    west, south, east, north = base.tile_local_bounds(tile_x, tile_y)
    records: list[dict] = []
    forest_features = 0
    forest_area_m2 = 0.0

    for feature_index, feature in enumerate(features):
        props = feature.get("properties") or {}
        subtype = feature.get("subtype") or props.get("subtype")
        if subtype != "forest":
            continue
        polygons = polygon_rings_local(feature.get("geometry") or {})
        if not polygons:
            continue
        forest_features += 1
        feature_id = feature.get("id") or props.get("id") or feature_index

        for polygon_index, rings in enumerate(polygons):
            area_m2 = polygon_area(rings)
            if area_m2 < 40.0:
                continue
            forest_area_m2 += area_m2
            outer = rings[0]
            min_x = max(west, min(p[0] for p in outer))
            max_x = min(east, max(p[0] for p in outer))
            min_z = max(south, min(p[1] for p in outer))
            max_z = min(north, max(p[1] for p in outer))
            if max_x <= min_x or max_z <= min_z:
                continue

            desired = max(1, int(round(area_m2 / 1_000_000.0 * PC_DENSITY_PER_KM2)))
            # Avoid pathological huge polygons dominating CPU before the tile cap.
            desired = min(desired, PC_MAX_TREES_PER_TILE * 3)
            rng = random.Random(stable_seed("forest", tile_x, tile_y, feature_id, polygon_index))
            accepted = 0
            attempts = 0
            max_attempts = max(64, desired * 20)
            while accepted < desired and attempts < max_attempts:
                attempts += 1
                world_x = rng.uniform(min_x, max_x)
                world_z = rng.uniform(min_z, max_z)
                if not point_in_polygon(world_x, world_z, rings):
                    continue
                fx = (world_x - west) / base.TILE_SIZE_M
                fz = (world_z - south) / base.TILE_SIZE_M
                elevation_m = sample_dem(dem, fx, fz)
                records.append(make_record(tile_x, tile_y, world_x, world_z, elevation_m, rng))
                accepted += 1

    # Stable ranking avoids geographic-order bias when the tile cap is reached.
    records.sort(key=lambda r: r["rank"])
    if len(records) > PC_MAX_TREES_PER_TILE:
        records = records[:PC_MAX_TREES_PER_TILE]
    return records, forest_features, forest_area_m2


def encode_records(records: list[dict]) -> bytes:
    if not records:
        return FORMAT_MAGIC + bytes((FORMAT_VERSION, 0)) + struct.pack("<Hi", 0, 0)

    base_dm = min(int(round(r["y"] * 10.0)) for r in records)
    payload = bytearray()
    for r in records:
        x_dm = max(0, min(65535, int(round(r["x"] * 10.0))))
        z_dm = max(0, min(65535, int(round(r["z"] * 10.0))))
        y_delta_dm = max(0, min(65535, int(round(r["y"] * 10.0)) - base_dm))
        variant = max(0, min(255, int(r["variant"])))
        # 0..255 maps to scale 0.5..2.0 in runtime.
        scale_q = max(0, min(255, int(round((r["scale"] - 0.5) / 1.5 * 255.0))))
        yaw_q = max(0, min(255, int(round((r["yaw"] % 360.0) / 360.0 * 255.0))))
        flags = 0
        payload += struct.pack("<HHHBBBB", x_dm, z_dm, y_delta_dm, variant, scale_q, yaw_q, flags)

    return FORMAT_MAGIC + bytes((FORMAT_VERSION, 0)) + struct.pack("<Hi", len(records), base_dm) + payload


def validate_payload(data: bytes) -> int:
    if len(data) < HEADER_SIZE or data[:4] != FORMAT_MAGIC or data[4] != FORMAT_VERSION:
        raise ValueError("invalid GSV1 header")
    count = struct.unpack_from("<H", data, 6)[0]
    expected = HEADER_SIZE + count * RECORD_SIZE
    if len(data) != expected:
        raise ValueError(f"invalid GSV1 size count={count} bytes={len(data)} expected={expected}")
    return count


def output_roots(region: str) -> tuple[Path, Path, Path]:
    root = ROOT / "Server/data/regions" / region / "vegetation"
    return root, root / "pc", root / "quest"


def write_manifest(region: str) -> None:
    root, _, _ = output_roots(region)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "GSV1_REGION",
        "version": 1,
        "region": region,
        "tileSizeMeters": base.TILE_SIZE_M,
        "grid": {"x": base.GRID_X, "y": base.GRID_Y},
        "source": {
            "provider": "Overture Maps Foundation",
            "theme": "base",
            "type": "land_cover",
            "filter": {"subtype": "forest"},
            "upstream": "ESA WorldCover",
        },
        "placement": {
            "deterministic": True,
            "speciesClassification": False,
            "pcMaxPerTile": PC_MAX_TREES_PER_TILE,
            "questMaxPerTile": QUEST_MAX_TREES_PER_TILE,
            "pcDensityPerKm2": PC_DENSITY_PER_KM2,
        },
        "assets": {
            "license": "CC0-1.0",
            "author": "Quaternius",
            "pack": "Textured LowPoly Trees",
            "variants": list(VARIANTS),
        },
        "record": {
            "bytes": RECORD_SIZE,
            "fields": ["x_dm:u16", "z_dm:u16", "y_delta_dm:u16", "variant:u8", "scale_q:u8", "yaw_q:u8", "flags:u8"],
            "scaleDecode": "0.5 + scale_q/255*1.5",
            "yawDecodeDegrees": "yaw_q/255*360",
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_tile(region: str, tile_x: int, tile_y: int, overwrite: bool = False) -> None:
    base.validate_tile(tile_x, tile_y)
    root, out_pc, out_quest = output_roots(region)
    out_pc.mkdir(parents=True, exist_ok=True)
    out_quest.mkdir(parents=True, exist_ok=True)
    stem = f"{tile_x:02d}_{tile_y:02d}"
    pc_path = out_pc / f"{stem}.gsv1"
    quest_path = out_quest / f"{stem}.gsv1"
    if not overwrite and pc_path.is_file() and quest_path.is_file() and pc_path.stat().st_size > 0 and quest_path.stat().st_size > 0:
        print("skip vegetation", stem)
        return

    dem_path = ROOT / "Server/data/regions" / region / "dem" / f"{stem}.gst2"
    if not dem_path.is_file():
        raise SystemExit(f"missing DEM for vegetation tile: {dem_path.relative_to(ROOT)}")
    dem = decode_gst2(dem_path)

    with tempfile.TemporaryDirectory(prefix=f"gsv1-{stem}-") as td:
        source = Path(td) / "land_cover.geojsonseq"
        download_overture_land_cover(tile_x, tile_y, source)
        records, forest_features, forest_area_m2 = sample_forest_records(tile_x, tile_y, iter_geojsonseq(source), dem)

    pc_records = records[:PC_MAX_TREES_PER_TILE]
    quest_count = min(QUEST_MAX_TREES_PER_TILE, int(round(len(pc_records) * QUEST_KEEP_RATIO)))
    # Every Nth ranked record keeps Quest spatially deterministic without a second GIS pass.
    if quest_count <= 0:
        quest_records: list[dict] = []
    elif quest_count >= len(pc_records):
        quest_records = pc_records
    else:
        stride = len(pc_records) / quest_count
        quest_records = [pc_records[min(len(pc_records) - 1, int(i * stride))] for i in range(quest_count)]

    pc_payload = encode_records(pc_records)
    quest_payload = encode_records(quest_records)
    validate_payload(pc_payload)
    validate_payload(quest_payload)
    pc_path.write_bytes(pc_payload)
    quest_path.write_bytes(quest_payload)
    print(
        "GSV1_TILE_READY",
        f"tile={stem}",
        f"forestFeatures={forest_features}",
        f"forestKm2={forest_area_m2 / 1_000_000.0:.3f}",
        f"pc={len(pc_records)}",
        f"quest={len(quest_records)}",
        f"pcBytes={len(pc_payload)}",
        f"questBytes={len(quest_payload)}",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate deterministic GIS-derived forest vegetation tiles (GSV1).")
    p.add_argument("--region", choices=sorted(regional.REGIONS), default="kanto")
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"))
    select.add_argument("--rows", nargs=2, type=int, metavar=("START", "END"))
    select.add_argument("--all", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    regional.apply_region(args.region)
    write_manifest(args.region)

    if args.tile is not None:
        tiles = [tuple(args.tile)]
    elif args.rows is not None:
        start, end = args.rows
        if start < 0 or end < start or end >= base.GRID_Y:
            raise SystemExit(f"rows outside region: {start}..{end}")
        tiles = [(x, y) for y in range(start, end + 1) for x in range(base.GRID_X)]
    else:
        tiles = [(x, y) for y in range(base.GRID_Y) for x in range(base.GRID_X)]

    print(
        "GSV1_BEGIN",
        f"region={args.region}",
        f"grid={base.GRID_X}x{base.GRID_Y}",
        f"tiles={len(tiles)}",
        "source=overture:base/land_cover:forest",
    )
    for i, (x, y) in enumerate(tiles, 1):
        print(f"=== vegetation [{i}/{len(tiles)}] {x:02d}_{y:02d} ===")
        build_tile(args.region, x, y, args.overwrite)
    print("GSV1_DONE", f"region={args.region}", f"tiles={len(tiles)}")


if __name__ == "__main__":
    main()
