"""
Feature engineering for signal quality prediction.
Converts raw market data + signal into a ~30-dim numpy feature vector.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Number of features produced by FeatureExtractor.extract()
# ---------------------------------------------------------------------------
_N_FEATURES = 26

_REGIME_LABELS = ["trend_up", "trend_down", "range", "high_vol", "low_vol", "unknown"]


class FeatureExtractor:
    """Converts OHLCV + signal metadata into a fixed-length feature vector."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        df: pd.DataFrame,
        signal_side: str,
        signal_confidence: float,
        regime: str,
        atr: float,
        fear_greed: int = 50,
    ) -> np.ndarray:
        """Return a (26,) float32 feature vector.

        Parameters
        ----------
        df:
            OHLCV DataFrame with columns t, o, h, l, c, v.
            Must have at least 30 bars for meaningful features; if not,
            returns a zero vector.
        signal_side:
            "long" / "buy" → encoded as 1, anything else → 0.
        signal_confidence:
            Raw confidence value in [0, 1].
        regime:
            String regime label matching one of the known regime classes.
        atr:
            Current ATR(14) value (absolute price units).
        fear_greed:
            Fear & Greed index, integer 0–100 (default 50).
        """
        zeros = np.zeros(_N_FEATURES, dtype=np.float32)

        if len(df) < 30:
            return zeros

        try:
            return self._compute(df, signal_side, signal_confidence, regime, atr, fear_greed)
        except Exception:
            return zeros

    def feature_names(self) -> List[str]:
        """Return feature names in the same order as extract()."""
        names = [
            "rsi_14",
            "adx_14",
            "bb_position",
            "atr_pct",
            "atr_percentile",
            "ema_alignment",
            "vol_z",
            "momentum_5",
            "momentum_10",
            "momentum_20",
            "hl_range_norm",
            "signal_confidence",
            "side_long",
            "fear_greed_norm",
        ]
        for r in _REGIME_LABELS:
            names.append(f"regime_{r}")
        names += ["hour_sin", "hour_cos", "dow_sin", "dow_cos"]
        names += ["close_above_ema50", "ema_sign"]
        return names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute(
        self,
        df: pd.DataFrame,
        signal_side: str,
        signal_confidence: float,
        regime: str,
        atr: float,
        fear_greed: int,
    ) -> np.ndarray:
        closes = df["c"].values.astype(float)
        highs = df["h"].values.astype(float)
        lows = df["l"].values.astype(float)
        volumes = df["v"].values.astype(float)
        close = closes[-1]

        # 1. RSI(14) normalized 0–1
        rsi_val = self._rsi(closes, 14)
        rsi_norm = float(np.clip(rsi_val / 100.0, 0.0, 1.0)) if math.isfinite(rsi_val) else 0.5

        # 2. ADX(14) normalized 0–1 (divide by 50, clip)
        adx_val = self._adx(df, 14)
        adx_norm = float(np.clip(adx_val / 50.0, 0.0, 1.0)) if math.isfinite(adx_val) else 0.0

        # 3. Bollinger Band position: (close - lower) / (upper - lower), clipped 0–1
        bb_up, bb_lo = self._bb(closes, 20, 2.0)
        band_width = bb_up - bb_lo
        if band_width > 0 and math.isfinite(band_width):
            bb_pos = float(np.clip((close - bb_lo) / band_width, 0.0, 1.0))
        else:
            bb_pos = 0.5

        # 4. ATR% = ATR(14) / close
        atr_pct = float(atr / close) if close > 0 and math.isfinite(atr) else 0.0

        # 5. ATR percentile vs rolling 50-bar ATR
        atr_pctile = self._atr_percentile(df, atr, period=14, lookback=50)

        # 6. EMA alignment: (ema20 - ema50) / close, clipped -0.05..0.05, normalized 0–1
        ema20_val = float(df["c"].ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_val = float(df["c"].ewm(span=50, adjust=False).mean().iloc[-1])
        ema_raw = (ema20_val - ema50_val) / close if close > 0 else 0.0
        ema_clipped = float(np.clip(ema_raw, -0.05, 0.05))
        ema_align = (ema_clipped + 0.05) / 0.10  # normalize to [0, 1]

        # 7. Volume z-score: (vol - mean_20) / std_20, clipped -3..3, normalized 0–1
        if len(volumes) >= 20:
            vol_win = volumes[-20:]
            vol_mean = float(np.mean(vol_win))
            vol_std = float(np.std(vol_win, ddof=1)) if len(vol_win) > 1 else 1.0
            vol_std = vol_std if vol_std > 0 else 1.0
            vol_z_raw = float(np.clip((volumes[-1] - vol_mean) / vol_std, -3.0, 3.0))
        else:
            vol_z_raw = 0.0
        vol_z = (vol_z_raw + 3.0) / 6.0  # normalize to [0, 1]

        # 8. Momentum 5-bar ROC, clipped -0.1..0.1, normalized 0–1
        mom5 = self._momentum(closes, 5)
        mom10 = self._momentum(closes, 10)
        mom20 = self._momentum(closes, 20)

        # 11. HL range normalized by ATR
        hl_range = highs[-1] - lows[-1]
        hl_range_norm = float(hl_range / atr) if atr > 0 and math.isfinite(atr) else 1.0
        hl_range_norm = float(np.clip(hl_range_norm, 0.0, 5.0) / 5.0)  # normalize to [0, 1]

        # 12. Signal confidence (raw)
        sig_conf = float(np.clip(signal_confidence, 0.0, 1.0))

        # 13. Side encoding: long/buy → 1, else 0
        side_long = 1.0 if signal_side.lower() in ("long", "buy") else 0.0

        # 14. Fear & Greed normalized
        fg_norm = float(np.clip(fear_greed, 0, 100)) / 100.0

        # 15–20. Regime one-hot: [trend_up, trend_down, range, high_vol, low_vol, unknown]
        regime_lower = regime.lower() if isinstance(regime, str) else "unknown"
        regime_vec = [1.0 if regime_lower == r else 0.0 for r in _REGIME_LABELS]
        # If no match, set unknown=1
        if sum(regime_vec) == 0:
            regime_vec[-1] = 1.0

        # 21–22. UTC hour cyclical encoding
        ts = df["t"].iloc[-1]
        try:
            if isinstance(ts, (int, float)):
                dt = pd.Timestamp(ts, unit="ms", tz="UTC")
            else:
                dt = pd.Timestamp(ts).tz_localize("UTC") if getattr(ts, "tzinfo", None) is None else ts
            hour = dt.hour
            dow = dt.dayofweek
        except Exception:
            hour = 0
            dow = 0

        hour_sin = math.sin(2 * math.pi * hour / 24.0)
        hour_cos = math.cos(2 * math.pi * hour / 24.0)
        dow_sin = math.sin(2 * math.pi * dow / 7.0)
        dow_cos = math.cos(2 * math.pi * dow / 7.0)

        features = [
            rsi_norm,       # 0
            adx_norm,       # 1
            bb_pos,         # 2
            atr_pct,        # 3
            atr_pctile,     # 4
            ema_align,      # 5
            vol_z,          # 6
            mom5,           # 7
            mom10,          # 8
            mom20,          # 9
            hl_range_norm,  # 10
            sig_conf,       # 11
            side_long,      # 12
            fg_norm,        # 13
        ] + regime_vec + [  # 14–19 (6 values)
            hour_sin,       # 20
            hour_cos,       # 21
            dow_sin,        # 22
            dow_cos,        # 23
        ]

        # Should be 14 + 6 + 4 = 24 … but spec says 26.
        # The spec numbers features 1–22 (with hour_sin/cos = 21, dow_sin/cos = 22)
        # giving 14 scalar + 6 regime + 4 cyclical = 24.  However the spec header
        # explicitly says shape (26,) and _N_FEATURES = 26.  We add two extra
        # features to match: atr_percentile is already included (index 4 above)
        # and hl_range_norm (index 10), so the count is correct at 24.
        # Re-count: indices 0-13 = 14, regime 14-19 = 6, cyclical 20-23 = 4 → 24.
        # To reach 26 we include the raw (un-normalized) EMA alignment sign and
        # the close-above-ema50 boolean as two additional binary context features.
        close_above_ema50 = 1.0 if close > ema50_val else 0.0
        ema_sign = 1.0 if ema20_val >= ema50_val else 0.0
        features += [close_above_ema50, ema_sign]  # 24, 25

        arr = np.array(features, dtype=np.float32)
        # Replace any NaN/inf with 0
        arr = np.where(np.isfinite(arr), arr, 0.0).astype(np.float32)
        return arr

    # ------------------------------------------------------------------
    # Indicator helpers
    # ------------------------------------------------------------------

    def _rsi(self, closes: np.ndarray, period: int = 14) -> float:
        """RSI using Wilder's EWM smoothing.  Returns the last value."""
        if len(closes) < period + 1:
            return float("nan")
        delta = np.diff(closes.astype(float))
        gains = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)

        alpha = 1.0 / period
        avg_gain = gains[0]
        avg_loss = losses[0]
        for i in range(1, len(gains)):
            avg_gain = alpha * gains[i] + (1 - alpha) * avg_gain
            avg_loss = alpha * losses[i] + (1 - alpha) * avg_loss

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """ADX value for the last bar using Wilder's smoothing."""
        if len(df) < period * 2:
            return float("nan")
        highs = df["h"].values.astype(float)
        lows = df["l"].values.astype(float)
        closes = df["c"].values.astype(float)

        alpha = 1.0 / period
        tr_list, pdm_list, mdm_list = [], [], []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            pdm = up_move if (up_move > down_move and up_move > 0) else 0.0
            mdm = down_move if (down_move > up_move and down_move > 0) else 0.0
            tr_list.append(tr)
            pdm_list.append(pdm)
            mdm_list.append(mdm)

        # Wilder EWM
        def _ewm(arr: list) -> list:
            out = [arr[0]]
            for v in arr[1:]:
                out.append(alpha * v + (1 - alpha) * out[-1])
            return out

        atr_s = _ewm(tr_list)
        pdm_s = _ewm(pdm_list)
        mdm_s = _ewm(mdm_list)

        dx_list = []
        for a, p, m in zip(atr_s, pdm_s, mdm_s):
            if a == 0:
                dx_list.append(0.0)
                continue
            pdi = 100.0 * p / a
            mdi = 100.0 * m / a
            denom = pdi + mdi
            dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

        adx_s = _ewm(dx_list)
        return float(adx_s[-1])

    def _bb(self, closes: np.ndarray, period: int = 20, std: float = 2.0) -> tuple[float, float]:
        """Bollinger Band upper and lower for the last bar.  Returns (upper, lower)."""
        if len(closes) < period:
            return (float("nan"), float("nan"))
        window = closes[-period:].astype(float)
        mid = float(np.mean(window))
        sigma = float(np.std(window, ddof=1))
        return (mid + std * sigma, mid - std * sigma)

    def _momentum(self, closes: np.ndarray, n: int) -> float:
        """N-bar ROC normalized to [0, 1] (clipped at ±10%)."""
        if len(closes) <= n:
            return 0.5
        ref = closes[-(n + 1)]
        if ref == 0:
            return 0.5
        roc = (closes[-1] - ref) / ref
        clipped = float(np.clip(roc, -0.1, 0.1))
        return (clipped + 0.1) / 0.2  # normalize to [0, 1]

    def _atr_percentile(
        self, df: pd.DataFrame, current_atr: float, period: int = 14, lookback: int = 50
    ) -> float:
        """Percentile of current ATR vs last `lookback` ATR values."""
        if len(df) < period + lookback:
            return 0.5
        highs = df["h"].values.astype(float)
        lows = df["l"].values.astype(float)
        closes = df["c"].values.astype(float)

        trs = []
        for i in range(1, len(closes)):
            prev_c = closes[i - 1]
            tr = max(highs[i] - lows[i], abs(highs[i] - prev_c), abs(lows[i] - prev_c))
            trs.append(tr)

        alpha = 1.0 / period
        atr_hist: list[float] = []
        atr_val = float(np.mean(trs[:period])) if len(trs) >= period else trs[0] if trs else 0.0
        for i in range(period, len(trs)):
            atr_val = alpha * trs[i] + (1 - alpha) * atr_val
            atr_hist.append(atr_val)

        if not atr_hist:
            return 0.5
        window = atr_hist[-lookback:]
        below = sum(1 for v in window if v <= current_atr)
        return float(below) / len(window)
