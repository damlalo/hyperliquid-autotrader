"""User state polling: positions, account value, open orders from Hyperliquid."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autotrader.hl.client import HyperliquidClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """Represents a single perpetual position."""

    coin: str
    side: str           # "long" or "short"
    size: float         # absolute size in base asset
    entry_px: float
    unrealized_pnl: float
    liquidation_px: float | None
    leverage: float
    margin_used: float


@dataclass
class OpenOrder:
    """Represents a resting open order."""

    oid: int
    coin: str
    side: str        # "B" (buy) or "A" (ask/sell)
    order_type: str
    limit_px: float
    sz: float
    orig_sz: float
    timestamp: int
    reduce_only: bool
    cloid: str | None = None


@dataclass
class ClearinghouseState:
    """Full clearinghouse state for one account."""

    margin_summary: dict = field(default_factory=dict)
    cross_margin_summary: dict = field(default_factory=dict)
    asset_positions: list[dict] = field(default_factory=list)
    time: int = 0


# ---------------------------------------------------------------------------
# UserStateCollector
# ---------------------------------------------------------------------------


class UserStateCollector:
    """Polls user clearinghouse state and open orders from the HL REST API."""

    def __init__(self, client: "HyperliquidClient", account_address: str) -> None:
        self._client = client
        self._address = account_address

    async def _call_info(self, method: str, params: dict) -> dict | list:
        """POST /info with a type+params payload via the async client."""
        payload = {"type": method, **params}
        return await self._client._post_info(payload)

    # ------------------------------------------------------------------ #
    # Clearinghouse state                                                  #
    # ------------------------------------------------------------------ #

    async def get_state(self) -> ClearinghouseState:
        """Return the full ``clearinghouseState`` for the configured account."""
        raw = await self._call_info(
            "clearinghouseState", {"user": self._address}
        )
        if not isinstance(raw, dict):
            logger.error("get_state: unexpected response type %s", type(raw))
            return ClearinghouseState()

        return ClearinghouseState(
            margin_summary=raw.get("marginSummary", {}),
            cross_margin_summary=raw.get("crossMarginSummary", {}),
            asset_positions=raw.get("assetPositions", []),
            time=int(raw.get("time", 0)),
        )

    # ------------------------------------------------------------------ #
    # Positions                                                            #
    # ------------------------------------------------------------------ #

    async def get_positions(self) -> dict[str, Position]:
        """Return open positions keyed by coin symbol.

        Only positions with non-zero size are included.
        """
        state = await self.get_state()
        positions: dict[str, Position] = {}

        for entry in state.asset_positions:
            pos_data: dict = entry.get("position", {})
            coin: str = pos_data.get("coin", "")
            szi: str = pos_data.get("szi", "0")
            size = float(szi)
            if size == 0.0:
                continue

            side = "long" if size > 0.0 else "short"
            entry_px = float(pos_data.get("entryPx", 0.0) or 0.0)
            unrealized_pnl = float(pos_data.get("unrealizedPnl", 0.0) or 0.0)
            liquidation_px_raw = pos_data.get("liquidationPx")
            liquidation_px = float(liquidation_px_raw) if liquidation_px_raw is not None else None
            leverage_data: dict = pos_data.get("leverage", {})
            leverage = float(leverage_data.get("value", 1.0))
            margin_used = float(pos_data.get("marginUsed", 0.0) or 0.0)

            positions[coin] = Position(
                coin=coin,
                side=side,
                size=abs(size),
                entry_px=entry_px,
                unrealized_pnl=unrealized_pnl,
                liquidation_px=liquidation_px,
                leverage=leverage,
                margin_used=margin_used,
            )

        return positions

    # ------------------------------------------------------------------ #
    # Account value                                                        #
    # ------------------------------------------------------------------ #

    async def get_account_value(self) -> float:
        """Return total account equity (USD) from the margin summary."""
        state = await self.get_state()
        # Prefer cross-margin account value when available.
        summary = state.cross_margin_summary or state.margin_summary
        return float(summary.get("accountValue", 0.0))

    # ------------------------------------------------------------------ #
    # Open orders                                                          #
    # ------------------------------------------------------------------ #

    async def get_open_orders(self) -> list[OpenOrder]:
        """Return all resting open orders for the account."""
        raw = await self._call_info("openOrders", {"user": self._address})

        if not isinstance(raw, list):
            logger.error("get_open_orders: unexpected response type %s", type(raw))
            return []

        orders: list[OpenOrder] = []
        for item in raw:
            try:
                orders.append(
                    OpenOrder(
                        oid=int(item["oid"]),
                        coin=str(item["coin"]),
                        side=str(item["side"]),
                        order_type=str(item.get("orderType", "")),
                        limit_px=float(item.get("limitPx", 0.0) or 0.0),
                        sz=float(item.get("sz", 0.0)),
                        orig_sz=float(item.get("origSz", item.get("sz", 0.0))),
                        timestamp=int(item.get("timestamp", 0)),
                        reduce_only=bool(item.get("reduceOnly", False)),
                        cloid=item.get("cloid"),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("get_open_orders: skipping malformed order %s: %s", item, exc)

        return orders
