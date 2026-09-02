#!/usr/bin/env python3
from __future__ import annotations

import functools
from pathlib import Path

import build_dataset as base
import build_dem_region as region

ROOT = Path(__file__).resolve().parents[1]
REGION_ID = "kanto-lod3"

# MeshLOD3: one extremely coarse 1024 km square centered on Kanto.
# 129x129 => 8 km vertex spacing, enough for distant silhouettes while
# retaining roughly the same vertex count as one normal 4 km terrain tile.
CENTER_LAT = 36.075
CENTER_LON = 139.675
GRID_X = 1
GRID_Y = 1
TILE_SIZE_M = 1_024_000.0
ORIGIN_X_M = -512_000.0
ORIGIN_Z_M = -512_000.0
DEM_GRID = 129
SOURCE_ZOOM = 8


def configure() -> None:
    base.CENTER_LAT = CENTER_LAT
    base.CENTER_LON = CENTER_LON
    base.GRID_X = GRID_X
    base.GRID_Y = GRID_Y
    base.TILE_SIZE_M = TILE_SIZE_M
    base.WORLD_ORIGIN_X_M = ORIGIN_X_M
    base.WORLD_ORIGIN_Z_M = ORIGIN_Z_M
    base.DEM_GRID = DEM_GRID
    base.DEM_Z = SOURCE_ZOOM
    base.OUT_DEM = ROOT / "Server/data/regions" / REGION_ID / "dem"
    base.fetch_cached = region.regional_fetch_cached
    uncached = base.fetch_best_dem_tile
    base.fetch_best_dem_tile = functools.lru_cache(maxsize=128)(uncached)


def main() -> None:
    configure()
    region.write_manifest(REGION_ID)
    base.build_dem(0, 0, overwrite=True)

    out = base.OUT_DEM / "00_00.gst2"
    expected = 16 + DEM_GRID * DEM_GRID * 2
    if not out.is_file():
        raise SystemExit("LOD3 output missing")
    if out.stat().st_size != expected:
        raise SystemExit(f"LOD3 size mismatch: {out.stat().st_size} != {expected}")

    data = out.read_bytes()
    if data[:4] != b"GST2" or data[4] != 2:
        raise SystemExit("LOD3 GST2 header invalid")
    grid = int.from_bytes(data[6:8], "little")
    if grid != DEM_GRID:
        raise SystemExit(f"LOD3 grid mismatch: {grid}")

    print(
        "[GST2-LOD3] DATA_READY",
        f"coverage=1024kmx1024km",
        f"radiusBaseChunks=128",
        f"grid={DEM_GRID}x{DEM_GRID}",
        f"spacingMeters={TILE_SIZE_M / (DEM_GRID - 1):.0f}",
        f"sourceZ={SOURCE_ZOOM}",
        f"bytes={out.stat().st_size}",
    )


if __name__ == "__main__":
    main()
