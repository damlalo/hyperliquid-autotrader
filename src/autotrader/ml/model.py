"""
LightGBM-based signal quality model. Predicts P(trade wins) given features.
Stored per-account at ~/.autotrader/ml/{account_safe}/signal_quality.lgbm
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class SignalQualityModel:
    """Predicts P(win) for a trade signal given a feature vector.

    Model files are stored under::

        ~/.autotrader/ml/{account_safe}/signal_quality.lgbm
        ~/.autotrader/ml/{account_safe}/meta.json

    LightGBM is a soft dependency — if it is not installed the model silently
    falls back to neutral predictions (0.5) and ``is_trained()`` returns False.
    """

    def __init__(self, account_safe: str = "default") -> None:
        self._account = account_safe

        base = Path(os.path.expanduser("~/.autotrader")) / "ml" / account_safe
        self._model_path: Path = base / "signal_quality.lgbm"
        self._meta_path: Path = base / "meta.json"

        self._model: Optional[Any] = None
        self._trained_on: int = 0
        self._feature_names: List[str] = []
        self._min_samples: int = 80

        # Attempt to load an existing persisted model
        try:
            self.load()
        except Exception as exc:
            logger.debug("SignalQualityModel: could not load existing model: %s", exc)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def is_trained(self) -> bool:
        """Return True if a fitted model is available."""
        return self._model is not None

    def predict(self, features: np.ndarray) -> float:
        """Return P(win) in [0.0, 1.0].

        Returns 0.5 (neutral) when no model is trained.
        """
        if not self.is_trained():
            return 0.5
        try:
            prob = float(self._model.predict_proba([features])[0][1])
            return float(np.clip(prob, 0.0, 1.0))
        except Exception as exc:
            logger.warning("SignalQualityModel.predict failed: %s", exc)
            return 0.5

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
    ) -> None:
        """Train (or retrain) the LightGBM classifier on (X, y).

        Parameters
        ----------
        X:
            Feature matrix of shape (N, n_features).
        y:
            Binary label array of shape (N,).  1 = win, 0 = loss.
        feature_names:
            Optional list of feature names (for inspection).
        """
        if len(X) < self._min_samples:
            logger.warning(
                "SignalQualityModel.fit: only %d samples (need %d). Skipping.",
                len(X),
                self._min_samples,
            )
            return

        try:
            import lightgbm as lgb  # type: ignore[import]
        except ImportError:
            logger.warning(
                "lightgbm is not installed. "
                "Install it with `pip install lightgbm` to enable ML signal filtering."
            )
            return

        clf = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=6,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )

        try:
            clf.fit(X, y)
        except Exception as exc:
            logger.error("SignalQualityModel.fit: LightGBM training failed: %s", exc)
            return

        self._model = clf
        self._trained_on = len(X)
        self._feature_names = feature_names or []

        try:
            self.save()
        except Exception as exc:
            logger.warning("SignalQualityModel.fit: could not save model: %s", exc)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist model and metadata to disk."""
        self._model_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            import joblib  # type: ignore[import]
        except ImportError:
            logger.warning("joblib is not installed; cannot save model.")
            return

        joblib.dump(self._model, self._model_path)

        meta = {
            "trained_on": self._trained_on,
            "feature_names": self._feature_names,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        with open(self._meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)

        logger.info(
            "SignalQualityModel saved to %s (trained_on=%d)",
            self._model_path,
            self._trained_on,
        )

    def load(self) -> bool:
        """Load model from disk.  Returns True on success, False if not found."""
        if not self._model_path.exists():
            return False

        try:
            import joblib  # type: ignore[import]
        except ImportError:
            logger.warning("joblib is not installed; cannot load model.")
            return False

        try:
            model = joblib.load(self._model_path)
        except Exception as exc:
            logger.warning("SignalQualityModel.load: failed to deserialize model: %s", exc)
            return False

        self._model = model

        if self._meta_path.exists():
            try:
                with open(self._meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                self._trained_on = int(meta.get("trained_on", 0))
                self._feature_names = meta.get("feature_names", [])
            except Exception as exc:
                logger.debug("SignalQualityModel.load: could not read meta.json: %s", exc)

        logger.info(
            "SignalQualityModel loaded from %s (trained_on=%d)",
            self._model_path,
            self._trained_on,
        )
        return True

    # ------------------------------------------------------------------
    # Gating helpers
    # ------------------------------------------------------------------

    def quality_gate(
        self,
        features: np.ndarray,
        min_prob: float = 0.42,
    ) -> tuple[bool, float]:
        """Return (passes_gate, probability).

        When the model is not yet trained, all signals pass with prob=0.5.
        """
        if not self.is_trained():
            return (True, 0.5)
        prob = self.predict(features)
        return (prob >= min_prob, prob)

    def confidence_multiplier(self, prob: float) -> float:
        """Map win-probability to a position-size multiplier.

        Thresholds
        ----------
        >= 0.70  → 1.25 (scale up, high confidence)
        >= 0.55  → 1.00 (standard sizing)
        >= 0.42  → 0.75 (reduce sizing, borderline signal)
        <  0.42  → 0.00 (blocked — should not reach execution)
        """
        if prob >= 0.70:
            return 1.25
        if prob >= 0.55:
            return 1.0
        if prob >= 0.42:
            return 0.75
        return 0.0
