#!/usr/bin/env python3
"""Run live trading. Skeleton. Do not use without passing governance gates."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Live trade (real orders)")
    parser.add_argument("--config", type=str, default="config/live.yaml", help="Config path")
    args = parser.parse_args()

    print(f"[live_trade] Config: {args.config}")
    print("[live_trade] TODO: load baseline, run main with env=live; only after gates and canary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
