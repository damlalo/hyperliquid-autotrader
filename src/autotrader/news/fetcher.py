"""
Async fetchers for market sentiment data.
- Alternative.me Fear & Greed Index (free, no auth)
- CoinDesk RSS headline scan for high-impact keywords
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger(__name__)

_FNG_URL = "https://api.alternative.me/fng/?limit=1&format=json"
_COINDESK_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"

HIGH_IMPACT_KEYWORDS: list[str] = [
    "hack",
    "exploit",
    "ban",
    "sec",
    "regulation",
    "crash",
    "emergency",
    "liquidat",
    "halt",
    "suspend",
    "insolv",
    "bankrupt",
    "freeze",
    "seize",
]
MEDIUM_IMPACT_KEYWORDS: list[str] = [
    "warning",
    "risk",
    "concern",
    "drop",
    "fell",
    "tumble",
    "surge",
    "pump",
]


@dataclass(frozen=True)
class FearGreedResult:
    value: int  # 0–100
    classification: str  # e.g. "Extreme Fear"
    timestamp: datetime


@dataclass(frozen=True)
class NewsHeadline:
    title: str
    published: datetime
    source: str
    impact_score: float  # 0.0–1.0 keyword-derived


def _score_headline(title: str) -> float:
    """Return an impact score for *title* based on keyword matching."""
    lower = title.lower()
    if any(kw in lower for kw in HIGH_IMPACT_KEYWORDS):
        return 0.9
    if any(kw in lower for kw in MEDIUM_IMPACT_KEYWORDS):
        return 0.4
    return 0.1


def _feedparser_tuple_to_datetime(time_tuple: tuple | None) -> datetime | None:
    """Convert a feedparser 9-tuple into a timezone-aware :class:`datetime`."""
    if time_tuple is None:
        return None
    try:
        import time as _time

        ts = _time.mktime(time_tuple[:9])
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


class MarketNewsFetcher:
    """Async fetcher for Fear & Greed index and CoinDesk news headlines.

    Pass an existing :class:`aiohttp.ClientSession` to share connection pools,
    or leave *session* as ``None`` to have the fetcher create its own.

    The internally-created session is **not** closed automatically so that
    callers decide lifetime.  Call :meth:`close` to release it if needed.
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "hyperliquid-autotrader/1.0"},
            )
        return self._session

    async def close(self) -> None:
        """Close the internal session if this instance owns it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Fear & Greed
    # ------------------------------------------------------------------

    async def fetch_fear_greed(self) -> FearGreedResult:
        """Fetch the current Fear & Greed index from Alternative.me.

        Falls back to a neutral ``FearGreedResult(50, "Neutral", now)`` on any
        error so that callers always receive a usable value.
        """
        _neutral = FearGreedResult(
            value=50,
            classification="Neutral",
            timestamp=datetime.now(tz=timezone.utc),
        )
        try:
            session = await self._get_session()
            async with session.get(_FNG_URL) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)

            data = payload["data"][0]
            value = int(data["value"])
            classification = str(data["value_classification"])
            ts = datetime.fromtimestamp(int(data["timestamp"]), tz=timezone.utc)
            return FearGreedResult(value=value, classification=classification, timestamp=ts)

        except Exception:
            log.warning("fetch_fear_greed failed — returning neutral fallback", exc_info=True)
            return _neutral

    # ------------------------------------------------------------------
    # News headlines
    # ------------------------------------------------------------------

    async def fetch_news_headlines(
        self,
        max_age_hours: float = 4.0,
    ) -> list[NewsHeadline]:
        """Fetch and score recent CoinDesk headlines.

        Entries older than *max_age_hours* are discarded.  Returns at most 20
        headlines sorted by *impact_score* descending.  Returns ``[]`` on any
        error.
        """
        try:
            session = await self._get_session()
            async with session.get(_COINDESK_RSS_URL) as resp:
                resp.raise_for_status()
                raw_bytes = await resp.read()

            # feedparser.parse is synchronous — run in executor to stay async
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, _parse_feed, raw_bytes)

            cutoff = datetime.now(tz=timezone.utc).timestamp() - max_age_hours * 3600.0
            headlines: list[NewsHeadline] = []

            for entry in getattr(feed, "entries", []):
                title = getattr(entry, "title", "") or ""
                published_dt = _feedparser_tuple_to_datetime(
                    getattr(entry, "published_parsed", None)
                )
                if published_dt is None:
                    continue
                if published_dt.timestamp() < cutoff:
                    continue

                score = _score_headline(title)
                headlines.append(
                    NewsHeadline(
                        title=title,
                        published=published_dt,
                        source="coindesk",
                        impact_score=score,
                    )
                )

            headlines.sort(key=lambda h: h.impact_score, reverse=True)
            return headlines[:20]

        except Exception:
            log.warning("fetch_news_headlines failed — returning []", exc_info=True)
            return []


def _parse_feed(raw_bytes: bytes):
    """Synchronous feedparser wrapper — called in an executor."""
    try:
        import feedparser  # type: ignore[import]

        return feedparser.parse(raw_bytes)
    except ImportError:
        log.warning(
            "feedparser not installed — install it with `pip install feedparser` "
            "for CoinDesk headline fetching.  Returning empty feed."
        )

        class _EmptyFeed:
            entries: list = []

        return _EmptyFeed()
