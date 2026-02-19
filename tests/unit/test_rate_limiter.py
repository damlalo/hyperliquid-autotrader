"""Tests for rate limiter. Placeholder."""


def test_rate_limiter_module_exists() -> None:
    from autotrader.hl import rate_limiter

    assert hasattr(rate_limiter, "acquire")


def test_acquire_does_not_raise() -> None:
    """acquire() is callable (no exception)."""
    from autotrader.hl.rate_limiter import acquire

    acquire()  # placeholder does nothing
