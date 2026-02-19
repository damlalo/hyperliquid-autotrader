#!/usr/bin/env python3
"""Startup verification. Must block the bot from trading if any check fails:
websocket connect, REST reachability, account read access, nonce window validity
and persistence, rate limit budgeter, and dataset availability.
Nonce constraints are strict on Hyperliquid."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    # 1) verify config + secrets present
    # 2) verify REST /info reachable and returns meta
    # 3) verify WS connect and can subscribe/unsubscribe
    # 4) verify server time sync within tolerance
    # 5) verify account state readable (clearinghouseState)
    # 6) verify nonce store writable + next nonce valid
    # 7) verify rate limiter initialized (1200 weight/min policy)
    # 8) verify kill switch is not engaged
    parser = argparse.ArgumentParser(description="Healthcheck (blocks trading if any check fails)")
    parser.add_argument("--config", type=str, default="config/base.yaml", help="Config path")
    args = parser.parse_args()
    # TODO: load config, run each check; return 1 on first failure
    print(f"[healthcheck] Config: {args.config}; TODO: implement all 8 checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
