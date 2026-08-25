#!/usr/bin/env python3
from __future__ import annotations

import io
import math
import os
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from PIL import Image

CENTER_LAT = 35.3606
CENTER_LON = 138.7274
TILE_SIZE_M = 4000.0
OUT_X = 7
OUT_Y = 6
PC_SIZE = 2048
QUEST_SIZE = 1024
DEM_GRID = 65
IMG_Z = 16
DEM_Z = 15
USER_AGENT = "sakusdev-map-source-seed/1.0"
ROOT = Path(__file__).resolve().parents[1]
OUT_PC = ROOT / "Server/data/pc" / f"{OUT_X:02d}_{OUT_Y:02d}.jpg"
OUT_QUEST = ROOT / "Server/data/quest" / f"{OUT_X:02d}_{OUT_Y:02d}.jpg"
OUT_DEM = ROOT / "Server/data/dem" / f"{OUT_X:02d}_{OUT_Y:02d}.gst2"


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def lonlat_to_global_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 256.0 * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def ground_resolution_m_per_px(lat: float, z: int) -> float:
    return 156543.03392804097 * math.cos(math.radians(lat)) / (1 << z)


def build_imagery() -> None:
    cx, cy = lonlat_to_global_px(CENTER_LON, CENTER_LAT, IMG_Z)
    span_px = TILE_SIZE_M / ground_resolution_m_per_px(CENTER_LAT, IMG_Z)
    left = cx - span_px / 2
    top = cy - span_px / 2
    right = cx + span_px / 2
    bottom = cy + span_px / 2

    tx0, ty0 = math.floor(left / 256), math.floor(top / 256)
    tx1, ty1 = math.floor((right - 1) / 256), math.floor((bottom - 1) / 256)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{IMG_Z}/{tx}/{ty}.jpg"
            data = fetch(url)
            with Image.open(io.BytesIO(data)) as im:
                tile = im.convert("RGB")
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
            print("imagery", url)

    box = (left - tx0 * 256, top - ty0 * 256, right - tx0 * 256, bottom - ty0 * 256)
    crop = mosaic.crop(box).resize((PC_SIZE, PC_SIZE), Image.Resampling.LANCZOS)
    OUT_PC.parent.mkdir(parents=True, exist_ok=True)
    crop.save(OUT_PC, "JPEG", quality=88, optimize=True, progressive=True)
    crop.resize((QUEST_SIZE, QUEST_SIZE), Image.Resampling.LANCZOS).save(
        OUT_QUEST, "JPEG", quality=80, optimize=True, progressive=True
    )
    print("wrote", OUT_PC, OUT_QUEST)


def parse_dem_text(data: bytes) -> list[list[float | None]]:
    rows: list[list[float | None]] = []
    for raw_line in data.decode("utf-8").strip().splitlines():
        row: list[float | None] = []
        for raw in raw_line.split(","):
            raw = raw.strip()
            if not raw or raw == "e":
                row.append(None)
            else:
                row.append(float(raw))
        rows.append(row)
    if len(rows) != 256 or any(len(r) != 256 for r in rows):
        raise ValueError("unexpected DEM tile dimensions")
    return rows


def fetch_dem_tile(tx: int, ty: int) -> list[list[float | None]]:
    for dataset in ("dem5a", "dem5b", "dem"):
        z = DEM_Z if dataset != "dem" else min(DEM_Z, 14)
        if z != DEM_Z:
            shift = DEM_Z - z
            qx, qy = tx >> shift, ty >> shift
        else:
            qx, qy = tx, ty
        url = f"https://cyberjapandata.gsi.go.jp/xyz/{dataset}/{z}/{qx}/{qy}.txt"
        try:
            rows = parse_dem_text(fetch(url))
            if z == DEM_Z:
                print("dem", dataset, url)
                return rows
            # Expand the parent DEM10B tile to the requested z15 child quadrant.
            ox = (tx & 1) * 128
            oy = (ty & 1) * 128
            out: list[list[float | None]] = []
            for y in range(256):
                sy = oy + y / 2
                iy = min(255, int(sy))
                out.append([])
                for x in range(256):
                    sx = ox + x / 2
                    ix = min(255, int(sx))
                    out[-1].append(rows[iy][ix])
            print("dem", dataset, url, "expanded")
            return out
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            print("dem fallback", dataset, tx, ty, repr(e))
    return [[None] * 256 for _ in range(256)]


def nearest_valid(data: list[list[float | None]], x: int, y: int) -> float:
    h, w = len(data), len(data[0])
    v = data[y][x]
    if v is not None:
        return v
    for radius in range(1, 9):
        for yy in range(max(0, y - radius), min(h, y + radius + 1)):
            for xx in range(max(0, x - radius), min(w, x + radius + 1)):
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


def build_dem() -> None:
    cx, cy = lonlat_to_global_px(CENTER_LON, CENTER_LAT, DEM_Z)
    span_px = TILE_SIZE_M / ground_resolution_m_per_px(CENTER_LAT, DEM_Z)
    left, top = cx - span_px / 2, cy - span_px / 2
    right, bottom = cx + span_px / 2, cy + span_px / 2
    tx0, ty0 = math.floor(left / 256), math.floor(top / 256)
    tx1, ty1 = math.floor((right - 1) / 256), math.floor((bottom - 1) / 256)
    width, height = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    mosaic: list[list[float | None]] = [[None] * width for _ in range(height)]

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_dem_tile(tx, ty)
            ox, oy = (tx - tx0) * 256, (ty - ty0) * 256
            for y in range(256):
                mosaic[oy + y][ox : ox + 256] = tile[y]

    local_left = left - tx0 * 256
    local_top = top - ty0 * 256
    local_right = right - tx0 * 256
    local_bottom = bottom - ty0 * 256
    heights: list[float] = []
    for gy in range(DEM_GRID):
        fy = gy / (DEM_GRID - 1)
        y = local_top + (local_bottom - local_top) * fy
        for gx in range(DEM_GRID):
            fx = gx / (DEM_GRID - 1)
            x = local_left + (local_right - local_left) * fx
            heights.append(bilinear(mosaic, x, y))

    mm = [round(v * 1000.0) for v in heights]
    offset = min(mm)
    maxv = max(mm)
    scale = max(1, math.ceil((maxv - offset) / 65535))
    samples = [max(0, min(65535, round((v - offset) / scale))) for v in mm]
    header = b"GST2" + bytes((2, 0)) + struct.pack("<Hii", DEM_GRID, offset, scale)
    payload = header + struct.pack("<" + "H" * len(samples), *samples)
    OUT_DEM.parent.mkdir(parents=True, exist_ok=True)
    OUT_DEM.write_bytes(payload)
    print("wrote", OUT_DEM, "grid", DEM_GRID, "offset_mm", offset, "scale_mm", scale)


def main() -> None:
    build_imagery()
    build_dem()
    for p in (OUT_PC, OUT_QUEST, OUT_DEM):
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"missing output: {p}")
        print(p.relative_to(ROOT), p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
