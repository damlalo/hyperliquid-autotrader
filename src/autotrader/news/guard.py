"""
NewsGuard: converts market sentiment + news headlines into a defensiveness
multiplier (0.0 = trade normally, 1.0 = fully defensive / flat).

The defensiveness value is used by the risk layer to scale down position
sizes. At 1.0 the TradeApprover will reject all new entries.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .fetcher import MarketNewsFetcher, NewsHeadline, FearGreedResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsState:
    defensiveness: float  # 0.0–1.0
    fear_greed: int
    fear_greed_class: str
    top_headline: str  # highest-impact headline title, "" if none
    reason: str  # human-readable explanation
    timestamp: datetime


def _default_news_state() -> NewsState:
    """Return a neutral ``NewsState`` used as a safe initial/fallback value."""
    return NewsState(
        defensiveness=0.0,
        fear_greed=50,
        fear_greed_class="Neutral",
        top_headline="",
        reason="No data yet",
        timestamp=datetime.now(tz=timezone.utc),
    )


class NewsGuard:
    """Translates current market sentiment into a defensiveness score.

    Parameters
    ----------
    fetcher:
        An existing :class:`~autotrader.news.fetcher.MarketNewsFetcher` or
        ``None`` to create a fresh one.
    cache_ttl_seconds:
        How long a cached :class:`NewsState` remains valid before the next
        call to :meth:`get_state` triggers a live refresh.  Defaults to 1 hour.
    """

    def __init__(
        self,
        fetcher: MarketNewsFetcher | None = None,
        cache_ttl_seconds: float = 3600.0,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else MarketNewsFetcher()
        self._cache_ttl = cache_ttl_seconds
        self._cache: NewsState | None = None
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # Core refresh logic
    # ------------------------------------------------------------------

    async def refresh(self) -> NewsState:
        """Fetch live Fear & Greed + headlines, compute defensiveness, update cache."""
        fg: FearGreedResult
        headlines: list[NewsHeadline]

        try:
            fg, headlines = await asyncio.gather(
                self._fetcher.fetch_fear_greed(),
                self._fetcher.fetch_news_headlines(),
            )
        except Exception:
            log.warning("NewsGuard.refresh: gather failed — using neutral fallback", exc_info=True)
            state = _default_news_state()
            self._cache = state
            self._cache_ts = time.monotonic()
            return state

        d = 0.0
        reasons: list[str] = []

        # Fear & Greed contribution
        if fg.value <= 15:
            d += 0.55
            reasons.append(f"Extreme Fear ({fg.value})")
        elif fg.value <= 25:
            d += 0.35
            reasons.append(f"Fear ({fg.value})")
        elif fg.value >= 85:
            d += 0.40
            reasons.append(f"Extreme Greed ({fg.value}) — overextension risk")
        elif fg.value >= 75:
            d += 0.25
            reasons.append(f"Greed ({fg.value})")

        # High-impact news contribution
        high_headlines = [h for h in headlines if h.impact_score >= 0.8]
        if high_headlines:
            d += min(0.50 * len(high_headlines), 0.60)
            reasons.append(f"{len(high_headlines)} high-impact headline(s)")

        d = min(d, 1.0)

        top_headline = headlines[0].title if headlines else ""
        reason = "; ".join(reasons) if reasons else "Normal conditions"

        state = NewsState(
            defensiveness=round(d, 6),
            fear_greed=fg.value,
            fear_greed_class=fg.classification,
            top_headline=top_headline,
            reason=reason,
            timestamp=datetime.now(tz=timezone.utc),
        )

        self._cache = state
        self._cache_ts = time.monotonic()
        log.info(
            "NewsGuard refreshed: defensiveness=%.3f fear_greed=%d reason=%r",
            d,
            fg.value,
            reason,
        )
        return state

    # ------------------------------------------------------------------
    # State accessor (cache-aware)
    # ------------------------------------------------------------------

    async def get_state(self, force_refresh: bool = False) -> NewsState:
        """Return a :class:`NewsState`, refreshing when the cache has expired.

        Parameters
        ----------
        force_refresh:
            When ``True``, bypass the TTL check and always fetch live data.
        """
        elapsed = time.monotonic() - self._cache_ts
        if not force_refresh and self._cache is not None and elapsed < self._cache_ttl:
            return self._cache
        return await self.refresh()

    # ------------------------------------------------------------------
    # Risk scaling helpers
    # ------------------------------------------------------------------

    def position_size_multiplier(self, state: NewsState) -> float:
        """Return a 0.0–1.0 multiplier to scale down position sizes.

        =========== =============
        Defensiveness  Multiplier
        =========== =============
        >= 0.95       0.00  (fully defensive — size to zero)
        >= 0.70       0.25
        >= 0.50       0.50
        >= 0.30       0.70
        >= 0.15       0.85
        < 0.15        1.00  (trade normally)
        =========== =============
        """
        d = state.defensiveness
        if d >= 0.95:
            return 0.0
        if d >= 0.70:
            return 0.25
        if d >= 0.50:
            return 0.50
        if d >= 0.30:
            return 0.70
        if d >= 0.15:
            return 0.85
        return 1.0

    def should_allow_new_entries(self, state: NewsState) -> bool:
        """Return ``False`` when defensiveness is so high that no new trades
        should be opened (defensiveness >= 0.95)."""
        return state.defensiveness < 0.95


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_guard: NewsGuard | None = None


def get_news_guard() -> NewsGuard:
    """Return the module-level :class:`NewsGuard` singleton, creating it on
    first call.

    The singleton uses all default parameters (no-auth fetcher, 1-hour cache).
    For custom configuration, instantiate :class:`NewsGuard` directly.
    """
    global _default_guard
    if _default_guard is None:
        _default_guard = NewsGuard()
    return _default_guard
