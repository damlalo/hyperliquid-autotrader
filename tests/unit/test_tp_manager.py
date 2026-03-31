"""Unit tests for TrailingTPManager 4-phase exit logic."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from autotrader.execution.tp_manager import PositionPhase, TrailingTPManager


def _make_tp_manager() -> TrailingTPManager:
    om = MagicMock()
    om.submit_exit = AsyncMock(return_value=None)
    tmpdir = Path(tempfile.mkdtemp())
    return TrailingTPManager(order_manager=om, state_dir=tmpdir)


def _open(mgr: TrailingTPManager, coin: str = "BTC", entry: float = 1000.0,
          atr: float = 20.0, side: str = "long") -> None:
    """Open a position using the real API (stop = 2×ATR from entry)."""
    r = 2 * atr
    if side == "long":
        stop, tp = entry - r, entry + 2 * r
    else:
        stop, tp = entry + r, entry - 2 * r
    mgr.open_position(coin=coin, side=side, entry_price=entry, size=1.0, stop=stop, tp=tp)


class TestTrailingTPManager:
    def setup_method(self):
        self.mgr = _make_tp_manager()

    def test_open_position_registers(self):
        _open(self.mgr)
        assert "BTC" in self.mgr._positions

    def test_close_removes_position(self):
        _open(self.mgr)
        self.mgr.close_position("BTC")
        assert "BTC" not in self.mgr._positions

    async def test_update_no_change_below_be(self):
        # entry=1000, R=40, 0.5R trigger=1020; price=1010 → no phase change
        _open(self.mgr, entry=1000.0, atr=20.0)
        actions = await self.mgr.update("BTC", current_price=1005.0, current_atr=20.0)
        assert all(a.get("action") not in ("partial_exit", "close_position") for a in actions)
        assert self.mgr._positions["BTC"].phase == PositionPhase.PHASE1

    async def test_be_trigger_at_half_r(self):
        # entry=1000, R=40, +0.5R = 1020
        _open(self.mgr, entry=1000.0, atr=20.0)
        await self.mgr.update("BTC", current_price=1021.0, current_atr=20.0)
        assert self.mgr._positions["BTC"].phase >= PositionPhase.PHASE2

    async def test_phase_one_r_partial_exit(self):
        # entry=1000, R=40, +1R = 1040
        _open(self.mgr, entry=1000.0, atr=20.0)
        await self.mgr.update("BTC", current_price=1021.0, current_atr=20.0)
        actions = await self.mgr.update("BTC", current_price=1045.0, current_atr=20.0)
        partial = [a for a in actions if a.get("action") == "partial_exit"]
        assert len(partial) >= 1
        # Phase 2 exit is 50% of initial_size=1.0 → size=0.5
        assert abs(partial[0]["size"] - 0.50) < 1e-6

    async def test_stop_hit_triggers_close(self):
        # entry=1000, stop=960 (2×20 below)
        _open(self.mgr, entry=1000.0, atr=20.0, side="long")
        actions = await self.mgr.update("BTC", current_price=955.0, current_atr=20.0)
        closes = [a for a in actions if a.get("action") == "close_position"]
        assert len(closes) == 1

    async def test_short_position_inverted_logic(self):
        # short: entry=1000, stop=1040, 0.5R below = 980
        _open(self.mgr, entry=1000.0, atr=20.0, side="short")
        await self.mgr.update("BTC", current_price=979.0, current_atr=20.0)
        assert self.mgr._positions["BTC"].phase >= PositionPhase.PHASE2

    async def test_no_position_update_returns_empty(self):
        actions = await self.mgr.update("ETH", current_price=2000.0, current_atr=50.0)
        assert actions == []

    def test_multiple_coins_independent(self):
        _open(self.mgr, coin="BTC", entry=1000.0, atr=20.0)
        _open(self.mgr, coin="ETH", entry=2000.0, atr=40.0)
        assert "BTC" in self.mgr._positions
        assert "ETH" in self.mgr._positions
        self.mgr.close_position("BTC")
        assert "BTC" not in self.mgr._positions
        assert "ETH" in self.mgr._positions
