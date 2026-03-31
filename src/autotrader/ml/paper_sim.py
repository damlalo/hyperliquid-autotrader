"""
Paper trading simulator. Runs all strategies on historical OHLCV data
to generate (features, labels) training pairs.

This is the cold-start mechanism — before live trades accumulate,
the model trains on simulated paper trades.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from autotrader.ml.features import FeatureExtractor
from autotrader.ml.labeler import TradeLabeler

logger = logging.getLogger(__name__)

# Minimum ATR value to guard against zero-division on flat synthetic data
_MIN_ATR = 1e-8

# Bars to skip after recording a trade to avoid overlapping positions
_SKIP_BARS = 4


class PaperSimulator:
    """Runs strategies over historical OHLCV to produce (features, label) pairs.

    The simulator replays bars chronologically, computing signals exactly as
    the live bot would (using only past data at each bar), then labels each
    simulated trade with its actual outcome.
    """

    def __init__(self, store: Any, config: dict) -> None:
        """
        Parameters
        ----------
        store:
            DataStore instance (or any object with a ``read_candles`` method).
        config:
            Bot configuration dict (passed through to strategies).
        """
        self._store = store
        self._config = config
        self._extractor = FeatureExtractor()
        self._labeler = TradeLabeler()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        coins: List[str],
        intervals: List[str],
        lookback_days: int = 90,
    ) -> pd.DataFrame:
        """Simulate paper trades for all coins and return a DataFrame.

        Columns: features (list), label (int), r_multiple (float),
                 coin (str), strategy (str), timestamp (int).
        """
        import time

        lookback_ms = int(lookback_days * 24 * 3600 * 1000)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_ms

        all_rows: List[dict] = []

        for coin in coins:
            candles_dict: Dict[str, pd.DataFrame] = {}
            for interval in intervals:
                try:
                    df = self._store.read_candles(coin, interval, start_ms=start_ms)
                    if df is not None and len(df) >= 200:
                        candles_dict[interval] = df.reset_index(drop=True)
                    else:
                        logger.debug(
                            "PaperSimulator: skipping %s %s — only %d bars",
                            coin,
                            interval,
                            len(df) if df is not None else 0,
                        )
                except Exception as exc:
                    logger.warning("PaperSimulator: error loading %s %s: %s", coin, interval, exc)

            if not candles_dict:
                continue

            try:
                rows = self._simulate_coin(coin, candles_dict)
                all_rows.extend(rows)
                logger.info(
                    "PaperSimulator: %s → %d simulated trades", coin, len(rows)
                )
            except Exception as exc:
                logger.error("PaperSimulator: error simulating %s: %s", coin, exc, exc_info=True)

        if not all_rows:
            return pd.DataFrame(
                columns=["features", "label", "r_multiple", "coin", "strategy", "timestamp"]
            )
        return pd.DataFrame(all_rows)

    def _simulate_coin(
        self,
        coin: str,
        candles: Dict[str, pd.DataFrame],
    ) -> List[dict]:
        """Walk bars for one coin and return a list of trade dicts."""
        # Late imports to avoid circular dependencies
        from autotrader.strategies.trend_breakout import TrendBreakoutStrategy
        from autotrader.strategies.range_meanrev import RangeMeanRevStrategy
        from autotrader.strategies.vol_expansion import VolExpansionStrategy
        from autotrader.strategies.funding_extremes import FundingExtremesStrategy
        from autotrader.regimes.classifier import RegimeClassifier
        from autotrader.regimes.hysteresis import HysteresisFilter
        from autotrader.hl.types import MarketContext
        from autotrader.features.technical import atr as compute_atr

        strategies = [
            TrendBreakoutStrategy(),
            RangeMeanRevStrategy(),
            VolExpansionStrategy(),
            FundingExtremesStrategy(),
        ]
        classifier = RegimeClassifier()
        hysteresis = HysteresisFilter()

        # Use the first available interval as the primary timeframe for bar walking
        primary_interval = _pick_primary_interval(list(candles.keys()))
        df_primary = candles[primary_interval]
        n_bars = len(df_primary)

        results: List[dict] = []
        skip_until: int = 0  # bar index after which we can enter again

        for i in range(100, n_bars):
            if i < skip_until:
                continue

            # Build sub-DataFrames using only data up to bar i
            sub_candles: Dict[str, pd.DataFrame] = {
                iv: df.iloc[: i + 1].copy() for iv, df in candles.items()
            }
            sub_primary = sub_candles[primary_interval]

            # Classify regime
            try:
                regime_result = classifier.classify(sub_candles.get("1h", sub_primary))
                regime = hysteresis.update(regime_result)
                regime_str = regime.value
            except Exception:
                regime_str = "unknown"

            # Compute ATR on primary
            try:
                atr_series = compute_atr(sub_primary, 14)
                atr_val = float(atr_series.iloc[-1])
                if not (atr_val > 0):
                    atr_val = _MIN_ATR
            except Exception:
                atr_val = _MIN_ATR

            # Build MarketContext
            ctx = MarketContext(
                coin=coin,
                candles=sub_candles,
                timestamp=int(sub_primary["t"].iloc[-1]) if "t" in sub_primary.columns else 0,
            )
            # Attach regime so ensemble/strategies can read it
            ctx.regime = regime_str  # type: ignore[attr-defined]

            for strategy in strategies:
                # Only run strategy if regime is applicable (or we have no info)
                applicable = strategy.applicable_regimes()
                if applicable and regime_str not in applicable:
                    continue

                try:
                    signal = strategy.compute_signal(ctx)
                except Exception as exc:
                    logger.debug(
                        "PaperSimulator: %s.compute_signal failed on %s bar %d: %s",
                        strategy.name, coin, i, exc,
                    )
                    continue

                if signal.side == "flat":
                    continue
                if signal.entry is None or signal.stop is None or signal.take_profit is None:
                    continue

                # Extract features
                try:
                    feat = self._extractor.extract(
                        df=sub_primary,
                        signal_side=signal.side,
                        signal_confidence=signal.confidence,
                        regime=regime_str,
                        atr=atr_val,
                    )
                except Exception as exc:
                    logger.debug("PaperSimulator: feature extraction failed: %s", exc)
                    continue

                # Label trade using forward simulation from bar i in primary df
                try:
                    outcome = self._labeler.label(
                        df=df_primary,
                        entry_idx=i,
                        side=signal.side,
                        entry_price=signal.entry,
                        tp_price=signal.take_profit,
                        sl_price=signal.stop,
                        max_bars=48,
                    )
                except Exception as exc:
                    logger.debug("PaperSimulator: labeling failed: %s", exc)
                    continue

                ts = int(sub_primary["t"].iloc[-1]) if "t" in sub_primary.columns else 0
                results.append(
                    {
                        "features": feat.tolist(),
                        "label": int(outcome.won),
                        "r_multiple": float(outcome.r_multiple),
                        "coin": coin,
                        "strategy": strategy.name,
                        "timestamp": ts,
                    }
                )

                # Skip next few bars to avoid overlapping trades
                skip_until = i + _SKIP_BARS
                break  # one trade per bar across all strategies

        return results

    # ------------------------------------------------------------------
    # Training data generation
    # ------------------------------------------------------------------

    async def generate_training_data(
        self,
        coins: List[str],
        intervals: List[str],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run simulation and return (X, y) arrays.

        Returns
        -------
        X: np.ndarray of shape (N, 26)
        y: np.ndarray of shape (N,)
        """
        df = await self.run(coins, intervals)

        if df.empty:
            logger.warning("PaperSimulator.generate_training_data: no training samples produced.")
            return np.empty((0, 26), dtype=np.float32), np.empty(0, dtype=np.int32)

        X_rows = []
        y_rows = []
        for _, row in df.iterrows():
            feat = row["features"]
            if isinstance(feat, list):
                feat = np.array(feat, dtype=np.float32)
            X_rows.append(feat)
            y_rows.append(int(row["label"]))

        X = np.stack(X_rows, axis=0).astype(np.float32)
        y = np.array(y_rows, dtype=np.int32)

        logger.info(
            "PaperSimulator.generate_training_data: %d samples, win_rate=%.1f%%",
            len(y),
            100.0 * float(y.mean()) if len(y) > 0 else 0.0,
        )
        return X, y


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_primary_interval(intervals: List[str]) -> str:
    """Choose the most granular available interval as the primary walk-through."""
    priority = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    for iv in priority:
        if iv in intervals:
            return iv
    return intervals[0]
