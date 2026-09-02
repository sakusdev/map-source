#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

import build_buildings as buildings

# Kanto 240 x 252 km region. Keep the legacy Fuji generator intact and configure
# its proven GSB1 encoder/DEM sampler dynamically for this region.
CENTER_LAT = 36.075
CENTER_LON = 139.675
GRID_X = 60
GRID_Y = 63
TILE_SIZE_M = 4000.0
WORLD_ORIGIN_X_M = -120000.0
WORLD_ORIGIN_Z_M = -126000.0
ROOT = Path(__file__).resolve().parents[1]


def configure_kanto() -> None:
    buildings.CENTER_LAT = CENTER_LAT
    buildings.CENTER_LON = CENTER_LON
    buildings.GRID_X = GRID_X
    buildings.GRID_Y = GRID_Y
    buildings.TILE_SIZE_M = TILE_SIZE_M
    buildings.WORLD_ORIGIN_X_M = WORLD_ORIGIN_X_M
    buildings.WORLD_ORIGIN_Z_M = WORLD_ORIGIN_Z_M
    buildings.PC_MAX_BUILDINGS_PER_TILE = 1200
    buildings.QUEST_MAX_BUILDINGS_PER_TILE = 300
    buildings.OUT_PC = ROOT / "Server/data/regions/kanto/buildings/pc"
    buildings.OUT_QUEST = ROOT / "Server/data/regions/kanto/buildings/quest"
    buildings.DEM_DIR = ROOT / "Server/data/regions/kanto/dem"
    buildings._CENTER_PX = buildings.lonlat_to_global_px(CENTER_LON, CENTER_LAT)
    buildings._MPP = buildings.ground_resolution_m_per_px(CENTER_LAT)
    buildings._dem_cache.clear()


def valid_gsb1(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < buildings.HEADER_SIZE:
        return False
    try:
        data = path.read_bytes()
        if data[:4] != b"GSB1" or data[4] != 1:
            return False
        count = int.from_bytes(data[6:8], "little")
        return len(data) == buildings.HEADER_SIZE + count * buildings.RECORD_SIZE
    except Exception:
        return False


def tile_complete(x: int, y: int) -> bool:
    stem = f"{x:02d}_{y:02d}.gsb1"
    return valid_gsb1(buildings.OUT_PC / stem) and valid_gsb1(buildings.OUT_QUEST / stem)


def chunk_tiles(x0: int, y0: int, width: int, height: int) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(y0, min(GRID_Y, y0 + height))
        for x in range(x0, min(GRID_X, x0 + width))
    }


def chunk_bbox(tiles: set[tuple[int, int]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in tiles]
    ys = [p[1] for p in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    local = (
        WORLD_ORIGIN_X_M + min_x * TILE_SIZE_M,
        WORLD_ORIGIN_Z_M + min_y * TILE_SIZE_M,
        WORLD_ORIGIN_X_M + (max_x + 1) * TILE_SIZE_M,
        WORLD_ORIGIN_Z_M + (max_y + 1) * TILE_SIZE_M,
    )
    return buildings.local_bounds_to_lonlat_bbox(local)


def all_chunks(width: int, height: int) -> list[tuple[int, int, set[tuple[int, int]]]]:
    result: list[tuple[int, int, set[tuple[int, int]]]] = []
    for y0 in range(0, GRID_Y, height):
        for x0 in range(0, GRID_X, width):
            result.append((x0, y0, chunk_tiles(x0, y0, width, height)))
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build deterministic shards of Kanto Overture GSB1 building tiles.")
    p.add_argument("--shard-count", type=int, default=1)
    p.add_argument("--shard-index", type=int, default=0)
    p.add_argument("--chunk-width", type=int, default=4)
    p.add_argument("--chunk-height", type=int, default=4)
    p.add_argument("--max-chunks", type=int, default=4)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    configure_kanto()

    if args.shard_count < 1:
        raise SystemExit("shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("shard-index outside shard-count")
    if args.chunk_width < 1 or args.chunk_height < 1:
        raise SystemExit("chunk dimensions must be >= 1")
    if args.max_chunks < 1:
        raise SystemExit("max-chunks must be >= 1")

    chunks = all_chunks(args.chunk_width, args.chunk_height)
    owned = [chunk for ordinal, chunk in enumerate(chunks) if ordinal % args.shard_count == args.shard_index]
    missing = [chunk for chunk in owned if args.overwrite or any(not tile_complete(x, y) for x, y in chunk[2])]
    selected = missing[: args.max_chunks]

    print(
        f"GSB1_KANTO_BEGIN shard={args.shard_index}/{args.shard_count} "
        f"ownedChunks={len(owned)} missingChunks={len(missing)} selectedChunks={len(selected)}"
    )

    built_tiles = 0
    failed: list[str] = []
    for item, (x0, y0, tiles) in enumerate(selected, 1):
        bbox = chunk_bbox(tiles)
        ok = False
        last_error: Exception | None = None
        for attempt in range(1, max(1, args.attempts) + 1):
            try:
                print(
                    f"GSB1_KANTO_CHUNK shard={args.shard_index}/{args.shard_count} "
                    f"origin={x0:02d}_{y0:02d} tiles={len(tiles)} item={item}/{len(selected)} attempt={attempt}"
                )
                with tempfile.TemporaryDirectory(prefix="gstb-kanto-") as td:
                    source = Path(td) / "buildings.geojsonseq"
                    buildings.download_overture(bbox, source)
                    buildings.build_from_geojson(source, tiles, overwrite=args.overwrite)
                    buildings.verify_outputs(tiles)
                ok = True
                break
            except (Exception, subprocess.SubprocessError) as exc:
                last_error = exc
                if attempt < max(1, args.attempts):
                    time.sleep(min(30, attempt * 6))
        if not ok:
            failed.append(f"{x0:02d}_{y0:02d}:{last_error!r}")
            continue
        built_tiles += len(tiles)

    remaining = len(missing) - len(selected) + len(failed)
    print(
        f"GSB1_KANTO_DONE shard={args.shard_index}/{args.shard_count} "
        f"builtTiles={built_tiles} remainingChunks={max(0, remaining)} failures={len(failed)}"
    )
    if failed:
        print("GSB1_KANTO_FAILURES", " | ".join(failed[:20]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
