#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import urllib.error
from pathlib import Path

from PIL import Image

import build_dataset as base
import build_dem_region as regional

DEFAULT_SOURCE_Z = 18
DEFAULT_SUBDIVISION = 4
NEAR_SIZE = 2048
NEAR_JPEG_QUALITY = 90


def fetch_source(source_z: int, tx: int, ty: int) -> Image.Image:
    seamless_url = f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{source_z}/{tx}/{ty}.jpg"
    seamless_path = base.cache_path("seamlessphoto", source_z, tx, ty, "jpg")
    try:
        data = base.fetch_cached(seamless_url, seamless_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    std_url = f"https://cyberjapandata.gsi.go.jp/xyz/std/{source_z}/{tx}/{ty}.png"
    std_path = base.cache_path("std", source_z, tx, ty, "png")
    try:
        data = base.fetch_cached(std_url, std_path)
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return Image.new("RGB", (256, 256), (127, 127, 127))


def configure_region(region_id: str) -> None:
    if region_id == "legacy":
        return
    regional.apply_region(region_id)


def output_root(region_id: str, source_z: int, subdivision: int) -> Path:
    if region_id == "legacy":
        return base.ROOT / f"Server/data/near-z{source_z}-s{subdivision}"
    return base.ROOT / f"Server/data/regions/{region_id}/near-z{source_z}-s{subdivision}"


def output_path(root: Path, stem: str, cell_index: int) -> Path:
    return root / f"{stem}_c{cell_index:02d}.jpg"


def build_near(
    region_id: str,
    x: int,
    y: int,
    overwrite: bool,
    source_z: int,
    subdivision: int,
) -> None:
    configure_region(region_id)
    base.validate_tile(x, y)
    if subdivision < 2 or subdivision > 8:
        raise SystemExit("subdivision must be 2..8")
    if source_z < 16 or source_z > 20:
        raise SystemExit("source zoom must be 16..20")

    stem = f"{x:02d}_{y:02d}"
    root = output_root(region_id, source_z, subdivision)
    outputs = [output_path(root, stem, i) for i in range(subdivision * subdivision)]
    meta_path = root / f"{stem}.json"
    if (
        not overwrite
        and all(p.is_file() and p.stat().st_size > 0 for p in outputs)
        and meta_path.is_file()
    ):
        print("skip near", region_id, stem, f"z={source_z}", f"subdivision={subdivision}")
        return

    left, top, right, bottom = base.tile_global_px_bounds(x, y, source_z)
    tx0, ty0, tx1, ty1 = base.source_tile_range((left, top, right, bottom))
    source_count = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    print(
        "near source mosaic",
        f"region={region_id}",
        f"tile={stem}",
        f"sourceZ={source_z}",
        f"xyz={tx0},{ty0}..{tx1},{ty1}",
        f"sourceTiles={source_count}",
    )
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetch_source(source_z, tx, ty)
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))

    l = left - tx0 * 256
    t = top - ty0 * 256
    r = right - tx0 * 256
    b = bottom - ty0 * 256
    width = r - l
    height = b - t

    root.mkdir(parents=True, exist_ok=True)
    for cy in range(subdivision):
        # Runtime cell Y grows northward from the south edge. PIL Y grows southward,
        # therefore translate the cell bounds from south-up to top-down here.
        cell_bottom = b - (height * cy / subdivision)
        cell_top = b - (height * (cy + 1) / subdivision)
        for cx in range(subdivision):
            cell_left = l + (width * cx / subdivision)
            cell_right = l + (width * (cx + 1) / subdivision)
            cell_index = cy * subdivision + cx
            box = (cell_left, cell_top, cell_right, cell_bottom)
            img = mosaic.crop(box).resize((NEAR_SIZE, NEAR_SIZE), Image.Resampling.LANCZOS)
            out = outputs[cell_index]
            img.save(out, "JPEG", quality=NEAR_JPEG_QUALITY, optimize=True, progressive=True)
            print(
                "wrote near",
                out.relative_to(base.ROOT),
                out.stat().st_size,
                f"cell={cx},{cy}",
                f"sourceZ={source_z}",
            )

    meters_per_cell = base.TILE_SIZE_M / subdivision
    metadata = {
        "format": "GST2_NEAR",
        "version": 2,
        "region": region_id,
        "tile": {"x": x, "y": y},
        "sourceZoom": source_z,
        "sourceTileCount": source_count,
        "subdivision": subdivision,
        "cellSizeMeters": meters_per_cell,
        "imageSize": NEAR_SIZE,
        "jpegQuality": NEAR_JPEG_QUALITY,
        "cellIndex": "south-row-major: index=y*subdivision+x",
        "urlPattern": f"{stem}_c{{index:02d}}.jpg",
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("near metadata", meta_path.relative_to(base.ROOT), json.dumps(metadata))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build player-centered high-detail imagery cells for one GST2 supertile.")
    parser.add_argument("--region", choices=["legacy", *sorted(regional.REGIONS)], default="legacy")
    parser.add_argument("--tile", nargs=2, type=int, required=True, metavar=("X", "Y"))
    parser.add_argument("--source-zoom", type=int, default=DEFAULT_SOURCE_Z)
    parser.add_argument("--subdivision", type=int, default=DEFAULT_SUBDIVISION)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build_near(args.region, args.tile[0], args.tile[1], args.overwrite, args.source_zoom, args.subdivision)


if __name__ == "__main__":
    main()
