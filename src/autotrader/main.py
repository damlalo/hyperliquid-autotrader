"""Entrypoint: parse --env and --config, run startup_checks, dispatch to paper/canary/live."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["paper", "canary", "live"], default="paper")
    parser.add_argument("--config", type=str, help="Config file path")
    args = parser.parse_args()

    # TODO: load config from args.config or default for args.env
    # TODO: run startup_checks
    print(f"[main] env={args.env}, config={args.config or 'default for env'}")
    print("[main] TODO: startup_checks then dispatch to scheduler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
