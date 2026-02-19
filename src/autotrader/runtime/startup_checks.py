"""Startup checks: config, env, HL connectivity, baseline (live/canary), kill switch state."""


def run_startup_checks(config: dict) -> list[str]:
    """Run all checks; return list of error messages. Empty => OK."""
    errors: list[str] = []
    # TODO: validate config, required env, HL info ping, baseline load, kill switch
    return errors
