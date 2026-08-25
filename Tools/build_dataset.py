#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import math
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from PIL import Image

CENTER_LAT = 35.3606
CENTER_LON = 138.7274
GRID_X = 15
GRID_Y = 14
TILE_SIZE_M = 4000.0
WORLD_ORIGIN_X_M = -30000.0
WORLD_ORIGIN_Z_M = -27500.0
PC_SIZE = 2048
QUEST_SIZE = 1024
DEM_GRID = 65
IMG_Z = 16
DEM_Z = 14
PC_JPEG_QUALITY = 88
QUEST_JPEG_QUALITY = 80
USER_AGENT = "sakusdev-map-source-dataset/2.0 (+https://github.com/sakusdev/map-source)"
REQUEST_DELAY_SECONDS = float(os.environ.get("GSI_REQUEST_DELAY", "0.05"))
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "Tools/.cache/gsi"
OUT_PC = ROOT / "Server/data/pc"
OUT_QUEST = ROOT / "Server/data/quest"
OUT_DEM = ROOT / "Server/data/dem"

# Current GSI recommended precision order. All are available as PNG at z14.
DEM_DATASETS = (
    "dem1a_png",
    "dem5a_png",
    "dem5b_png",
    "dem5c_png",
    "dem_png",
)


def lonlat_to_global_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 256.0 * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def ground_resolution_m_per_px(lat: float, z: int) -> float:
    return 156543.03392804097 * math.cos(math.radians(lat)) / (1 << z)


def tile_local_bounds(x: int, y: int) -> tuple[float, float, float, float]:
    west = WORLD_ORIGIN_X_M + x * TILE_SIZE_M
    east = west + TILE_SIZE_M
    south = WORLD_ORIGIN_Z_M + y * TILE_SIZE_M
    north = south + TILE_SIZE_M
    return west, south, east, north


def tile_global_px_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    cx, cy = lonlat_to_global_px(CENTER_LON, CENTER_LAT, z)
    mpp = ground_resolution_m_per_px(CENTER_LAT, z)
    west, south, east, north = tile_local_bounds(x, y)
    left = cx + west / mpp
    right = cx + east / mpp
    top = cy - north / mpp
    bottom = cy - south / mpp
    return left, top, right, bottom


def cache_path(dataset: str, z: int, x: int, y: int, ext: str) -> Path:
    return CACHE / dataset / str(z) / str(x) / f"{y}.{ext}"


def fetch_cached(url: str, path: Path, timeout: int = 45) -> bytes:
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            if REQUEST_DELAY_SECONDS > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if not data:
                raise IOError("empty response")
            path.write_bytes(data)
            return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            if attempt < 3:
                time.sleep(1.5 * (2 ** attempt))
    assert last_error is not None
    raise last_error


def source_tile_range(bounds: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    tx0 = math.floor(left / 256)
    ty0 = math.floor(top / 256)
    tx1 = math.floor((right - 1e-6) / 256)
    ty1 = math.floor((bottom - 1e-6) / 256)
    return tx0, ty0, tx1, ty1


def fetch_imagery_tile(tx: int, ty: int) -> Image.Image:
    url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{IMG_Z}/{tx}/{ty}.jpg"
    path = cache_path("seamlessphoto", IMG_Z, tx, ty, "jpg")
    data = fetch_cached(url, path)
    with Image.open(io.BytesIO(data)) as im:
        return im.convert("RGB")


def build_imagery(x: int, y: int, overwrite: bool = False) -> None:
    stem = f"{x:02d}_{y:02d}"
    out_pc = OUT_PC / f"{stem}.jpg"
    out_quest = OUT_QUEST / f"{stem}.jpg"
    if not overwrite and out_pc.is_file() and out_pc.stat().st_size > 0 and out_quest.is_file() and out_quest.stat().st_size > 0:
        print("skip imagery", stem)
        return

    bounds = tile_global_px_bounds(x, y, IMG_Z)
    left, top, right, bottom = bounds
    tx0, ty0, tx1, ty1 = source_tile_range(bounds)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_imagery_tile(tx, ty)
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))

    box = (
        left - tx0 * 256,
        top - ty0 * 256,
        right - tx0 * 256,
        bottom - ty0 * 256,
    )
    pc = mosaic.crop(box).resize((PC_SIZE, PC_SIZE), Image.Resampling.LANCZOS)
    quest = pc.resize((QUEST_SIZE, QUEST_SIZE), Image.Resampling.LANCZOS)

    OUT_PC.mkdir(parents=True, exist_ok=True)
    OUT_QUEST.mkdir(parents=True, exist_ok=True)
    pc.save(out_pc, "JPEG", quality=PC_JPEG_QUALITY, optimize=True, progressive=True)
    quest.save(out_quest, "JPEG", quality=QUEST_JPEG_QUALITY, optimize=True, progressive=True)
    print("wrote imagery", stem, out_pc.stat().st_size, out_quest.stat().st_size)


def decode_dem_png(data: bytes) -> list[list[float | None]]:
    with Image.open(io.BytesIO(data)) as im:
        rgb = im.convert("RGB")
        if rgb.size != (256, 256):
            raise ValueError(f"unexpected DEM PNG size: {rgb.size}")
        pixels = list(rgb.getdata())

    rows: list[list[float | None]] = []
    for y in range(256):
        row: list[float | None] = []
        base = y * 256
        for x in range(256):
            r, g, b = pixels[base + x]
            encoded = (r << 16) | (g << 8) | b
            if encoded == (1 << 23):
                row.append(None)
            elif encoded < (1 << 23):
                row.append(encoded * 0.01)
            else:
                row.append((encoded - (1 << 24)) * 0.01)
        rows.append(row)
    return rows


def fetch_dem_source_tile(dataset: str, tx: int, ty: int) -> list[list[float | None]] | None:
    url = f"https://cyberjapandata.gsi.go.jp/xyz/{dataset}/{DEM_Z}/{tx}/{ty}.png"
    path = cache_path(dataset, DEM_Z, tx, ty, "png")
    try:
        data = fetch_cached(url, path)
        return decode_dem_png(data)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except (urllib.error.URLError, ValueError, OSError) as exc:
        print("DEM source error", dataset, tx, ty, repr(exc))
        return None


def fetch_best_dem_tile(tx: int, ty: int) -> list[list[float | None]]:
    combined: list[list[float | None]] = [[None] * 256 for _ in range(256)]
    missing = 256 * 256

    for dataset in DEM_DATASETS:
        source = fetch_dem_source_tile(dataset, tx, ty)
        if source is None:
            continue
        filled = 0
        for y in range(256):
            dst_row = combined[y]
            src_row = source[y]
            for x in range(256):
                if dst_row[x] is None and src_row[x] is not None:
                    dst_row[x] = src_row[x]
                    filled += 1
        missing -= filled
        print("dem", tx, ty, dataset, "filled", filled, "remaining", missing)
        if missing <= 0:
            break

    return combined


def nearest_valid(data: list[list[float | None]], x: int, y: int) -> float:
    h, w = len(data), len(data[0])
    v = data[y][x]
    if v is not None:
        return v
    for radius in range(1, 17):
        y0, y1 = max(0, y - radius), min(h, y + radius + 1)
        x0, x1 = max(0, x - radius), min(w, x + radius + 1)
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                vv = data[yy][xx]
                if vv is not None:
                    return vv
    return 0.0


def bilinear(data: list[list[float | None]], x: float, y: float) -> float:
    h, w = len(data), len(data[0])
    x = max(0.0, min(x, w - 1.001))
    y = max(0.0, min(y, h - 1.001))
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0
    v00 = nearest_valid(data, x0, y0)
    v10 = nearest_valid(data, x1, y0)
    v01 = nearest_valid(data, x0, y1)
    v11 = nearest_valid(data, x1, y1)
    return (v00 * (1 - fx) + v10 * fx) * (1 - fy) + (v01 * (1 - fx) + v11 * fx) * fy


def build_dem(x: int, y: int, overwrite: bool = False) -> None:
    stem = f"{x:02d}_{y:02d}"
    out_dem = OUT_DEM / f"{stem}.gst2"
    if not overwrite and out_dem.is_file() and out_dem.stat().st_size > 0:
        print("skip dem", stem)
        return

    bounds = tile_global_px_bounds(x, y, DEM_Z)
    left, top, right, bottom = bounds
    tx0, ty0, tx1, ty1 = source_tile_range(bounds)
    width = (tx1 - tx0 + 1) * 256
    height = (ty1 - ty0 + 1) * 256
    mosaic: list[list[float | None]] = [[None] * width for _ in range(height)]

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_best_dem_tile(tx, ty)
            ox, oy = (tx - tx0) * 256, (ty - ty0) * 256
            for py in range(256):
                mosaic[oy + py][ox : ox + 256] = tile[py]

    local_left = left - tx0 * 256
    local_top = top - ty0 * 256
    local_right = right - tx0 * 256
    local_bottom = bottom - ty0 * 256

    # Mesh vertex order is south -> north in Unity (+Z), while raster Y is north -> south.
    heights: list[float] = []
    for gy in range(DEM_GRID):
        north_fraction = gy / (DEM_GRID - 1)
        py = local_bottom + (local_top - local_bottom) * north_fraction
        for gx in range(DEM_GRID):
            east_fraction = gx / (DEM_GRID - 1)
            px = local_left + (local_right - local_left) * east_fraction
            heights.append(bilinear(mosaic, px, py))

    mm = [round(v * 1000.0) for v in heights]
    offset = min(mm)
    maxv = max(mm)
    scale = max(1, math.ceil((maxv - offset) / 65535))
    samples = [max(0, min(65535, round((v - offset) / scale))) for v in mm]

    header = b"GST2" + bytes((2, 0)) + struct.pack("<Hii", DEM_GRID, offset, scale)
    payload = header + struct.pack("<" + "H" * len(samples), *samples)
    OUT_DEM.mkdir(parents=True, exist_ok=True)
    out_dem.write_bytes(payload)
    print("wrote dem", stem, "bytes", len(payload), "min_m", min(heights), "max_m", max(heights), "scale_mm", scale)


def iter_all_tiles() -> Iterable[tuple[int, int]]:
    for y in range(GRID_Y):
        for x in range(GRID_X):
            yield x, y


def validate_tile(x: int, y: int) -> None:
    if x < 0 or x >= GRID_X or y < 0 or y >= GRID_Y:
        raise SystemExit(f"tile outside grid: {x},{y}; valid x=0..{GRID_X - 1} y=0..{GRID_Y - 1}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build GSI Streaming Terrain v2 PC/Quest imagery and GST2 DEM data.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="build all 15x14 = 210 supertiles")
    selection.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"), help="build one supertile")
    parser.add_argument("--imagery-only", action="store_true")
    parser.add_argument("--dem-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.imagery_only and args.dem_only:
        raise SystemExit("--imagery-only and --dem-only are mutually exclusive")

    if args.all:
        tiles = list(iter_all_tiles())
    else:
        x, y = args.tile
        validate_tile(x, y)
        tiles = [(x, y)]

    print(
        "dataset",
        f"center={CENTER_LAT},{CENTER_LON}",
        f"grid={GRID_X}x{GRID_Y}",
        f"tile={TILE_SIZE_M}m",
        f"origin=({WORLD_ORIGIN_X_M},{WORLD_ORIGIN_Z_M})",
        f"count={len(tiles)}",
    )

    for index, (x, y) in enumerate(tiles, start=1):
        print(f"=== [{index}/{len(tiles)}] {x:02d}_{y:02d} ===")
        if not args.dem_only:
            build_imagery(x, y, args.overwrite)
        if not args.imagery_only:
            build_dem(x, y, args.overwrite)

    missing: list[str] = []
    for x, y in tiles:
        stem = f"{x:02d}_{y:02d}"
        if not args.dem_only:
            for p in (OUT_PC / f"{stem}.jpg", OUT_QUEST / f"{stem}.jpg"):
                if not p.is_file() or p.stat().st_size == 0:
                    missing.append(str(p.relative_to(ROOT)))
        if not args.imagery_only:
            p = OUT_DEM / f"{stem}.gst2"
            if not p.is_file() or p.stat().st_size == 0:
                missing.append(str(p.relative_to(ROOT)))

    if missing:
        raise SystemExit("missing outputs: " + ", ".join(missing))
    print("complete", len(tiles), "tiles")


if __name__ == "__main__":
    main()
