"""Live entrypoint. Key rule: live never runs research; only loads approved bundles from governance."""
from __future__ import annotations

import argparse
import sys


def load_config(path: str | None) -> dict:
    """Load config from path. TODO: implement YAML + env substitution."""
    return {}


def run_startup_checks(cfg: dict) -> None:
    """Run startup checks; raise or exit if any fail. TODO: implement."""
    pass


def run_paper_or_canary(cfg: dict) -> None:
    """Run paper or canary trader. TODO: implement."""
    pass


def load_approved_bundle() -> dict:
    """Load from artifacts/baselines/current.json. TODO: implement."""
    return {}


def run_live_trader(cfg: dict, bundle: dict) -> None:
    """Run live trader with approved bundle only. TODO: implement."""
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["paper", "canary", "live"], default="paper")
    parser.add_argument(
        "--config", type=str, default="config/base.yaml", help="Config file path"
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_startup_checks(cfg)

    if cfg.get("env", "paper") in ("paper", "canary"):
        run_paper_or_canary(cfg)
        return 0

    # live
    bundle = load_approved_bundle()
    run_live_trader(cfg, bundle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
