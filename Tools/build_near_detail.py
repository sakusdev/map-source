#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import urllib.error
from pathlib import Path

from PIL import Image

import build_dataset as base

NEAR_Z = 17
NEAR_SIZE = 2048
NEAR_JPEG_QUALITY = 90
OUT_NEAR = base.ROOT / "Server/data/near"


def fetch_source(tx: int, ty: int) -> Image.Image:
    seamless_url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{NEAR_Z}/{tx}/{ty}.jpg"
    seamless_path = base.cache_path("seamlessphoto", NEAR_Z, tx, ty, "jpg")
    try:
        data = base.fetch_cached(seamless_url, seamless_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    std_url = f"https://cyberjapandata.gsi.go.jp/xyz/std/{NEAR_Z}/{tx}/{ty}.png"
    std_path = base.cache_path("std", NEAR_Z, tx, ty, "png")
    try:
        data = base.fetch_cached(std_url, std_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return Image.new("RGB", (256, 256), (127, 127, 127))


def build_near(x: int, y: int, overwrite: bool) -> None:
    base.validate_tile(x, y)
    stem = f"{x:02d}_{y:02d}"
    outputs = [OUT_NEAR / f"{stem}_q{q}.jpg" for q in range(4)]
    if not overwrite and all(p.is_file() and p.stat().st_size > 0 for p in outputs):
        print("skip near", stem)
        return

    left, top, right, bottom = base.tile_global_px_bounds(x, y, NEAR_Z)
    tx0, ty0, tx1, ty1 = base.source_tile_range((left, top, right, bottom))
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_source(tx, ty)
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))

    l = left - tx0 * 256
    t = top - ty0 * 256
    r = right - tx0 * 256
    b = bottom - ty0 * 256
    mx = (l + r) * 0.5
    my = (t + b) * 0.5

    # q0=SW, q1=SE, q2=NW, q3=NE. PIL Y grows southward.
    boxes = [
        (l, my, mx, b),
        (mx, my, r, b),
        (l, t, mx, my),
        (mx, t, r, my),
    ]

    OUT_NEAR.mkdir(parents=True, exist_ok=True)
    for q, box in enumerate(boxes):
        img = mosaic.crop(box).resize((NEAR_SIZE, NEAR_SIZE), Image.Resampling.LANCZOS)
        img.save(outputs[q], "JPEG", quality=NEAR_JPEG_QUALITY, optimize=True, progressive=True)
        print("wrote near", outputs[q], outputs[q].stat().st_size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 2km/2048px near-detail imagery quadrants for one GST2 supertile.")
    parser.add_argument("--tile", nargs=2, type=int, required=True, metavar=("X", "Y"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build_near(args.tile[0], args.tile[1], args.overwrite)


if __name__ == "__main__":
    main()
