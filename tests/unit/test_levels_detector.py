"""Unit tests for LevelDetector — S/R level detection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrader.levels.detector import Level, LevelDetector


def _make_df(n: int = 200) -> pd.DataFrame:
    """Synthetic OHLCV with repeating highs/lows to create detectable levels.

    Uses short column names (o, h, l, c, v) as expected by LevelDetector.
    """
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.normal(0, 50, n)) + 50_000
    high = close + rng.uniform(20, 200, n)
    low = close - rng.uniform(20, 200, n)
    # Plant obvious price levels at 50_200 and 49_800
    for i in range(0, n, 30):
        high[i] = 50_200
        low[i] = 49_800
    return pd.DataFrame({"o": close, "h": high, "l": low, "c": close, "v": np.ones(n) * 1000})


class TestLevelDetector:
    def setup_method(self):
        self.df = _make_df()
        self.det = LevelDetector()

    def test_detect_returns_list(self):
        levels = self.det.detect(self.df)
        assert isinstance(levels, list)
        assert len(levels) > 0

    def test_level_fields(self):
        levels = self.det.detect(self.df)
        lvl = levels[0]
        assert isinstance(lvl, Level)
        assert 0.0 <= lvl.strength <= 1.0
        assert lvl.kind in ("support", "resistance", "both")
        assert lvl.price > 0
        assert lvl.touches >= 1

    def test_sorted_by_strength(self):
        levels = self.det.detect(self.df)
        strengths = [lv.strength for lv in levels]
        assert strengths == sorted(strengths, reverse=True)

    def test_nearest_support(self):
        levels = self.det.detect(self.df)
        price = float(self.df["c"].iloc[-1])
        sup = LevelDetector.nearest_support(price, levels, max_dist_pct=0.10)
        if sup is not None:
            assert sup.price <= price

    def test_nearest_resistance(self):
        levels = self.det.detect(self.df)
        price = float(self.df["c"].iloc[-1])
        res = LevelDetector.nearest_resistance(price, levels, max_dist_pct=0.10)
        if res is not None:
            assert res.price >= price

    def test_levels_between(self):
        levels = self.det.detect(self.df)
        price = float(self.df["c"].iloc[-1])
        lo, hi = price * 0.98, price * 1.02
        between = LevelDetector.levels_between(lo, hi, levels)
        for lv in between:
            assert lo < lv.price < hi

    def test_atr_distance(self):
        levels = self.det.detect(self.df)
        if not levels:
            return
        price = float(self.df["c"].iloc[-1])
        dist = LevelDetector.atr_distance(levels[0], current_price=price, atr=100.0)
        assert dist >= 0.0

    def test_short_df_no_crash(self):
        short = self.df.head(10)
        levels = self.det.detect(short)
        assert isinstance(levels, list)

    def test_empty_df_no_crash(self):
        empty = pd.DataFrame(columns=["o", "h", "l", "c", "v"])
        levels = self.det.detect(empty)
        assert levels == []
