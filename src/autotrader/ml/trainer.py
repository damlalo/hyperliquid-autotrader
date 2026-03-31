"""
Model training orchestrator. Manages the full lifecycle:
1. Cold-start: train from paper simulation on historical data
2. Incremental: retrain when N new live trades accumulate
3. Scheduled: nightly retraining at 00:00 UTC

Persists training data to ~/.autotrader/ml/{account}/training_data.parquet
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from autotrader.ml.model import SignalQualityModel
from autotrader.ml.paper_sim import PaperSimulator

logger = logging.getLogger(__name__)


class ModelTrainer:
    """Orchestrates training lifecycle for the SignalQualityModel.

    Responsibilities
    ----------------
    - Cold-start: run paper simulation on historical data and fit the model
      when no model exists yet.
    - Incremental retrain: accumulate live trade outcomes; retrain when
      ``_retrain_every`` new samples have been collected.
    - Nightly retrain: scheduled daily at 00:00 UTC; forces a full
      re-simulation + refit.
    """

    def __init__(
        self,
        model: SignalQualityModel,
        simulator: PaperSimulator,
        account_safe: str = "default",
    ) -> None:
        self._model = model
        self._simulator = simulator
        self._account_safe = account_safe

        base = Path(os.path.expanduser("~/.autotrader")) / "ml" / account_safe
        self._data_path: Path = base / "training_data.parquet"

        self._pending_trades: List[dict] = []
        self._retrain_every: int = 50

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ml-trainer")

        # Load existing training data if present
        self._training_df: Optional[pd.DataFrame] = self._load_training_data()

    # ------------------------------------------------------------------
    # Cold start
    # ------------------------------------------------------------------

    async def cold_start(self, coins: List[str], intervals: List[str]) -> None:
        """Train from paper simulation unless model is already trained.

        Skips if the model is trained AND the training data file already
        has more than 200 rows (assumes sufficient historical coverage).
        """
        already_trained = (
            self._model.is_trained()
            and self._training_df is not None
            and len(self._training_df) > 200
        )
        if already_trained:
            logger.info(
                "ModelTrainer.cold_start: model already trained on %d samples. Skipping.",
                len(self._training_df),
            )
            return

        logger.info("ModelTrainer.cold_start: running paper simulation for %d coins…", len(coins))
        try:
            X, y = await self._simulator.generate_training_data(coins, intervals)
        except Exception as exc:
            logger.error("ModelTrainer.cold_start: simulation failed: %s", exc, exc_info=True)
            return

        if len(X) < 80:
            logger.warning(
                "ModelTrainer.cold_start: only %d samples produced (need 80). "
                "Try increasing lookback_days or adding more coins.",
                len(X),
            )
            return

        feature_names = self._simulator._extractor.feature_names()
        self._model.fit(X, y, feature_names=feature_names)

        # Persist training data
        try:
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._save_arrays_as_parquet(X, y),
            )
        except Exception as exc:
            logger.warning("ModelTrainer.cold_start: could not save training data: %s", exc)

    # ------------------------------------------------------------------
    # Incremental updates from live trades
    # ------------------------------------------------------------------

    def record_trade_outcome(
        self,
        features: np.ndarray,
        label: int,
        r_multiple: float,
        coin: str,
    ) -> None:
        """Record one live trade outcome and trigger retrain if threshold is met.

        Parameters
        ----------
        features:
            Feature vector for the trade.
        label:
            1 if trade won, 0 if lost.
        r_multiple:
            Realized R-multiple of the trade.
        coin:
            Instrument name (for bookkeeping).
        """
        self._pending_trades.append(
            {
                "features": features.tolist(),
                "label": int(label),
                "r_multiple": float(r_multiple),
                "coin": coin,
                "timestamp": int(
                    datetime.now(tz=timezone.utc).timestamp() * 1000
                ),
            }
        )

        if len(self._pending_trades) >= self._retrain_every:
            self._executor.submit(self._do_incremental_retrain)

    def _do_incremental_retrain(self) -> None:
        """Blocking method: merge pending trades into training data and retrain."""
        if not self._pending_trades:
            return

        # Snapshot and clear pending list (under GIL — safe for CPython)
        pending = list(self._pending_trades)
        self._pending_trades.clear()

        logger.info(
            "ModelTrainer._do_incremental_retrain: appending %d new trade(s).", len(pending)
        )

        try:
            new_df = pd.DataFrame(pending)

            # Merge with existing training data
            if self._training_df is not None and not self._training_df.empty:
                combined = pd.concat([self._training_df, new_df], ignore_index=True)
            else:
                combined = new_df

            self._training_df = combined
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(self._data_path, index=False)

            # Rebuild X, y from full dataset
            X, y = self._df_to_arrays(combined)
            if len(X) >= self._model._min_samples:
                feature_names = self._simulator._extractor.feature_names()
                self._model.fit(X, y, feature_names=feature_names)
            else:
                logger.info(
                    "ModelTrainer._do_incremental_retrain: "
                    "only %d samples; skipping fit until more data accumulates.",
                    len(X),
                )
        except Exception as exc:
            logger.error(
                "ModelTrainer._do_incremental_retrain: failed: %s", exc, exc_info=True
            )

    # ------------------------------------------------------------------
    # Nightly scheduled retrain
    # ------------------------------------------------------------------

    async def schedule_nightly_retrain(
        self,
        coins: List[str],
        intervals: List[str],
    ) -> None:
        """Coroutine that triggers a full retrain every day at 00:00 UTC.

        Runs indefinitely; wrap in a Task and cancel on shutdown.
        """
        while True:
            seconds_until_midnight = _seconds_until_utc_midnight()
            logger.info(
                "ModelTrainer.schedule_nightly_retrain: next retrain in %.0f s (%.1f h).",
                seconds_until_midnight,
                seconds_until_midnight / 3600.0,
            )
            try:
                await asyncio.sleep(seconds_until_midnight)
            except asyncio.CancelledError:
                logger.info("ModelTrainer.schedule_nightly_retrain: cancelled.")
                return

            logger.info("ModelTrainer.schedule_nightly_retrain: starting nightly retrain.")
            try:
                # Force retrain by clearing training data record
                self._training_df = None
                await self.cold_start(coins, intervals)
            except Exception as exc:
                logger.error(
                    "ModelTrainer.schedule_nightly_retrain: retrain error: %s", exc, exc_info=True
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_training_data(self) -> Optional[pd.DataFrame]:
        """Load persisted training data from parquet, if it exists."""
        if not self._data_path.exists():
            return None
        try:
            df = pd.read_parquet(self._data_path)
            logger.info(
                "ModelTrainer: loaded %d existing training rows from %s.",
                len(df),
                self._data_path,
            )
            return df
        except Exception as exc:
            logger.warning("ModelTrainer: could not load training data: %s", exc)
            return None

    def _save_arrays_as_parquet(self, X: np.ndarray, y: np.ndarray) -> None:
        """Persist X / y arrays as a parquet file."""
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for feat, label in zip(X, y):
            rows.append({"features": feat.tolist(), "label": int(label), "r_multiple": float("nan"), "coin": "", "timestamp": 0})
        df = pd.DataFrame(rows)
        df.to_parquet(self._data_path, index=False)
        self._training_df = df
        logger.info(
            "ModelTrainer: saved %d training rows to %s.", len(df), self._data_path
        )

    @staticmethod
    def _df_to_arrays(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Convert a training DataFrame back into (X, y) arrays."""
        X_rows = []
        y_rows = []
        for _, row in df.iterrows():
            feat = row["features"]
            if isinstance(feat, list):
                feat = np.array(feat, dtype=np.float32)
            elif not isinstance(feat, np.ndarray):
                feat = np.array(list(feat), dtype=np.float32)
            X_rows.append(feat.astype(np.float32))
            y_rows.append(int(row["label"]))

        if not X_rows:
            return np.empty((0, 26), dtype=np.float32), np.empty(0, dtype=np.int32)

        X = np.stack(X_rows, axis=0)
        y = np.array(y_rows, dtype=np.int32)
        return X, y


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _seconds_until_utc_midnight() -> float:
    """Return seconds until the next 00:00:00 UTC."""
    now = datetime.now(tz=timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Advance by one day
    from datetime import timedelta
    tomorrow = tomorrow + timedelta(days=1)
    delta = (tomorrow - now).total_seconds()
    return max(delta, 1.0)
