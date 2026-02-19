#!/usr/bin/env python3
"""Bootstrap historical data (candles, funding, etc.) into store. Skeleton."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# TODO: import from autotrader when implemented
# from autotrader.utils.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap history into store")
    parser.add_argument("--config", type=str, default="config/paper.yaml", help="Config path")
    parser.add_argument("--symbols", type=str, nargs="*", help="Symbols to fetch")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    print(f"[bootstrap_history] Would load config: {args.config}")
    print(f"[bootstrap_history] Symbols: {args.symbols or 'from config'}, range: {args.start} - {args.end}")
    print("[bootstrap_history] TODO: implement HL historical fetch and store write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
