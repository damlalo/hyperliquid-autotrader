"""
Support and resistance level detection via fractal pivots, volume clusters,
and swing highs/lows. Used by strategies to anchor TP/stop targets at
structurally significant prices rather than fixed ATR multiples.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_MIN_BARS = 50


@dataclass(frozen=True)
class Level:
    price: float
    strength: float  # 0.0–1.0 composite score
    kind: str  # "support" | "resistance" | "both"
    touches: int
    last_touch_bar: int


class LevelDetector:
    """Detects support and resistance levels from OHLCV data.

    Combines three independent detection methods:
      1. Williams fractal pivots
      2. High-volume price clusters
      3. Rolling swing highs/lows

    All raw price candidates are merged, clustered to remove near-duplicates,
    and ranked by a composite strength score.

    Expected DataFrame columns: ``o``, ``h``, ``l``, ``c``, ``v``
    (open, high, low, close, volume — all float).
    """

    # ---------------------------------------------------------------------------
    # Public interface
    # ---------------------------------------------------------------------------

    def detect(self, df: pd.DataFrame, n_levels: int = 10) -> list[Level]:
        """Run all detection methods and return the top *n_levels* by strength.

        Returns an empty list when fewer than ``_MIN_BARS`` rows are present.
        """
        if len(df) < _MIN_BARS:
            log.debug("detect: only %d bars, need %d — returning []", len(df), _MIN_BARS)
            return []

        raw: list[float] = []
        try:
            raw.extend(self._fractal_pivots(df))
        except Exception:
            log.exception("_fractal_pivots failed")
        try:
            raw.extend(self._volume_clusters(df))
        except Exception:
            log.exception("_volume_clusters failed")
        try:
            raw.extend(self._swing_levels(df))
        except Exception:
            log.exception("_swing_levels failed")

        if not raw:
            return []

        return self._cluster_and_rank(raw, df, n_levels)

    # ---------------------------------------------------------------------------
    # Detection helpers
    # ---------------------------------------------------------------------------

    def _fractal_pivots(self, df: pd.DataFrame, n: int = 2) -> list[float]:
        """Williams fractal highs and lows.

        A fractal *high* at bar *i* iff ``df['h'].iloc[i]`` is strictly greater
        than the *n* bars on each side.  Analogously for fractal lows with
        ``df['l']``.  Edge bars (first/last *n*) are skipped.
        """
        highs = df["h"].to_numpy(dtype=float)
        lows = df["l"].to_numpy(dtype=float)
        pivots: list[float] = []

        for i in range(n, len(df) - n):
            # Fractal high
            window_h = np.concatenate([highs[i - n : i], highs[i + 1 : i + n + 1]])
            if highs[i] > window_h.max():
                pivots.append(float(highs[i]))
            # Fractal low
            window_l = np.concatenate([lows[i - n : i], lows[i + 1 : i + n + 1]])
            if lows[i] < window_l.min():
                pivots.append(float(lows[i]))

        return pivots

    def _volume_clusters(self, df: pd.DataFrame, n_buckets: int = 30) -> list[float]:
        """High-volume price nodes via equal-width price histogram.

        Divides the full price range into *n_buckets* bins, accumulates bar
        volume into each bin using the typical price, and returns the 5 bin
        midpoints with the highest accumulated volume.
        """
        lo = df["l"].min()
        hi = df["h"].max()
        if hi <= lo:
            return []

        typical = ((df["h"] + df["l"] + df["c"]) / 3.0).to_numpy(dtype=float)
        volume = df["v"].to_numpy(dtype=float)

        bins = np.linspace(lo, hi, n_buckets + 1)
        bin_vol = np.zeros(n_buckets, dtype=float)

        indices = np.searchsorted(bins[1:], typical, side="right")
        indices = np.clip(indices, 0, n_buckets - 1)

        np.add.at(bin_vol, indices, volume)

        top5_idx = np.argsort(bin_vol)[-5:][::-1]
        midpoints = [(bins[i] + bins[i + 1]) / 2.0 for i in top5_idx]
        return midpoints

    def _swing_levels(self, df: pd.DataFrame, lookback: int = 20) -> list[float]:
        """Rolling *lookback*-bar highs and lows, sampled every 5 bars."""
        roll_high = df["h"].rolling(lookback).max()
        roll_low = df["l"].rolling(lookback).min()

        sampled_high = roll_high.iloc[lookback - 1 :: 5].dropna()
        sampled_low = roll_low.iloc[lookback - 1 :: 5].dropna()

        prices = set(round(float(p), 10) for p in sampled_high)
        prices.update(round(float(p), 10) for p in sampled_low)
        return list(prices)

    # ---------------------------------------------------------------------------
    # Clustering and ranking
    # ---------------------------------------------------------------------------

    def _cluster_and_rank(
        self,
        raw_prices: list[float],
        df: pd.DataFrame,
        n_levels: int,
    ) -> list[Level]:
        """Merge nearby prices and score each cluster.

        Steps:
        1. Sort prices ascending.
        2. Merge prices within 0.4 % of each other → representative = median.
        3. For each representative count *touches*: bars where
           ``|close - price| / price < 0.3 %``.
        4. Classify kind from touch direction.
        5. Score and return top *n_levels*.
        """
        if not raw_prices:
            return []

        sorted_prices = sorted(raw_prices)
        clusters: list[list[float]] = []
        current_cluster: list[float] = [sorted_prices[0]]

        for p in sorted_prices[1:]:
            ref = current_cluster[-1]
            if abs(p - ref) / ref <= 0.004:  # 0.4 % tolerance
                current_cluster.append(p)
            else:
                clusters.append(current_cluster)
                current_cluster = [p]
        clusters.append(current_cluster)

        closes = df["c"].to_numpy(dtype=float)
        volumes = df["v"].to_numpy(dtype=float)
        mean_vol = volumes.mean() if volumes.mean() > 0 else 1.0

        levels: list[Level] = []
        for cluster in clusters:
            level_price = float(np.median(cluster))

            # Touch detection: |close - level| / level < 0.3 %
            rel_dist = np.abs(closes - level_price) / level_price
            touch_mask = rel_dist < 0.003
            touches = int(touch_mask.sum())

            if touches == 0:
                # Still include the level but with minimal score
                level = Level(
                    price=level_price,
                    strength=0.01,
                    kind="both",
                    touches=0,
                    last_touch_bar=-1,
                )
                levels.append(level)
                continue

            touch_indices = np.where(touch_mask)[0]
            last_touch_bar = int(touch_indices[-1])

            # Direction classification
            # "below close" → the candle closed below the level → close < level_price
            from_below = (closes[touch_mask] < level_price).sum()
            from_above = (closes[touch_mask] >= level_price).sum()
            ratio_below = from_below / touches

            if ratio_below > 0.60:
                kind = "resistance"
            elif ratio_below < 0.40:
                kind = "support"
            else:
                kind = "both"

            # Volume weight: sum of touch-bar volumes / mean, normalised 0–1
            touch_vol_sum = float(volumes[touch_mask].sum())
            raw_vol_weight = touch_vol_sum / mean_vol
            # Normalise: cap at 10× mean as "full weight"
            volume_weight = min(raw_vol_weight / 10.0, 1.0)

            strength = min(touches / 8.0, 1.0) * (1.0 + volume_weight)
            # Remap so max possible (1.0 * 2.0) → 1.0
            strength = min(strength / 2.0, 1.0)

            levels.append(
                Level(
                    price=level_price,
                    strength=round(strength, 6),
                    kind=kind,
                    touches=touches,
                    last_touch_bar=last_touch_bar,
                )
            )

        levels.sort(key=lambda lv: lv.strength, reverse=True)
        return levels[:n_levels]

    # ---------------------------------------------------------------------------
    # Utility functions (module-level wrappers also available below)
    # ---------------------------------------------------------------------------

    @staticmethod
    def nearest_support(
        price: float,
        levels: list[Level],
        max_dist_pct: float = 0.05,
    ) -> Level | None:
        """Return the strongest support level below *price* within *max_dist_pct*.

        Only levels classified as ``"support"`` or ``"both"`` are considered.
        """
        candidates = [
            lv
            for lv in levels
            if lv.price < price
            and (price - lv.price) / price <= max_dist_pct
            and lv.kind in ("support", "both")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda lv: lv.strength)

    @staticmethod
    def nearest_resistance(
        price: float,
        levels: list[Level],
        max_dist_pct: float = 0.05,
    ) -> Level | None:
        """Return the strongest resistance level above *price* within *max_dist_pct*.

        Only levels classified as ``"resistance"`` or ``"both"`` are considered.
        """
        candidates = [
            lv
            for lv in levels
            if lv.price > price
            and (lv.price - price) / price <= max_dist_pct
            and lv.kind in ("resistance", "both")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda lv: lv.strength)

    @staticmethod
    def levels_between(
        low: float,
        high: float,
        levels: list[Level],
    ) -> list[Level]:
        """Return all levels with price in the open interval (*low*, *high*),
        sorted by price ascending."""
        return sorted(
            [lv for lv in levels if low < lv.price < high],
            key=lambda lv: lv.price,
        )

    @staticmethod
    def atr_distance(level: Level, current_price: float, atr: float) -> float:
        """Distance from *current_price* to *level.price* expressed in ATR units."""
        if atr <= 0:
            raise ValueError(f"atr must be positive, got {atr}")
        return abs(current_price - level.price) / atr


# ---------------------------------------------------------------------------
# Module-level convenience wrappers (strategy code can import these directly)
# ---------------------------------------------------------------------------

_default_detector = LevelDetector()


def nearest_support(
    price: float,
    levels: list[Level],
    max_dist_pct: float = 0.05,
) -> Level | None:
    """Module-level wrapper for :meth:`LevelDetector.nearest_support`."""
    return LevelDetector.nearest_support(price, levels, max_dist_pct)


def nearest_resistance(
    price: float,
    levels: list[Level],
    max_dist_pct: float = 0.05,
) -> Level | None:
    """Module-level wrapper for :meth:`LevelDetector.nearest_resistance`."""
    return LevelDetector.nearest_resistance(price, levels, max_dist_pct)


def levels_between(low: float, high: float, levels: list[Level]) -> list[Level]:
    """Module-level wrapper for :meth:`LevelDetector.levels_between`."""
    return LevelDetector.levels_between(low, high, levels)


def atr_distance(level: Level, current_price: float, atr: float) -> float:
    """Module-level wrapper for :meth:`LevelDetector.atr_distance`."""
    return LevelDetector.atr_distance(level, current_price, atr)
