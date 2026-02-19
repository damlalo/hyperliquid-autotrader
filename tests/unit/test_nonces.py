"""Tests for nonces. Placeholder."""


def test_nonces_module_exists() -> None:
    from autotrader.hl import nonces

    assert hasattr(nonces, "get_next")


def test_get_next_returns_int() -> None:
    from autotrader.hl.nonces import get_next

    n = get_next()
    assert isinstance(n, int)
