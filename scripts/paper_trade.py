#!/usr/bin/env python3
"""Run paper trading loop. Skeleton."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper trade (no real orders)")
    parser.add_argument("--config", type=str, default="config/paper.yaml", help="Config path")
    args = parser.parse_args()

    print(f"[paper_trade] Config: {args.config}")
    print("[paper_trade] TODO: run main with env=paper; no orders sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
