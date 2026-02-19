#!/usr/bin/env python3
"""Rollback to a previous baseline from history. Skeleton."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback to previous baseline")
    parser.add_argument("--baseline-dir", type=str, default="artifacts/baselines", help="Baselines dir")
    parser.add_argument("--to", type=str, help="History file or 'latest'")
    args = parser.parse_args()

    print(f"[rollback] Would restore from history: {args.to or 'latest'}")
    print("[rollback] TODO: copy history file to current.json, then restart runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
