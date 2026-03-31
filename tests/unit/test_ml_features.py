"""Unit tests for ML FeatureExtractor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrader.ml.features import FeatureExtractor


def _make_df(n: int = 100) -> pd.DataFrame:
    """Short-name OHLCV as expected by FeatureExtractor (t, o, h, l, c, v)."""
    rng = np.random.default_rng(0)
    close = np.cumsum(rng.normal(0, 10, n)) + 1000
    high = close + rng.uniform(1, 20, n)
    low = close - rng.uniform(1, 20, n)
    volume = rng.uniform(100, 1000, n)
    # t = unix millisecond timestamps, one per minute
    t = np.arange(1_700_000_000_000, 1_700_000_000_000 + n * 60_000, 60_000)
    return pd.DataFrame({"t": t, "o": close, "h": high, "l": low, "c": close, "v": volume})


_FE = FeatureExtractor()
_FEATURE_NAMES = _FE.feature_names()


class TestFeatureExtractor:
    def setup_method(self):
        self.df = _make_df(100)
        self.fe = FeatureExtractor()
        self.atr = 15.0

    def _extract(self, df=None, side="long", conf=0.7, regime="trend_up", fg=50):
        return self.fe.extract(
            df if df is not None else self.df,
            signal_side=side,
            signal_confidence=conf,
            regime=regime,
            atr=self.atr,
            fear_greed=fg,
        )

    def test_output_shape(self):
        feats = self._extract()
        assert feats.shape == (len(_FEATURE_NAMES),)
        assert feats.dtype == np.float32

    def test_feature_names_count(self):
        assert len(_FEATURE_NAMES) == 26

    def test_no_nans_on_valid_input(self):
        feats = self._extract()
        assert not np.any(np.isnan(feats)), "NaN values in features"

    def test_zero_vector_on_short_df(self):
        short_df = _make_df(25)
        feats = self._extract(df=short_df)
        assert np.all(feats == 0.0)

    def test_signal_meta_injection(self):
        feats = self._extract(conf=0.75)
        idx = _FEATURE_NAMES.index("signal_confidence")
        assert abs(feats[idx] - 0.75) < 1e-5

    def test_regime_onehot_sum_one(self):
        """Only one regime flag should be set."""
        feats = self._extract(regime="range")
        regime_names = [n for n in _FEATURE_NAMES if n.startswith("regime_")]
        idxs = [_FEATURE_NAMES.index(r) for r in regime_names]
        total = sum(feats[i] for i in idxs)
        assert abs(total - 1.0) < 1e-5

    def test_deterministic(self):
        a = self._extract()
        b = self._extract()
        np.testing.assert_array_equal(a, b)

    def test_side_encoding(self):
        feats_long = self._extract(side="long")
        feats_short = self._extract(side="short")
        idx = _FEATURE_NAMES.index("side_long")
        assert feats_long[idx] == pytest.approx(1.0)
        assert feats_short[idx] == pytest.approx(0.0)
