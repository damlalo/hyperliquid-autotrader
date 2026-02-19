#!/usr/bin/env python3
"""Run walk-forward test. Skeleton."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run walk-forward")
    parser.add_argument("--config", type=str, default="config/paper.yaml", help="Config path")
    parser.add_argument("--train-days", type=int, help="Training window days")
    parser.add_argument("--test-days", type=int, help="Test window days")
    args = parser.parse_args()

    print(f"[run_walkforward] Config: {args.config}, train: {args.train_days}, test: {args.test_days}")
    print("[run_walkforward] TODO: implement WFO and write OOS metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
