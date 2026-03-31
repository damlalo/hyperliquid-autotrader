"""Unit tests for PortfolioHedger delta computation and hedge recommendation."""
from __future__ import annotations

import pytest

from autotrader.risk.hedging import PortfolioHedger, DeltaSnapshot


def _pos_dict(coin: str, szi: float, entry_px: float) -> dict:
    """Position dict in the format expected by PortfolioHedger.compute_delta."""
    return {"szi": szi, "entryPx": entry_px}


class TestPortfolioHedger:
    def setup_method(self):
        self.hedger = PortfolioHedger(delta_threshold=0.65, target_delta=0.30)

    def test_compute_delta_long_only(self):
        # 1 BTC long @ 10,000 = +10,000 notional; 0.5 ETH long @ 2,000 = +1,000
        positions = {
            "BTC": _pos_dict("BTC", szi=1.0, entry_px=10_000),
            "ETH": _pos_dict("ETH", szi=0.5, entry_px=2_000),
        }
        snap = self.hedger.compute_delta(positions, equity=20_000)
        assert snap.net_delta_usd == pytest.approx(11_000)
        assert snap.net_delta_pct == pytest.approx(11_000 / 20_000)

    def test_compute_delta_neutral(self):
        positions = {
            "BTC_long": _pos_dict("BTC", szi=1.0, entry_px=10_000),
            "BTC_short": _pos_dict("BTC", szi=-1.0, entry_px=10_000),
        }
        snap = self.hedger.compute_delta(positions, equity=20_000)
        assert snap.net_delta_usd == pytest.approx(0.0)
        assert snap.net_delta_pct == pytest.approx(0.0)

    def test_needs_hedge_above_threshold(self):
        # net_delta_pct = 0.75 > threshold 0.65
        snap = DeltaSnapshot(
            net_delta_usd=15_000,
            gross_exposure_usd=15_000,
            net_delta_pct=0.75,
            per_coin={"BTC": 15_000},
        )
        assert self.hedger.should_hedge(snap)

    def test_no_hedge_below_threshold(self):
        snap = DeltaSnapshot(
            net_delta_usd=5_000,
            gross_exposure_usd=5_000,
            net_delta_pct=0.25,
            per_coin={"BTC": 5_000},
        )
        assert not self.hedger.should_hedge(snap)

    def test_recommend_hedge_returns_recommendation(self):
        snap = DeltaSnapshot(
            net_delta_usd=15_000,
            gross_exposure_usd=15_000,
            net_delta_pct=0.75,
            per_coin={"BTC": 15_000},
        )
        rec = self.hedger.recommend_hedge(snap, equity=20_000, available_coins=["BTC", "ETH"])
        assert rec is not None
        assert rec.coin in ("BTC", "ETH")
        assert rec.side == "short"
        assert rec.size_usd > 0

    def test_hedge_size_clamped_to_max(self):
        """Hedge should not exceed 20% of equity."""
        snap = DeltaSnapshot(
            net_delta_usd=900_000,
            gross_exposure_usd=900_000,
            net_delta_pct=9.0,
            per_coin={"BTC": 900_000},
        )
        rec = self.hedger.recommend_hedge(snap, equity=100_000, available_coins=["BTC"])
        assert rec is not None
        assert rec.size_usd <= 100_000 * 0.20 + 1  # allow rounding

    def test_no_hedge_needed_returns_none(self):
        snap = DeltaSnapshot(
            net_delta_usd=1_000,
            gross_exposure_usd=1_000,
            net_delta_pct=0.05,
            per_coin={"BTC": 1_000},
        )
        rec = self.hedger.recommend_hedge(snap, equity=20_000, available_coins=["BTC", "ETH"])
        assert rec is None

    def test_register_and_deregister(self):
        self.hedger.register_hedge("BTC", side="short")
        assert self.hedger.is_hedge("BTC")
        self.hedger.deregister_hedge("BTC")
        assert not self.hedger.is_hedge("BTC")

    def test_no_double_hedge_same_side(self):
        """If BTC already has a short hedge, it should be excluded from candidates."""
        self.hedger.register_hedge("BTC", side="short")
        snap = DeltaSnapshot(
            net_delta_usd=15_000,
            gross_exposure_usd=15_000,
            net_delta_pct=0.75,
            per_coin={"ETH": 15_000},
        )
        rec = self.hedger.recommend_hedge(snap, equity=20_000, available_coins=["BTC", "ETH"])
        # BTC already has short hedge → should pick ETH (or None if ETH also unavailable)
        if rec is not None:
            assert rec.coin != "BTC"

    def test_short_skew_recommends_long_hedge(self):
        snap = DeltaSnapshot(
            net_delta_usd=-15_000,
            gross_exposure_usd=15_000,
            net_delta_pct=-0.75,
            per_coin={"BTC": -15_000},
        )
        rec = self.hedger.recommend_hedge(snap, equity=20_000, available_coins=["BTC", "ETH"])
        assert rec is not None
        assert rec.side == "long"
