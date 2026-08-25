#!/usr/bin/env python3
from __future__ import annotations

import io
import urllib.error

from PIL import Image

import build_dataset as base


def fetch_imagery_tile_with_fallback(tx: int, ty: int) -> Image.Image:
    seamless_url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{base.IMG_Z}/{tx}/{ty}.jpg"
    seamless_path = base.cache_path("seamlessphoto", base.IMG_Z, tx, ty, "jpg")
    try:
        data = base.fetch_cached(seamless_url, seamless_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        print("imagery seamlessphoto 404; fallback std", tx, ty)

    std_url = f"https://cyberjapandata.gsi.go.jp/xyz/std/{base.IMG_Z}/{tx}/{ty}.png"
    std_path = base.cache_path("std", base.IMG_Z, tx, ty, "png")
    try:
        data = base.fetch_cached(std_url, std_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        print("imagery std 404; using neutral placeholder", tx, ty)
        return Image.new("RGB", (256, 256), (127, 127, 127))


base.fetch_imagery_tile = fetch_imagery_tile_with_fallback

if __name__ == "__main__":
    base.main()
