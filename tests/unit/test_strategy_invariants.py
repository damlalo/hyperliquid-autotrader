"""Strategy invariants: no cap override, no-trade conditions. Placeholder."""


def test_base_strategy_has_signal() -> None:
    from autotrader.strategies.base import BaseStrategy

    class Stub(BaseStrategy):
        pass

    s = Stub()
    out = s.signal({})
    assert isinstance(out, dict)


def test_ensemble_combine_returns_dict() -> None:
    from autotrader.strategies.ensemble import combine

    out = combine([], [])
    assert isinstance(out, dict)
