#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import time

import build_water as water


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a deterministic shard of Kanto GSW1 water tiles.")
    p.add_argument("--shard-count", type=int, default=1)
    p.add_argument("--shard-index", type=int, default=0)
    p.add_argument("--max-tiles", type=int, default=50)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--attempts", type=int, default=3)
    return p.parse_args()


def complete(x: int, y: int) -> bool:
    stem = f"{x:02d}_{y:02d}.gsw1"
    for root in (water.OUT_PC, water.OUT_QUEST):
        path = root / stem
        if not path.is_file() or path.stat().st_size < water.HEADER_SIZE:
            return False
        try:
            water.verify(path)
        except Exception:
            return False
    return True


def main() -> None:
    args = parse_args()
    if args.shard_count < 1:
        raise SystemExit("shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("shard-index outside shard-count")
    if args.max_tiles < 1:
        raise SystemExit("max-tiles must be >= 1")

    ordered = [(x, y) for y in range(water.GRID_Y) for x in range(water.GRID_X)]
    owned = [tile for ordinal, tile in enumerate(ordered) if ordinal % args.shard_count == args.shard_index]
    missing = [tile for tile in owned if args.overwrite or not complete(*tile)]
    selected = missing[: args.max_tiles]
    print(
        f"GSW1_REGION_BEGIN shard={args.shard_index}/{args.shard_count} "
        f"owned={len(owned)} missing={len(missing)} selected={len(selected)}"
    )

    built = 0
    failures: list[str] = []
    for i, (x, y) in enumerate(selected, 1):
        ok = False
        last_error: Exception | None = None
        for attempt in range(1, max(1, args.attempts) + 1):
            try:
                print(f"GSW1_REGION_BUILD shard={args.shard_index}/{args.shard_count} tile={x:02d}_{y:02d} item={i}/{len(selected)} attempt={attempt}")
                water.build_tile(x, y, overwrite=args.overwrite)
                water.verify(water.OUT_PC / f"{x:02d}_{y:02d}.gsw1")
                water.verify(water.OUT_QUEST / f"{x:02d}_{y:02d}.gsw1")
                ok = True
                break
            except (Exception, subprocess.SubprocessError) as exc:
                last_error = exc
                if attempt < max(1, args.attempts):
                    time.sleep(min(20, attempt * 4))
        if not ok:
            failures.append(f"{x:02d}_{y:02d}:{last_error!r}")
            continue
        built += 1

    remaining = len(missing) - built
    print(
        f"GSW1_REGION_DONE shard={args.shard_index}/{args.shard_count} "
        f"built={built} remainingOwned={max(0, remaining)} failures={len(failures)}"
    )
    if failures:
        print("GSW1_REGION_FAILURES", " | ".join(failures[:20]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
