"""Unit tests for NewsGuard defensiveness logic."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from autotrader.news.guard import NewsGuard, NewsState


def _state(d: float, fg: int = 50) -> NewsState:
    return NewsState(
        defensiveness=d,
        fear_greed=fg,
        fear_greed_class="Neutral",
        top_headline="",
        reason="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestNewsGuard:
    def setup_method(self):
        self.guard = NewsGuard()

    def test_position_size_multiplier_normal(self):
        # Defensiveness < 0.15 → 1.0 (no restriction)
        mult = self.guard.position_size_multiplier(_state(0.0))
        assert mult == pytest.approx(1.0)

    def test_position_size_multiplier_extreme(self):
        # Defensiveness >= 0.95 → 0.0 (block)
        mult = self.guard.position_size_multiplier(_state(0.95))
        assert mult == pytest.approx(0.0)

    def test_position_size_multiplier_mid(self):
        # 0.50 defensiveness → 0.50
        mult = self.guard.position_size_multiplier(_state(0.50))
        assert mult == pytest.approx(0.50)

    def test_position_size_multiplier_band_0_30(self):
        mult = self.guard.position_size_multiplier(_state(0.30))
        assert mult == pytest.approx(0.70)

    def test_position_size_multiplier_band_0_70(self):
        mult = self.guard.position_size_multiplier(_state(0.70))
        assert mult == pytest.approx(0.25)

    def test_should_allow_new_entries_low_defense(self):
        assert self.guard.should_allow_new_entries(_state(0.0))
        assert self.guard.should_allow_new_entries(_state(0.94))

    def test_should_block_new_entries_high_defense(self):
        assert not self.guard.should_allow_new_entries(_state(0.95))
        assert not self.guard.should_allow_new_entries(_state(1.0))

    def test_get_state_returns_none_initially(self):
        fresh = NewsGuard()
        assert fresh._cache is None

    def test_multiplier_bands_monotone(self):
        """Higher defensiveness → lower multiplier."""
        levels = [0.0, 0.15, 0.30, 0.50, 0.70, 0.95]
        mults = [self.guard.position_size_multiplier(_state(d)) for d in levels]
        for i in range(len(mults) - 1):
            assert mults[i] >= mults[i + 1], (
                f"mult[{i}]={mults[i]} < mult[{i+1}]={mults[i+1]}"
            )

    @pytest.mark.asyncio
    async def test_refresh_populates_cache(self):
        from autotrader.news.fetcher import FearGreedResult, NewsHeadline

        mock_fg = AsyncMock(
            return_value=FearGreedResult(value=50, classification="Neutral", timestamp=datetime.now(tz=timezone.utc))
        )
        mock_news = AsyncMock(return_value=[])
        with (
            patch.object(self.guard._fetcher, "fetch_fear_greed", mock_fg),
            patch.object(self.guard._fetcher, "fetch_news_headlines", mock_news),
        ):
            state = await self.guard.refresh()
        assert self.guard._cache is not None
        assert isinstance(state, NewsState)
        assert 0.0 <= state.defensiveness <= 1.0

    @pytest.mark.asyncio
    async def test_get_state_uses_cache(self):
        """Second call should return cached result without a new fetch."""
        from autotrader.news.fetcher import FearGreedResult

        call_count = 0

        async def mock_fg():
            nonlocal call_count
            call_count += 1
            return FearGreedResult(value=60, classification="Greed", timestamp=datetime.now(tz=timezone.utc))

        mock_news = AsyncMock(return_value=[])
        with (
            patch.object(self.guard._fetcher, "fetch_fear_greed", mock_fg),
            patch.object(self.guard._fetcher, "fetch_news_headlines", mock_news),
        ):
            await self.guard.get_state()
            await self.guard.get_state()  # Should hit cache

        assert call_count == 1  # Only one actual fetch
