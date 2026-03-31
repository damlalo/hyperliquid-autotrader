"""Funding rate and open-interest collectors for Hyperliquid."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from autotrader.hl.client import HyperliquidClient
    from autotrader.store.datastore import DataStore

logger = logging.getLogger(__name__)

# Funding snapshots are generated every 8 hours on Hyperliquid.
_FUNDING_PERIOD_MS = 8 * 60 * 60 * 1000

_FUNDING_DTYPES: dict[str, str] = {
    "time": "int64",
    "fundingRate": "float64",
    "premium": "float64",
}

_OI_DTYPES: dict[str, str] = {
    "coin": "object",
    "timestamp": "int64",
    "openInterest": "float64",
    "markPx": "float64",
    "oraclePx": "float64",
    "funding": "float64",
}


def _empty_funding() -> pd.DataFrame:
    return pd.DataFrame(
        {col: pd.Series(dtype=dtype) for col, dtype in _FUNDING_DTYPES.items()}
    )


def _empty_oi() -> pd.DataFrame:
    return pd.DataFrame(
        {col: pd.Series(dtype=dtype) for col, dtype in _OI_DTYPES.items()}
    )


def _parse_funding_snapshot(raw: list[dict]) -> pd.DataFrame:
    """Parse raw funding snapshot list from HL API into a typed DataFrame."""
    if not raw:
        return _empty_funding()
    records = []
    for item in raw:
        records.append(
            {
                "time": int(item["time"]),
                "fundingRate": float(item["fundingRate"]),
                "premium": float(item.get("premium", 0.0)),
            }
        )
    df = pd.DataFrame(records)
    for col, dtype in _FUNDING_DTYPES.items():
        df[col] = df[col].astype(dtype)
    return df.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# FundingCollector
# ---------------------------------------------------------------------------


class FundingCollector:
    """Fetches historical and incremental funding rate snapshots."""

    def __init__(self, client: "HyperliquidClient", store: "DataStore") -> None:
        self._client = client
        self._store = store

    async def _fetch_batch(
        self, coin: str, start_ms: int, end_ms: int
    ) -> pd.DataFrame:
        raw = await self._client._post_info({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_ms,
            "endTime": end_ms,
        })
        return _parse_funding_snapshot(raw or [])

    async def bootstrap(self, coin: str, lookback_days: int = 90) -> pd.DataFrame:
        """Fetch full funding history for *lookback_days* and write to store."""
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000

        # Batch window: 500 periods × 8 h = ~167 days per request (generous)
        batch_ms = 500 * _FUNDING_PERIOD_MS
        end_ms = now_ms
        all_frames: list[pd.DataFrame] = []

        logger.info("bootstrap funding %s: %d days lookback", coin, lookback_days)

        while end_ms > cutoff_ms:
            start_ms = max(end_ms - batch_ms, cutoff_ms)
            df_batch = await self._fetch_batch(coin, start_ms, end_ms)
            if not df_batch.empty:
                self._store.write_funding(coin, df_batch)
                all_frames.append(df_batch)
                logger.debug(
                    "bootstrap funding %s: %d rows [%d, %d]",
                    coin, len(df_batch), start_ms, end_ms,
                )
            end_ms = start_ms - _FUNDING_PERIOD_MS
            await asyncio.sleep(0)

        if not all_frames:
            logger.warning("bootstrap funding %s: no data returned", coin)
            return _empty_funding()

        combined = pd.concat(all_frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["time"], keep="last")
        combined = combined.sort_values("time").reset_index(drop=True)
        logger.info("bootstrap funding %s: %d total rows", coin, len(combined))
        return combined

    async def update(self, coin: str) -> pd.DataFrame:
        """Fetch funding snapshots since the last stored timestamp."""
        existing = self._store.read_funding(coin)
        now_ms = int(time.time() * 1000)

        if existing.empty:
            logger.info("update funding %s: no existing data — running 90-day bootstrap", coin)
            return await self.bootstrap(coin, lookback_days=90)

        last_t = int(existing["time"].max())
        start_ms = last_t + _FUNDING_PERIOD_MS

        if start_ms >= now_ms:
            logger.debug("update funding %s: already up to date", coin)
            return existing

        df_new = await self._fetch_batch(coin, start_ms, now_ms)
        if df_new.empty:
            logger.debug("update funding %s: no new data", coin)
            return existing

        self._store.write_funding(coin, df_new)
        logger.info("update funding %s: wrote %d new rows", coin, len(df_new))
        return self._store.read_funding(coin)


# ---------------------------------------------------------------------------
# OICollector
# ---------------------------------------------------------------------------


class OICollector:
    """Fetches current open-interest and mark/oracle prices from meta_and_asset_ctxs."""

    def __init__(self, client: "HyperliquidClient") -> None:
        self._client = client

    async def get_current(self, coins: list[str]) -> pd.DataFrame:
        """Return current OI snapshot for *coins* via metaAndAssetCtxs.

        Calls the HL ``metaAndAssetCtxs`` info endpoint and extracts the
        relevant fields for each requested coin.
        """
        raw = await self._client._post_info({"type": "metaAndAssetCtxs"})

        if not raw or len(raw) < 2:
            logger.warning("get_current OI: unexpected response shape")
            return _empty_oi()

        universe: list[dict] = raw[0].get("universe", [])
        asset_ctxs: list[dict] = raw[1]

        # Build a name → index map.
        name_to_idx = {item["name"]: idx for idx, item in enumerate(universe)}

        requested_set = set(coins)
        now_ms = int(time.time() * 1000)
        records = []

        for coin in coins:
            idx = name_to_idx.get(coin)
            if idx is None or idx >= len(asset_ctxs):
                logger.warning("get_current OI: coin %s not found in universe", coin)
                continue
            ctx = asset_ctxs[idx]
            records.append(
                {
                    "coin": coin,
                    "timestamp": now_ms,
                    "openInterest": float(ctx.get("openInterest", 0.0)),
                    "markPx": float(ctx.get("markPx", 0.0)),
                    "oraclePx": float(ctx.get("oraclePx", 0.0)),
                    "funding": float(ctx.get("funding", 0.0)),
                }
            )

        if not records:
            return _empty_oi()

        df = pd.DataFrame(records)
        for col, dtype in _OI_DTYPES.items():
            df[col] = df[col].astype(dtype)
        return df
