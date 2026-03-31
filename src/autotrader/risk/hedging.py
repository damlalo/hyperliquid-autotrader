"""
Portfolio delta hedging. Computes net directional exposure and recommends
a hedge trade when the portfolio becomes too one-sided.

Philosophy: we don't want to be >65% net long or net short as a fraction
of total equity. When breached, place a small opposing position in the
most liquid available coin (BTC or ETH) to bring delta back toward neutral.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HedgeRecommendation:
    """A recommended hedge trade to rebalance net portfolio delta.

    Attributes
    ----------
    coin:
        Instrument to hedge with (e.g. "BTC").
    side:
        ``"long"`` or ``"short"`` — direction of the hedge order.
    size_usd:
        Notional USD size of the recommended hedge.
    current_delta_pct:
        Net delta / equity **before** applying the hedge.
    target_delta_pct:
        Estimated net delta / equity **after** applying the hedge.
    reason:
        Human-readable explanation for the hedge.
    """

    coin: str
    side: str
    size_usd: float
    current_delta_pct: float
    target_delta_pct: float
    reason: str


@dataclass
class DeltaSnapshot:
    """A point-in-time picture of portfolio directional exposure.

    Attributes
    ----------
    net_delta_usd:
        Signed net notional (positive = net long, negative = net short).
    gross_exposure_usd:
        Sum of absolute notionals across all positions.
    net_delta_pct:
        ``net_delta_usd / equity`` — how skewed the book is.
    per_coin:
        Mapping of coin → signed notional for each open position.
    timestamp:
        UTC datetime when the snapshot was computed.
    """

    net_delta_usd: float
    gross_exposure_usd: float
    net_delta_pct: float
    per_coin: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Hedger
# ---------------------------------------------------------------------------


class PortfolioHedger:
    """Compute net portfolio delta and recommend hedges when skewed.

    Parameters
    ----------
    delta_threshold:
        Fractional threshold (relative to equity) at which a hedge is
        triggered.  Default ``0.65`` means hedge when >65% net long/short.
    target_delta:
        Fractional net delta to aim for after hedging.  Default ``0.30``.
    hedge_coins:
        Ordered list of preferred hedge instruments.  The first coin in the
        list that is available and not already hedged in the same direction
        will be used.
    """

    def __init__(
        self,
        delta_threshold: float = 0.65,
        target_delta: float = 0.30,
        hedge_coins: list[str] | None = None,
    ) -> None:
        self._threshold = delta_threshold
        self._target = target_delta
        self._hedge_coins: list[str] = hedge_coins or ["BTC", "ETH"]
        # coin → "long" | "short"
        self._active_hedges: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Delta computation
    # ------------------------------------------------------------------

    def compute_delta(
        self,
        positions: dict[str, Any],
        equity: float,
    ) -> DeltaSnapshot:
        """Compute a :class:`DeltaSnapshot` from the current open positions.

        Supports both Pydantic model objects (attribute access) and plain
        dicts (key access) for each position value.

        Parameters
        ----------
        positions:
            Mapping of coin → position object or dict.  Each entry must
            expose ``szi`` (signed size; positive = long) and ``entryPx``
            (entry price).  ``marginUsed`` is used as a fallback when size
            data is unavailable.
        equity:
            Total account equity in USD (used to normalise net delta).

        Returns
        -------
        DeltaSnapshot
        """
        net_delta_usd = 0.0
        gross_exposure_usd = 0.0
        per_coin: dict[str, float] = {}

        for coin, position in positions.items():
            signed_size = _get_field(position, "szi", default=None)
            entry_px = _get_field(position, "entryPx", default=None)
            margin_used = _get_field(position, "marginUsed", default=None)

            if signed_size is not None and entry_px is not None:
                try:
                    szi = float(signed_size)
                    epx = float(entry_px)
                    signed_notional = szi * epx
                    abs_notional = abs(signed_notional)
                except (TypeError, ValueError):
                    log.warning(
                        "hedging: could not parse szi/entryPx for %s — skipping", coin
                    )
                    continue
            elif margin_used is not None:
                # Fallback: use margin as a proxy for exposure (no leverage info)
                try:
                    abs_notional = float(margin_used)
                    # Without szi sign we cannot determine direction; skip delta
                    log.debug(
                        "hedging: %s — no szi/entryPx, using marginUsed=%.2f as abs notional",
                        coin,
                        abs_notional,
                    )
                    signed_notional = abs_notional  # assume long if we can't tell
                except (TypeError, ValueError):
                    log.warning("hedging: could not parse marginUsed for %s — skipping", coin)
                    continue
            else:
                log.debug("hedging: %s — no usable position fields, skipping", coin)
                continue

            per_coin[coin] = signed_notional
            net_delta_usd += signed_notional
            gross_exposure_usd += abs_notional

        safe_equity = equity if equity > 0 else 1.0
        net_delta_pct = net_delta_usd / safe_equity

        snapshot = DeltaSnapshot(
            net_delta_usd=net_delta_usd,
            gross_exposure_usd=gross_exposure_usd,
            net_delta_pct=net_delta_pct,
            per_coin=per_coin,
            timestamp=datetime.now(timezone.utc),
        )

        log.debug(
            "hedging: delta snapshot net=%.2f (%.1f%%) gross=%.2f equity=%.2f",
            net_delta_usd,
            net_delta_pct * 100,
            gross_exposure_usd,
            equity,
        )
        return snapshot

    # ------------------------------------------------------------------
    # Hedge decision
    # ------------------------------------------------------------------

    def should_hedge(self, snapshot: DeltaSnapshot) -> bool:
        """Return ``True`` when the portfolio delta exceeds the threshold.

        Parameters
        ----------
        snapshot:
            Delta snapshot from :meth:`compute_delta`.
        """
        return abs(snapshot.net_delta_pct) > self._threshold

    def recommend_hedge(
        self,
        snapshot: DeltaSnapshot,
        equity: float,
        available_coins: list[str],
    ) -> HedgeRecommendation | None:
        """Build a hedge recommendation if the portfolio is over-extended.

        Returns ``None`` when no hedge is needed or no valid hedge instrument
        can be found.

        Parameters
        ----------
        snapshot:
            Current delta snapshot.
        equity:
            Total account equity in USD.
        available_coins:
            Coins currently tradeable on the exchange / in the universe.

        Returns
        -------
        HedgeRecommendation or None
        """
        if not self.should_hedge(snapshot):
            return None

        # Direction: offset the current skew
        net_long = snapshot.net_delta_pct > 0
        hedge_side = "short" if net_long else "long"

        # Select the best available hedge instrument
        hedge_coin: str | None = None
        for candidate in self._hedge_coins:
            if candidate not in available_coins:
                continue
            existing_hedge_side = self._active_hedges.get(candidate)
            if existing_hedge_side == hedge_side:
                # Already have a hedge in this direction on this coin
                log.debug(
                    "hedging: %s already hedged %s — skipping as candidate",
                    candidate,
                    hedge_side,
                )
                continue
            hedge_coin = candidate
            break

        if hedge_coin is None:
            log.warning(
                "hedging: no valid hedge instrument found "
                "(available=%s, preferred=%s, active_hedges=%s)",
                available_coins,
                self._hedge_coins,
                self._active_hedges,
            )
            return None

        # Compute size: enough to bring |net_delta| down to target level
        safe_equity = equity if equity > 0 else 1.0
        # Amount of notional we need to add in the hedge direction
        #   net_long: hedge is short → reduces net_delta_usd
        #   net_short: hedge is long → increases net_delta_usd (toward 0)
        desired_abs_delta = self._target * safe_equity
        raw_size_usd = abs(snapshot.net_delta_usd) - desired_abs_delta

        # Clamp: minimum $50, maximum 20% of equity
        max_size = safe_equity * 0.20
        size_usd = max(min(abs(raw_size_usd), max_size), 50.0)

        # Estimate delta after hedge
        hedge_sign = -1 if hedge_side == "short" else 1
        target_delta_pct = snapshot.net_delta_pct + hedge_sign * (size_usd / safe_equity)

        reason = (
            f"Net delta {snapshot.net_delta_pct:+.1%} exceeds threshold "
            f"{self._threshold:.0%}; hedging {hedge_side} {hedge_coin} "
            f"${size_usd:,.0f} to target ~{target_delta_pct:+.1%}"
        )

        log.info("hedging: recommendation — %s", reason)

        return HedgeRecommendation(
            coin=hedge_coin,
            side=hedge_side,
            size_usd=size_usd,
            current_delta_pct=snapshot.net_delta_pct,
            target_delta_pct=target_delta_pct,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Hedge tracking
    # ------------------------------------------------------------------

    def register_hedge(self, coin: str, side: str) -> None:
        """Record that a hedge has been placed on *coin*.

        Parameters
        ----------
        coin:
            Instrument being hedged.
        side:
            ``"long"`` or ``"short"`` — direction of the hedge order.
        """
        self._active_hedges[coin] = side
        log.info("hedging: registered hedge %s %s", coin, side)

    def deregister_hedge(self, coin: str) -> None:
        """Remove the active-hedge record for *coin* (e.g. after it closes).

        Parameters
        ----------
        coin:
            Instrument whose hedge has been closed.
        """
        removed = self._active_hedges.pop(coin, None)
        if removed is not None:
            log.info("hedging: deregistered hedge %s (was %s)", coin, removed)

    def is_hedge(self, coin: str) -> bool:
        """Return ``True`` if *coin* currently has an active hedge recorded."""
        return coin in self._active_hedges

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------

    def hedge_summary(self, snapshot: DeltaSnapshot) -> str:
        """Return a concise human-readable summary of portfolio delta.

        Example output::

            Net delta: +34.2% ($17,100 long bias). Top: BTC +$10,000, ETH +$7,100

        Parameters
        ----------
        snapshot:
            Delta snapshot from :meth:`compute_delta`.
        """
        sign = "+" if snapshot.net_delta_usd >= 0 else ""
        bias = "long bias" if snapshot.net_delta_usd >= 0 else "short bias"
        pct_str = f"{sign}{snapshot.net_delta_pct:.1%}"
        usd_str = f"${abs(snapshot.net_delta_usd):,.0f}"

        # Top contributors by absolute notional (descending)
        top = sorted(
            snapshot.per_coin.items(),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )[:5]

        top_parts: list[str] = []
        for coin, notional in top:
            prefix = "+" if notional >= 0 else "-"
            top_parts.append(f"{coin} {prefix}${abs(notional):,.0f}")

        top_str = ", ".join(top_parts) if top_parts else "no positions"
        return f"Net delta: {pct_str} ({usd_str} {bias}). Top: {top_str}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Retrieve *key* from *obj* whether it is a dict or an attribute-bearing object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
