"""Candle collector: bootstrap full history and incremental updates from Hyperliquid."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from autotrader.hl.client import HyperliquidClient
    from autotrader.store.datastore import DataStore

logger = logging.getLogger(__name__)

# Hyperliquid returns at most 5000 candles per request.
_BATCH_SIZE = 5000

# Column dtypes for raw candle data.
_CANDLE_DTYPES: dict[str, str] = {
    "t": "int64",
    "o": "float64",
    "h": "float64",
    "l": "float64",
    "c": "float64",
    "v": "float64",
    "n": "int64",
}

# Interval string → milliseconds duration
_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "3d": 3 * 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


def _interval_ms(interval: str) -> int:
    ms = _INTERVAL_MS.get(interval)
    if ms is None:
        raise ValueError(
            f"Unknown interval '{interval}'.  Supported: {list(_INTERVAL_MS)}"
        )
    return ms


def _parse_candles(raw: list[dict]) -> pd.DataFrame:
    """Convert a list of raw candle dicts from the HL API into a typed DataFrame."""
    if not raw:
        return pd.DataFrame({col: pd.Series(dtype=dtype) for col, dtype in _CANDLE_DTYPES.items()})

    records = []
    for item in raw:
        records.append(
            {
                "t": int(item["t"]),
                "o": float(item["o"]),
                "h": float(item["h"]),
                "l": float(item["l"]),
                "c": float(item["c"]),
                "v": float(item["v"]),
                "n": int(item["n"]),
            }
        )
    df = pd.DataFrame(records)
    for col, dtype in _CANDLE_DTYPES.items():
        df[col] = df[col].astype(dtype)
    return df


def _validate(df: pd.DataFrame, coin: str, interval: str) -> pd.DataFrame:
    """Apply basic sanity checks; drop offending rows and log warnings."""
    if df.empty:
        return df

    before = len(df)

    # Remove duplicate timestamps.
    df = df.drop_duplicates(subset=["t"], keep="last")

    # Require positive prices and volume.
    price_cols = ["o", "h", "l", "c", "v"]
    mask_positive = (df[price_cols] > 0).all(axis=1)
    n_bad = (~mask_positive).sum()
    if n_bad:
        logger.warning(
            "candles %s/%s: dropping %d rows with non-positive price/volume",
            coin, interval, n_bad,
        )
    df = df[mask_positive]

    # Sort and reset index.
    df = df.sort_values("t").reset_index(drop=True)

    # Verify monotonically increasing t after sort.
    if not df["t"].is_monotonic_increasing:
        logger.error(
            "candles %s/%s: timestamps not monotonically increasing after sort",
            coin, interval,
        )

    if len(df) < before:
        logger.debug(
            "candles %s/%s: removed %d invalid rows (%d remaining)",
            coin, interval, before - len(df), len(df),
        )

    return df


class CandleCollector:
    """Fetches OHLCV candles from Hyperliquid and persists them via *store*."""

    def __init__(self, client: "HyperliquidClient", store: "DataStore") -> None:
        self._client = client
        self._store = store

    # ------------------------------------------------------------------ #
    # Internal fetch                                                       #
    # ------------------------------------------------------------------ #

    async def _fetch_batch(
        self,
        coin: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """Fetch a single batch of candles from the HL /info endpoint."""
        raw = await self._client._post_info({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        })
        if not raw:
            return _parse_candles([])
        return _parse_candles(raw)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def bootstrap(
        self,
        coin: str,
        interval: str,
        lookback_days: int = 365,
    ) -> pd.DataFrame:
        """Fetch complete history for *lookback_days* in reverse-paginated batches.

        Writes each batch to the store as it is received so progress is not lost
        on interruption.  Returns the full combined DataFrame.
        """
        import time

        period_ms = _interval_ms(interval)
        batch_duration_ms = _BATCH_SIZE * period_ms
        now_ms = int(time.time() * 1000)
        end_ms = now_ms
        cutoff_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000

        all_frames: list[pd.DataFrame] = []

        logger.info(
            "bootstrap %s/%s: fetching %d days of history", coin, interval, lookback_days
        )

        while end_ms > cutoff_ms:
            start_ms = max(end_ms - batch_duration_ms, cutoff_ms)
            df_batch = await self._fetch_batch(coin, interval, start_ms, end_ms)

            if not df_batch.empty:
                df_batch = _validate(df_batch, coin, interval)
                self._store.write_candles(coin, interval, df_batch)
                all_frames.append(df_batch)
                logger.debug(
                    "bootstrap %s/%s: fetched %d candles [%d, %d]",
                    coin, interval, len(df_batch), start_ms, end_ms,
                )

            end_ms = start_ms - period_ms  # step back; avoid overlap

            # Brief yield to prevent blocking the event loop.
            await asyncio.sleep(0)

        if not all_frames:
            logger.warning("bootstrap %s/%s: no data returned", coin, interval)
            return _parse_candles([])

        combined = pd.concat(all_frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["t"], keep="last")
        combined = combined.sort_values("t").reset_index(drop=True)
        logger.info(
            "bootstrap %s/%s: complete — %d total candles", coin, interval, len(combined)
        )
        return combined

    async def update(self, coin: str, interval: str) -> pd.DataFrame:
        """Fetch candles since the last stored timestamp and write incremental update.

        Falls back to a 7-day bootstrap if no prior data exists.
        """
        import time

        existing = self._store.read_candles(coin, interval)
        now_ms = int(time.time() * 1000)

        if existing.empty:
            logger.info(
                "update %s/%s: no existing data, falling back to 7-day bootstrap",
                coin, interval,
            )
            return await self.bootstrap(coin, interval, lookback_days=7)

        last_t = int(existing["t"].max())
        period_ms = _interval_ms(interval)
        start_ms = last_t + period_ms  # start from next period

        if start_ms >= now_ms:
            logger.debug("update %s/%s: already up to date", coin, interval)
            return existing

        df_new = await self._fetch_batch(coin, interval, start_ms, now_ms)

        if df_new.empty:
            logger.debug("update %s/%s: no new candles", coin, interval)
            return existing

        df_new = _validate(df_new, coin, interval)
        self._store.write_candles(coin, interval, df_new)
        logger.info(
            "update %s/%s: wrote %d new candles", coin, interval, len(df_new)
        )
        return self._store.read_candles(coin, interval)

    async def update_all(
        self,
        coins: list[str],
        intervals: list[str],
    ) -> None:
        """Call ``update`` for every (coin, interval) combination concurrently."""
        tasks = [
            self.update(coin, interval)
            for coin in coins
            for interval in intervals
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (coin, interval), result in zip(
            [(c, i) for c in coins for i in intervals], results
        ):
            if isinstance(result, Exception):
                logger.error(
                    "update_all %s/%s failed: %s", coin, interval, result
                )
