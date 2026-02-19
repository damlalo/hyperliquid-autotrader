#!/usr/bin/env python3
"""Healthcheck: config, env, connectivity. Skeleton."""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Healthcheck")
    parser.add_argument("--config", type=str, default="config/paper.yaml", help="Config path")
    args = parser.parse_args()

    print(f"[healthcheck] Config: {args.config}")
    required = ["HL_ACCOUNT_ADDRESS", "HL_API_WALLET_PRIVATE_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"[healthcheck] Missing env: {missing}")
        return 1
    print("[healthcheck] Env OK; TODO: HL info ping, DB ping if POSTGRES_DSN set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
