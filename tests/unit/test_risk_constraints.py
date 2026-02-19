"""Tests for risk constraints. Placeholder."""


def test_check_portfolio_returns_bool() -> None:
    from autotrader.risk.constraints import check_portfolio

    assert check_portfolio(1000.0, {}) is True


def test_check_trade_returns_bool() -> None:
    from autotrader.risk.constraints import check_trade

    assert check_trade(100.0, 1.0, {}) is True
