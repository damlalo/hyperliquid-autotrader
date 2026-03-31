"""Unit tests for TradeLabeler."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrader.ml.labeler import TradeLabeler, TradeOutcome


def _make_trending_df(n: int = 50, step: float = 10.0) -> pd.DataFrame:
    """Steadily rising bars — TP should always be hit for long entries."""
    close = np.arange(1000, 1000 + n * step, step)
    return pd.DataFrame({
        "o": close,
        "h": close + 5,
        "l": close - 5,
        "c": close,
        "v": np.ones(n),
    })


def _make_flat_df(n: int = 50, price: float = 1000.0) -> pd.DataFrame:
    close = np.full(n, price)
    return pd.DataFrame({
        "o": close,
        "h": close + 2,
        "l": close - 2,
        "c": close,
        "v": np.ones(n),
    })


class TestTradeLabeler:
    def setup_method(self):
        self.labeler = TradeLabeler()

    def test_long_tp_hit_trending(self):
        df = _make_trending_df()
        entry_idx = 5
        entry_price = float(df["c"].iloc[entry_idx])
        tp = entry_price * 1.02
        sl = entry_price * 0.98
        outcome = self.labeler.label(df, entry_idx=entry_idx, side="long",
                                     entry_price=entry_price, tp_price=tp, sl_price=sl)
        assert isinstance(outcome, TradeOutcome)
        assert outcome.won is True
        assert outcome.outcome_type == "tp"
        assert outcome.r_multiple > 0

    def test_short_sl_hit_trending(self):
        """In a rising market, a short entry should hit SL."""
        df = _make_trending_df()
        entry_idx = 5
        entry_price = float(df["c"].iloc[entry_idx])
        tp = entry_price * 0.98   # short TP below
        sl = entry_price * 1.02   # short SL above
        outcome = self.labeler.label(df, entry_idx=entry_idx, side="short",
                                     entry_price=entry_price, tp_price=tp, sl_price=sl)
        assert outcome.won is False
        assert outcome.outcome_type == "sl"

    def test_timeout_on_flat_market(self):
        df = _make_flat_df(50)
        entry_idx = 5
        entry_price = float(df["c"].iloc[entry_idx])
        tp = entry_price * 1.10
        sl = entry_price * 0.90
        outcome = self.labeler.label(df, entry_idx=entry_idx, side="long",
                                     entry_price=entry_price, tp_price=tp, sl_price=sl,
                                     max_bars=40)
        assert outcome.outcome_type == "timeout"

    def test_bars_to_outcome_positive(self):
        df = _make_trending_df()
        entry_idx = 2
        entry_price = float(df["c"].iloc[entry_idx])
        tp = entry_price * 1.005
        sl = entry_price * 0.995
        outcome = self.labeler.label(df, entry_idx=entry_idx, side="long",
                                     entry_price=entry_price, tp_price=tp, sl_price=sl)
        assert outcome.bars_to_outcome >= 1

    def test_same_bar_tp_sl_collision_handled(self):
        """TP and SL both hit same bar — should not raise."""
        df = pd.DataFrame({
            "o": [1000.0] * 10,
            "h": [1050.0] * 10,
            "l": [950.0] * 10,
            "c": [1000.0] * 10,
            "v": [1.0] * 10,
        })
        outcome = self.labeler.label(df, entry_idx=0, side="long",
                                     entry_price=1000.0, tp_price=1010.0, sl_price=990.0)
        assert outcome.outcome_type in ("tp", "sl", "timeout")

    def test_label_batch(self):
        df = _make_trending_df(100)
        trades = [
            {
                "entry_idx": i,
                "entry_price": float(df["c"].iloc[i]),
                "tp_price": float(df["c"].iloc[i]) * 1.02,
                "sl_price": float(df["c"].iloc[i]) * 0.98,
                "side": "long",
            }
            for i in range(5, 50, 5)
        ]
        outcomes = self.labeler.label_batch(df, trades)
        assert len(outcomes) == len(trades)
        for o in outcomes:
            assert isinstance(o, TradeOutcome)

    def test_r_multiple_consistent_with_tp(self):
        """When TP is hit, r_multiple should be positive."""
        df = _make_trending_df(100, step=5.0)
        entry_idx = 0
        entry_price = float(df["c"].iloc[entry_idx])
        tp = entry_price * 1.01
        sl = entry_price * 0.99
        outcome = self.labeler.label(df, entry_idx=entry_idx, side="long",
                                     entry_price=entry_price, tp_price=tp, sl_price=sl)
        if outcome.outcome_type == "tp":
            assert outcome.r_multiple > 0
        elif outcome.outcome_type == "sl":
            assert outcome.r_multiple < 0
