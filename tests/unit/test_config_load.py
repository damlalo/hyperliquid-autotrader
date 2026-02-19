"""Tests for config load. Placeholder."""


def test_config_load_module_exists() -> None:
    """Config module is importable."""
    from autotrader.utils import config

    assert hasattr(config, "load_config")


def test_load_config_returns_dict() -> None:
    """load_config returns a dict (placeholder)."""
    from autotrader.utils.config import load_config

    out = load_config("config/paper.yaml")
    assert isinstance(out, dict)
