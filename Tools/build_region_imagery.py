#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import urllib.error
from pathlib import Path

from PIL import Image

import build_dataset as base
import build_dem_region as region_dem

ROOT = Path(__file__).resolve().parents[1]

TIERS = {
    "far": {"source_z": 14, "size": 512, "quality": 76},
    "base": {"source_z": 16, "size": 1024, "quality": 84},
}


def fetch_source(source_z: int, tx: int, ty: int) -> Image.Image:
    url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{source_z}/{tx}/{ty}.jpg"
    path = base.cache_path("seamlessphoto", source_z, tx, ty, "jpg")
    try:
        data = base.fetch_cached(url, path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        # Seamlessphoto has no imagery over some sea-only source tiles. Keep a
        # neutral fallback so one offshore tile cannot abort a regional shard.
        return Image.new("RGB", (256, 256), (88, 112, 122))


def build_tile(region_id: str, tier: str, x: int, y: int, overwrite: bool) -> Path:
    cfg = TIERS[tier]
    source_z = int(cfg["source_z"])
    size = int(cfg["size"])
    quality = int(cfg["quality"])
    stem = f"{x:02d}_{y:02d}"
    out_dir = ROOT / "Server/data/regions" / region_id / "img" / tier
    out = out_dir / f"{stem}.jpg"
    if not overwrite and out.is_file() and out.stat().st_size > 0:
        print("skip imagery", region_id, tier, stem)
        return out

    bounds = base.tile_global_px_bounds(x, y, source_z)
    left, top, right, bottom = bounds
    tx0, ty0, tx1, ty1 = base.source_tile_range(bounds)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_source(source_z, tx, ty)
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))

    box = (left - tx0 * 256, top - ty0 * 256, right - tx0 * 256, bottom - ty0 * 256)
    img = mosaic.crop(box).resize((size, size), Image.Resampling.LANCZOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    print("wrote region imagery", out.relative_to(ROOT), out.stat().st_size, f"sourceZ={source_z}", f"size={size}")
    return out


def write_tier_manifest(region_id: str, tier: str) -> Path:
    cfg = TIERS[tier]
    out_dir = ROOT / "Server/data/regions" / region_id / "img" / tier
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "GST2_REGION_IMAGERY",
        "version": 1,
        "region": region_id,
        "tier": tier,
        "source": "GSI seamlessphoto",
        "sourceZoom": int(cfg["source_z"]),
        "imageSize": int(cfg["size"]),
        "jpegQuality": int(cfg["quality"]),
        "tileSizeMeters": base.TILE_SIZE_M,
        "grid": {"x": base.GRID_X, "y": base.GRID_Y},
        "urlPattern": "{x:02d}_{y:02d}.jpg",
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build lightweight regional imagery supertiles.")
    p.add_argument("--region", choices=sorted(region_dem.REGIONS), default="kanto")
    p.add_argument("--tier", choices=sorted(TIERS), default="far")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--tile", nargs=2, type=int, metavar=("X", "Y"))
    sel.add_argument("--row", type=int)
    sel.add_argument("--rows", nargs=2, type=int, metavar=("START", "END"))
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    region_dem.apply_region(args.region)
    # apply_region installs the regional resilient fetch implementation.
    write_tier_manifest(args.region, args.tier)

    if args.tile is not None:
        x, y = args.tile
        base.validate_tile(x, y)
        tiles = [(x, y)]
    elif args.row is not None:
        if args.row < 0 or args.row >= base.GRID_Y:
            raise SystemExit("row outside region")
        tiles = [(x, args.row) for x in range(base.GRID_X)]
    else:
        start, end = args.rows
        if start < 0 or end < start or end >= base.GRID_Y:
            raise SystemExit("rows outside region")
        tiles = [(x, y) for y in range(start, end + 1) for x in range(base.GRID_X)]

    for i, (x, y) in enumerate(tiles, 1):
        print(f"=== imagery [{i}/{len(tiles)}] {x:02d}_{y:02d} tier={args.tier} ===")
        build_tile(args.region, args.tier, x, y, args.overwrite)

    print("complete imagery", args.region, args.tier, len(tiles))


if __name__ == "__main__":
    main()
