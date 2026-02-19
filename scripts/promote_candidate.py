#!/usr/bin/env python3
"""Promote candidate to current baseline. Skeleton."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote candidate to current baseline")
    parser.add_argument("--candidate", type=str, required=True, help="Path to candidate JSON")
    parser.add_argument("--baseline-dir", type=str, default="artifacts/baselines", help="Baselines dir")
    args = parser.parse_args()

    base = Path(args.baseline_dir)
    current = base / "current.json"
    history_dir = base / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    print(f"[promote_candidate] Would copy {args.candidate} -> {current}, backup to history/")
    # TODO: validate candidate, backup current, write candidate to current.json
    return 0


if __name__ == "__main__":
    sys.exit(main())
