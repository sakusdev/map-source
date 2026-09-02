#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path

import build_dem_region as regional
import build_near_detail as near


def complete(root: Path, x: int, y: int, subdivision: int) -> bool:
    stem = f"{x:02d}_{y:02d}"
    meta = root / f"{stem}.json"
    if not meta.is_file() or meta.stat().st_size == 0:
        return False
    for i in range(subdivision * subdivision):
        p = root / f"{stem}_c{i:02d}.jpg"
        if not p.is_file() or p.stat().st_size == 0:
            return False
    return True


def cleanup_incomplete(root: Path, x: int, y: int, subdivision: int) -> None:
    if complete(root, x, y, subdivision):
        return
    stem = f"{x:02d}_{y:02d}"
    (root / f"{stem}.json").unlink(missing_ok=True)
    for i in range(subdivision * subdivision):
        (root / f"{stem}_c{i:02d}.jpg").unlink(missing_ok=True)


def center_out_tiles(grid_x: int, grid_y: int) -> list[tuple[int, int]]:
    cx = (grid_x - 1) * 0.5
    cy = (grid_y - 1) * 0.5
    tiles = [(x, y) for y in range(grid_y) for x in range(grid_x)]
    tiles.sort(key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cy) ** 2, abs(p[1] - cy), abs(p[0] - cx), p[1], p[0]))
    return tiles


def main() -> None:
    ap = argparse.ArgumentParser(description="Resumably expand GST2 near-detail imagery across a region, center-out.")
    ap.add_argument("--region", choices=sorted(regional.REGIONS), default="kanto")
    ap.add_argument("--source-zoom", type=int, default=18)
    ap.add_argument("--subdivision", type=int, default=4)
    ap.add_argument("--max-tiles", type=int, default=1, help="0 means unlimited successful tiles")
    ap.add_argument("--max-seconds", type=float, default=0.0, help="0 means no time limit")
    ap.add_argument("--tile-attempts", type=int, default=2)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--shard-index", type=int, default=0)
    args = ap.parse_args()

    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit(f"--shard-index must be in 0..{args.shard_count - 1}")

    cfg = regional.apply_region(args.region)
    root = near.output_root(args.region, args.source_zoom, args.subdivision)
    root.mkdir(parents=True, exist_ok=True)
    ordered = center_out_tiles(int(cfg["grid_x"]), int(cfg["grid_y"]))

    # Partition BEFORE filtering completed outputs. This keeps tile ownership
    # stable across concurrent branches and future resume runs.
    owned = [tile for ordinal, tile in enumerate(ordered) if ordinal % args.shard_count == args.shard_index]
    missing = [(x, y) for x, y in owned if not complete(root, x, y, args.subdivision)]
    print(
        f"NEAR_REGION_BEGIN region={args.region} grid={cfg['grid_x']}x{cfg['grid_y']} "
        f"shard={args.shard_index}/{args.shard_count} owned={len(owned)} missing={len(missing)} "
        f"sourceZ={args.source_zoom} subdivision={args.subdivision}"
    )

    started = time.monotonic()
    built = 0
    failures: list[str] = []

    for x, y in missing:
        if args.max_tiles > 0 and built >= args.max_tiles:
            break
        if args.max_seconds > 0 and time.monotonic() - started >= args.max_seconds:
            print("NEAR_REGION_TIME_LIMIT")
            break

        stem = f"{x:02d}_{y:02d}"
        ok = False
        for attempt in range(1, max(1, args.tile_attempts) + 1):
            try:
                cleanup_incomplete(root, x, y, args.subdivision)
                print(f"NEAR_REGION_BUILD tile={stem} shard={args.shard_index}/{args.shard_count} attempt={attempt}")
                near.build_near(args.region, x, y, False, args.source_zoom, args.subdivision)
                if not complete(root, x, y, args.subdivision):
                    raise RuntimeError("tile output incomplete")
                ok = True
                break
            except Exception as exc:
                cleanup_incomplete(root, x, y, args.subdivision)
                print(f"NEAR_REGION_TILE_ERROR tile={stem} attempt={attempt} error={exc!r}")
                if attempt < max(1, args.tile_attempts):
                    time.sleep(min(30.0, 5.0 * attempt))

        if ok:
            built += 1
            print(f"NEAR_REGION_TILE_READY tile={stem} shard={args.shard_index}/{args.shard_count} built={built}")
        else:
            failures.append(stem)

    remaining = sum(1 for x, y in owned if not complete(root, x, y, args.subdivision))
    print(
        f"NEAR_REGION_DONE shard={args.shard_index}/{args.shard_count} "
        f"built={built} remainingOwned={remaining} failures={len(failures)}"
    )
    if failures:
        print("NEAR_REGION_FAILURES " + " ".join(failures[:50]))
    if built == 0 and remaining > 0:
        raise SystemExit("no near-detail tile could be completed in this pass")


if __name__ == "__main__":
    main()
