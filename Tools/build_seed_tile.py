#!/usr/bin/env python3
from __future__ import annotations

from build_dataset import build_dem, build_imagery

SEED_X = 7
SEED_Y = 6


def main() -> None:
    print(f"Building GSI Streaming Terrain v2 seed tile {SEED_X:02d}_{SEED_Y:02d}")
    build_imagery(SEED_X, SEED_Y, overwrite=True)
    build_dem(SEED_X, SEED_Y, overwrite=True)


if __name__ == "__main__":
    main()
