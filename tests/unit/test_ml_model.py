"""Unit tests for SignalQualityModel."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from autotrader.ml.model import SignalQualityModel


def _make_data(n: int = 300, n_features: int = 26):
    rng = np.random.default_rng(7)
    X = rng.standard_normal((n, n_features)).astype(np.float32)
    y = rng.integers(0, 2, size=n)
    return X, y


def _fresh_model() -> SignalQualityModel:
    """Return a model backed by a unique temp directory so no model is pre-loaded."""
    tmpdir = Path(tempfile.mkdtemp())
    account = f"test_{uuid.uuid4().hex[:8]}"
    # Patch the base dir so the model writes to temp
    with patch("autotrader.ml.model.Path") as _mock:
        pass  # just to verify import path
    # Directly override _model_dir after construction is simpler
    m = SignalQualityModel.__new__(SignalQualityModel)
    m._account = account
    m._model_dir = tmpdir / "ml" / account
    m._model_dir.mkdir(parents=True, exist_ok=True)
    m._model_path = m._model_dir / "signal_quality.lgbm"
    m._meta_path = m._model_dir / "meta.json"
    m._model = None
    m._trained_on = 0
    m._min_samples = 80
    return m


class TestSignalQualityModel:
    def setup_method(self):
        self.model = _fresh_model()

    def test_untrained_default_predict(self):
        feats = np.zeros(26, dtype=np.float32)
        prob = self.model.predict(feats)
        assert abs(prob - 0.5) < 1e-6

    def test_untrained_quality_gate_passes(self):
        feats = np.zeros(26, dtype=np.float32)
        passes, prob = self.model.quality_gate(feats)
        assert passes is True  # untrained → always pass
        assert prob == 0.5

    def test_is_trained_false_initially(self):
        assert not self.model.is_trained()

    def test_fit_and_predict(self):
        X, y = _make_data(300)
        self.model.fit(X, y)
        assert self.model.is_trained()
        prob = self.model.predict(X[0])
        assert 0.0 <= prob <= 1.0

    def test_quality_gate_trained(self):
        X, y = _make_data(300)
        self.model.fit(X, y)
        passes, prob = self.model.quality_gate(X[0], min_prob=0.0)
        assert isinstance(passes, bool)
        assert 0.0 <= prob <= 1.0

    def test_quality_gate_rejects_below_threshold(self):
        X, y = _make_data(300)
        self.model.fit(X, y)
        # with min_prob=1.0 nothing passes
        passes, _ = self.model.quality_gate(X[0], min_prob=1.0)
        assert passes is False

    def test_confidence_multiplier_bands(self):
        # prob >= 0.65 → 1.25
        assert self.model.confidence_multiplier(0.70) == 1.25
        # 0.55 <= prob < 0.65 → 1.0
        assert self.model.confidence_multiplier(0.58) == 1.0
        # 0.42 <= prob < 0.55 → 0.75
        assert self.model.confidence_multiplier(0.48) == 0.75
        # prob < 0.42 → 0.0 (block)
        assert self.model.confidence_multiplier(0.30) == 0.0

    def test_fit_too_few_samples_skips(self):
        # Model requires _min_samples (80); 10 samples should skip
        X, y = _make_data(10)
        self.model.fit(X, y)  # should not raise
        assert not self.model.is_trained()
