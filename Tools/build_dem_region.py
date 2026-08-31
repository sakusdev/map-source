#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

import build_dataset as base

ROOT = Path(__file__).resolve().parents[1]

REGIONS = {
    "kanto": {
        "center_lat": 36.075,
        "center_lon": 139.675,
        "grid_x": 60,
        "grid_y": 63,
        "tile_size_m": 4000.0,
        "origin_x_m": -120000.0,
        "origin_z_m": -126000.0,
        "dem_grid": 129,
        "dem_z": 14,
    },
}

TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def regional_fetch_cached(url: str, path: Path, timeout: int = 45) -> bytes:
    """Fetch GSI data with bounded but resilient retries for regional builds."""
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()

    timeout = float(os.environ.get("GSI_HTTP_TIMEOUT", "20"))
    attempts = max(1, int(os.environ.get("GSI_HTTP_ATTEMPTS", "6")))
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": base.USER_AGENT})
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            if base.REQUEST_DELAY_SECONDS > 0:
                time.sleep(base.REQUEST_DELAY_SECONDS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
            if not data:
                raise IOError("empty response")
            path.write_bytes(data)
            return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise
            if exc.code not in TRANSIENT_HTTP_CODES:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt + 1 < attempts:
            retry_after = 0.0
            if isinstance(last_error, urllib.error.HTTPError):
                raw_retry_after = last_error.headers.get("Retry-After")
                if raw_retry_after:
                    try:
                        retry_after = max(0.0, float(raw_retry_after))
                    except ValueError:
                        retry_after = 0.0

            backoff = min(20.0, 1.5 * (2 ** attempt))
            delay = max(retry_after, backoff) + random.uniform(0.0, 0.75)
            print(
                "GSI transient fetch error",
                repr(last_error),
                f"attempt={attempt + 1}/{attempts}",
                f"retry_in={delay:.2f}s",
                url,
            )
            time.sleep(delay)

    assert last_error is not None
    raise last_error


def global_px_to_lonlat(px: float, py: float, z: int) -> tuple[float, float]:
    n = 256.0 * (1 << z)
    lon = px / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * py / n))))
    return lon, lat


def apply_region(region_id: str) -> dict:
    cfg = REGIONS[region_id]
    base.CENTER_LAT = float(cfg["center_lat"])
    base.CENTER_LON = float(cfg["center_lon"])
    base.GRID_X = int(cfg["grid_x"])
    base.GRID_Y = int(cfg["grid_y"])
    base.TILE_SIZE_M = float(cfg["tile_size_m"])
    base.WORLD_ORIGIN_X_M = float(cfg["origin_x_m"])
    base.WORLD_ORIGIN_Z_M = float(cfg["origin_z_m"])
    base.DEM_GRID = int(cfg["dem_grid"])
    base.DEM_Z = int(cfg["dem_z"])
    base.OUT_DEM = ROOT / "Server/data/regions" / region_id / "dem"

    # Kanto generation may touch thousands of source tiles. Retry transient GSI
    # failures aggressively enough that a single 5xx does not kill a multi-hour
    # shard, while still keeping every individual request bounded.
    base.fetch_cached = regional_fetch_cached

    # Adjacent 4 km supertiles reuse most Z14 DEM source tiles. Keep only a
    # local decoded neighborhood so a multi-row shard stays fast without
    # retaining the whole Kanto source mosaic in RAM.
    uncached = base.fetch_best_dem_tile
    base.fetch_best_dem_tile = functools.lru_cache(maxsize=48)(uncached)
    return cfg


def coverage_lonlat() -> dict[str, float]:
    z = base.DEM_Z
    cx, cy = base.lonlat_to_global_px(base.CENTER_LON, base.CENTER_LAT, z)
    mpp = base.ground_resolution_m_per_px(base.CENTER_LAT, z)
    west_m = base.WORLD_ORIGIN_X_M
    east_m = west_m + base.GRID_X * base.TILE_SIZE_M
    south_m = base.WORLD_ORIGIN_Z_M
    north_m = south_m + base.GRID_Y * base.TILE_SIZE_M

    left = cx + west_m / mpp
    right = cx + east_m / mpp
    top = cy - north_m / mpp
    bottom = cy - south_m / mpp
    west, north = global_px_to_lonlat(left, top, z)
    east, south = global_px_to_lonlat(right, bottom, z)
    return {"west": west, "south": south, "east": east, "north": north}


def write_manifest(region_id: str) -> Path:
    out_dir = ROOT / "Server/data/regions" / region_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "GST2_REGION",
        "version": 1,
        "region": region_id,
        "center": {"lat": base.CENTER_LAT, "lon": base.CENTER_LON},
        "coverage": coverage_lonlat(),
        "grid": {"x": base.GRID_X, "y": base.GRID_Y},
        "tileSizeMeters": base.TILE_SIZE_M,
        "originMeters": {"x": base.WORLD_ORIGIN_X_M, "z": base.WORLD_ORIGIN_Z_M},
        "dem": {
            "format": "GST2",
            "version": 2,
            "grid": base.DEM_GRID,
            "sourceZoom": base.DEM_Z,
            "urlPattern": "dem/{x:02d}_{y:02d}.gst2",
            "sourcePriority": list(base.DEM_DATASETS),
        },
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("manifest", path.relative_to(ROOT), json.dumps(manifest["coverage"]))
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build DEM-only GST2 region datasets.")
    p.add_argument("--region", choices=sorted(REGIONS), default="kanto")
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument("--all", action="store_true")
    select.add_argument("--row", type=int)
    select.add_argument("--rows", nargs=2, type=int, metavar=("START", "END"))
    select.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"))
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    apply_region(args.region)
    write_manifest(args.region)

    if args.all:
        rows = range(base.GRID_Y)
        tiles = [(x, y) for y in rows for x in range(base.GRID_X)]
    elif args.rows is not None:
        start, end = args.rows
        if start < 0 or end < start or end >= base.GRID_Y:
            raise SystemExit(f"rows outside region: {start}..{end}; valid 0..{base.GRID_Y - 1}")
        tiles = [(x, y) for y in range(start, end + 1) for x in range(base.GRID_X)]
    elif args.row is not None:
        if args.row < 0 or args.row >= base.GRID_Y:
            raise SystemExit(f"row outside region: {args.row}; valid 0..{base.GRID_Y - 1}")
        tiles = [(x, args.row) for x in range(base.GRID_X)]
    else:
        x, y = args.tile
        base.validate_tile(x, y)
        tiles = [(x, y)]

    print(
        "region",
        args.region,
        f"center={base.CENTER_LAT},{base.CENTER_LON}",
        f"grid={base.GRID_X}x{base.GRID_Y}",
        f"tile={base.TILE_SIZE_M}m",
        f"origin=({base.WORLD_ORIGIN_X_M},{base.WORLD_ORIGIN_Z_M})",
        f"demGrid={base.DEM_GRID}",
        f"count={len(tiles)}",
    )

    for i, (x, y) in enumerate(tiles, 1):
        print(f"=== [{i}/{len(tiles)}] {x:02d}_{y:02d} ===")
        base.build_dem(x, y, overwrite=args.overwrite)

    missing = []
    for x, y in tiles:
        p = base.OUT_DEM / f"{x:02d}_{y:02d}.gst2"
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(str(p.relative_to(ROOT)))
    if missing:
        raise SystemExit("missing outputs: " + ", ".join(missing[:20]))

    print("complete", args.region, len(tiles), "tiles")


if __name__ == "__main__":
    main()
