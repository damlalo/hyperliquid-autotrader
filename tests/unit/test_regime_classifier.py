"""Tests for regime classifier. Placeholder."""


def test_classify_returns_string() -> None:
    from autotrader.regimes.classifier import classify

    out = classify({})
    assert isinstance(out, str)
    assert out == "unknown"


def test_hysteresis_apply() -> None:
    from autotrader.regimes.hysteresis import apply

    out = apply("trend", "range", {})
    assert isinstance(out, str)
