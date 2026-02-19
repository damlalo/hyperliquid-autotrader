"""Smoke test: all main packages import."""


def test_import_autotrader() -> None:
    import autotrader

    assert autotrader.__version__


def test_import_hl() -> None:
    from autotrader.hl import client, rate_limiter, nonces

    assert client.info is not None
    assert rate_limiter.acquire is not None
    assert nonces.get_next is not None


def test_import_runtime() -> None:
    from autotrader.runtime import startup_checks, kill_switch

    assert startup_checks.run_startup_checks is not None
    assert kill_switch.is_triggered is not None


def test_import_strategies_base() -> None:
    from autotrader.strategies.base import BaseStrategy

    assert BaseStrategy is not None


def test_import_risk_constraints() -> None:
    from autotrader.risk import constraints

    assert constraints.check_portfolio is not None
    assert constraints.check_trade is not None


def test_import_regime_classifier() -> None:
    from autotrader.regimes.classifier import classify

    assert classify({}) == "unknown"
