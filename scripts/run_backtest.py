#!/usr/bin/env python3
"""Run backtest from config. Skeleton."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--config", type=str, default="config/paper.yaml", help="Config path")
    parser.add_argument("--dataset", type=str, help="Dataset manifest or path")
    args = parser.parse_args()

    print(f"[run_backtest] Config: {args.config}, dataset: {args.dataset or 'from config'}")
    print("[run_backtest] TODO: load config, run event-driven backtest, write metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
