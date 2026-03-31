"""
Trailing take-profit and stop-loss manager.

Manages open positions through a multi-phase profit-taking scheme:

  Phase 0 — Entry placed, waiting for fill
  Phase 1 — In trade, tighten stop to breakeven at +0.5R
  Phase 2 — At +1R: close 50% of position (first partial TP)
  Phase 3 — At +1.5R: close 25% more, move stop to +0.5R
  Phase 4 — At +2R+: trail remaining 25% with ATR-based trailing stop

This maximises captured profit while protecting capital after each
milestone. The manager interacts with OrderManager to submit partial
exit orders and update stop levels.

State is persisted to disk so that restarts do not lose phase tracking.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autotrader.execution.order_manager import OrderManager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------


class PositionPhase(IntEnum):
    WAITING = 0   # entry placed, awaiting fill
    PHASE1 = 1    # in trade, watching for +0.5R breakeven move
    PHASE2 = 2    # stop at BE, watching for +1R partial exit
    PHASE3 = 3    # 50% out, watching for +1.5R partial exit
    PHASE4 = 4    # 75% out, trailing ATR stop on remainder
    CLOSED = 5    # position fully closed


@dataclass
class ManagedPosition:
    """Full state for a single tracked position."""

    coin: str
    side: str                          # "long" | "short"
    entry_price: float
    initial_size: float                # full position size in contracts/coins
    remaining_size: float              # what's still open
    initial_stop: float
    initial_tp: float
    current_stop: float
    phase: PositionPhase
    risk_per_unit: float               # abs(entry_price - initial_stop)
    partial_exits: list[dict] = field(default_factory=list)
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def improves_stop(new_stop: float, current_stop: float, side: str) -> bool:
    """Return True if moving stop to new_stop is beneficial for this side.

    For longs: stop should only move up (locking in more profit).
    For shorts: stop should only move down.
    """
    if side == "long":
        return new_stop > current_stop
    return new_stop < current_stop


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TrailingTPManager:
    """Multi-phase trailing take-profit / stop-loss manager.

    Parameters
    ----------
    order_manager:
        An :class:`~autotrader.execution.order_manager.OrderManager` instance
        used to submit partial exit orders.
    state_dir:
        Directory for persisting position state to disk.  Defaults to
        ``~/.autotrader/tp_state``.
    """

    def __init__(
        self,
        order_manager: "OrderManager",
        state_dir: Path | None = None,
    ) -> None:
        self._order_manager = order_manager
        self._state_dir: Path = state_dir or Path(
            os.path.expanduser("~/.autotrader/tp_state")
        )
        self._positions: dict[str, ManagedPosition] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_position(
        self,
        coin: str,
        side: str,
        entry_price: float,
        size: float,
        stop: float,
        tp: float,
    ) -> None:
        """Register a newly filled position with the manager.

        Parameters
        ----------
        coin:
            Instrument name (e.g. "BTC").
        side:
            ``"long"`` or ``"short"``.
        entry_price:
            Actual fill price.
        size:
            Full position size in native units.
        stop:
            Initial stop-loss price.
        tp:
            Initial take-profit price (informational; phased exits override).
        """
        risk_per_unit = abs(entry_price - stop)
        pos = ManagedPosition(
            coin=coin,
            side=side,
            entry_price=entry_price,
            initial_size=size,
            remaining_size=size,
            initial_stop=stop,
            initial_tp=tp,
            current_stop=stop,
            phase=PositionPhase.PHASE1,
            risk_per_unit=risk_per_unit,
            partial_exits=[],
            opened_at=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        self._positions[coin] = pos
        self._save_state()
        log.info(
            "tp_manager: opened %s %s @ %.4f risk=%.4f",
            coin,
            side,
            entry_price,
            risk_per_unit,
        )

    def close_position(self, coin: str, reason: str = "manual") -> None:
        """Remove a position from management (e.g. manually closed on exchange).

        Parameters
        ----------
        coin:
            Instrument name.
        reason:
            Informational reason string for logging.
        """
        if coin in self._positions:
            log.info("tp_manager: closing %s reason=%s", coin, reason)
            del self._positions[coin]
            self._save_state()
            # Remove persisted file
            state_file = self._state_dir / f"{coin}.json"
            if state_file.exists():
                try:
                    state_file.unlink()
                except OSError as exc:
                    log.warning("tp_manager: could not remove state file %s: %s", state_file, exc)

    async def update(
        self,
        coin: str,
        current_price: float,
        current_atr: float,
    ) -> list[dict]:
        """Evaluate phase transitions and trailing stop for one position.

        Call this once per price tick / loop iteration for each managed coin.

        Parameters
        ----------
        coin:
            Instrument name.
        current_price:
            Current market price.
        current_atr:
            Current ATR value (used for Phase 4 trailing stop distance).

        Returns
        -------
        list[dict]
            Action dicts describing what happened this tick.  Each dict
            contains at minimum ``{"action": str, "coin": str}``.  Callers
            should pass the list to :meth:`process_actions`.
        """
        if coin not in self._positions:
            return []

        pos = self._positions[coin]
        actions: list[dict] = []

        r = pos.risk_per_unit
        if r <= 0:
            log.warning("tp_manager: %s has zero risk_per_unit — skipping", coin)
            return []

        direction = 1 if pos.side == "long" else -1
        current_r = (current_price - pos.entry_price) * direction / r

        # ------------------------------------------------------------------
        # Phase 1: move stop to breakeven at +0.5R
        # ------------------------------------------------------------------
        if pos.phase == PositionPhase.PHASE1 and current_r >= 0.5:
            new_stop = pos.entry_price  # breakeven
            if improves_stop(new_stop, pos.current_stop, pos.side):
                pos.current_stop = new_stop
                pos.phase = PositionPhase.PHASE2
                actions.append(
                    {
                        "action": "move_stop_to_be",
                        "coin": coin,
                        "new_stop": new_stop,
                    }
                )
                log.info(
                    "tp_manager: %s Phase1→Phase2 — stop moved to BE %.4f (+0.5R hit)",
                    coin,
                    new_stop,
                )

        # ------------------------------------------------------------------
        # Phase 2: partial exit 50% at +1.0R
        # ------------------------------------------------------------------
        if pos.phase == PositionPhase.PHASE2 and current_r >= 1.0:
            exit_size = pos.initial_size * 0.50
            pos.remaining_size -= exit_size
            pos.phase = PositionPhase.PHASE3
            exit_record = {
                "phase": 2,
                "size": exit_size,
                "price": current_price,
                "r": current_r,
            }
            pos.partial_exits.append(exit_record)
            actions.append(
                {
                    "action": "partial_exit",
                    "coin": coin,
                    "size": exit_size,
                    "price": current_price,
                    "reason": "1R_target",
                }
            )
            log.info(
                "tp_manager: %s Phase2→Phase3 — partial exit %.6f @ %.4f (+1R hit)",
                coin,
                exit_size,
                current_price,
            )

        # ------------------------------------------------------------------
        # Phase 3: partial exit 25% at +1.5R, move stop to +0.5R
        # ------------------------------------------------------------------
        if pos.phase == PositionPhase.PHASE3 and current_r >= 1.5:
            exit_size = pos.initial_size * 0.25
            pos.remaining_size -= exit_size
            new_stop = pos.entry_price + direction * r * 0.5
            if improves_stop(new_stop, pos.current_stop, pos.side):
                pos.current_stop = new_stop
            pos.phase = PositionPhase.PHASE4
            exit_record = {
                "phase": 3,
                "size": exit_size,
                "price": current_price,
                "r": current_r,
            }
            pos.partial_exits.append(exit_record)
            actions.append(
                {
                    "action": "partial_exit",
                    "coin": coin,
                    "size": exit_size,
                    "price": current_price,
                    "reason": "1.5R_target",
                }
            )
            actions.append(
                {
                    "action": "move_stop",
                    "coin": coin,
                    "new_stop": pos.current_stop,
                }
            )
            log.info(
                "tp_manager: %s Phase3→Phase4 — partial exit %.6f @ %.4f (+1.5R hit) stop→%.4f",
                coin,
                exit_size,
                current_price,
                pos.current_stop,
            )

        # ------------------------------------------------------------------
        # Phase 4: ATR trailing stop on remaining 25%
        # ------------------------------------------------------------------
        if pos.phase == PositionPhase.PHASE4:
            trail_stop = current_price - direction * current_atr * 1.5
            if improves_stop(trail_stop, pos.current_stop, pos.side):
                pos.current_stop = trail_stop
                actions.append(
                    {
                        "action": "trail_stop",
                        "coin": coin,
                        "new_stop": trail_stop,
                    }
                )
                log.debug(
                    "tp_manager: %s trail stop updated → %.4f (ATR=%.4f)",
                    coin,
                    trail_stop,
                    current_atr,
                )

        # ------------------------------------------------------------------
        # Stop-hit check (any phase)
        # ------------------------------------------------------------------
        stopped_out = (
            pos.side == "long" and current_price <= pos.current_stop
        ) or (
            pos.side == "short" and current_price >= pos.current_stop
        )

        if stopped_out and pos.remaining_size > 0:
            actions.append(
                {
                    "action": "close_position",
                    "coin": coin,
                    "size": pos.remaining_size,
                    "price": current_price,
                    "reason": "stop_hit",
                }
            )
            log.info(
                "tp_manager: %s STOPPED OUT — closing %.6f @ %.4f (stop=%.4f)",
                coin,
                pos.remaining_size,
                current_price,
                pos.current_stop,
            )
            pos.remaining_size = 0.0
            pos.phase = PositionPhase.CLOSED

        # ------------------------------------------------------------------
        # Bookkeeping
        # ------------------------------------------------------------------
        pos.last_updated = datetime.now(timezone.utc)
        self._save_state()

        return actions

    async def process_actions(
        self,
        actions: list[dict],
        current_prices: dict[str, float],
    ) -> None:
        """Execute actions returned by :meth:`update`.

        Submits exit orders via the order manager for ``partial_exit`` and
        ``close_position`` actions.  Stop-movement actions are logged only;
        actual exchange stop amendments require a cancel+replace cycle that
        the caller is responsible for (e.g. via ``submit_stop``).

        Parameters
        ----------
        actions:
            List of action dicts as returned by :meth:`update`.
        current_prices:
            Current prices keyed by coin (used to derive exit limit price
            when the action price is stale).
        """
        for action in actions:
            act = action.get("action")
            coin = action.get("coin", "")
            pos = self._positions.get(coin)

            log.info("tp_manager: processing action=%s coin=%s detail=%s", act, coin, action)

            if act in ("partial_exit", "close_position"):
                size = action.get("size", 0.0)
                price = action.get("price") or current_prices.get(coin, 0.0)
                reason = action.get("reason", act)

                if size <= 0 or price <= 0:
                    log.warning(
                        "tp_manager: skipping %s — invalid size=%.6f price=%.4f",
                        act,
                        size,
                        price,
                    )
                    continue

                side = pos.side if pos is not None else None
                try:
                    await self._order_manager.submit_exit(
                        coin=coin,
                        size=size,
                        exit_px=price,
                        reduce_only=True,
                        existing_side=side,
                    )
                    log.info(
                        "tp_manager: exit submitted %s %.6f @ %.4f reason=%s",
                        coin,
                        size,
                        price,
                        reason,
                    )
                except Exception as exc:
                    log.error(
                        "tp_manager: failed to submit exit for %s: %s",
                        coin,
                        exc,
                    )

            elif act in ("move_stop", "trail_stop", "move_stop_to_be"):
                new_stop = action.get("new_stop")
                log.info(
                    "tp_manager: [stop-update] %s %s new_stop=%.4f "
                    "(cancel+replace required on exchange)",
                    act,
                    coin,
                    new_stop if new_stop is not None else float("nan"),
                )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, coin: str) -> ManagedPosition | None:
        """Return the managed position for *coin*, or ``None``."""
        return self._positions.get(coin)

    def active_coins(self) -> list[str]:
        """Return coins with positions that are not yet CLOSED."""
        return [
            coin
            for coin, pos in self._positions.items()
            if pos.phase != PositionPhase.CLOSED
        ]

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist all managed positions to individual JSON files."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        for coin, pos in self._positions.items():
            state_file = self._state_dir / f"{coin}.json"
            data = {
                "coin": pos.coin,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "initial_size": pos.initial_size,
                "remaining_size": pos.remaining_size,
                "initial_stop": pos.initial_stop,
                "initial_tp": pos.initial_tp,
                "current_stop": pos.current_stop,
                "phase": pos.phase.value,
                "risk_per_unit": pos.risk_per_unit,
                "partial_exits": pos.partial_exits,
                "opened_at": pos.opened_at.isoformat(),
                "last_updated": pos.last_updated.isoformat(),
            }
            try:
                state_file.write_text(json.dumps(data, indent=2))
            except OSError as exc:
                log.error("tp_manager: failed to save state for %s: %s", coin, exc)

    def _load_state(self) -> None:
        """Restore persisted positions from disk on startup."""
        if not self._state_dir.exists():
            return

        for state_file in self._state_dir.glob("*.json"):
            try:
                data = json.loads(state_file.read_text())
                pos = ManagedPosition(
                    coin=data["coin"],
                    side=data["side"],
                    entry_price=float(data["entry_price"]),
                    initial_size=float(data["initial_size"]),
                    remaining_size=float(data["remaining_size"]),
                    initial_stop=float(data["initial_stop"]),
                    initial_tp=float(data["initial_tp"]),
                    current_stop=float(data["current_stop"]),
                    phase=PositionPhase(int(data["phase"])),
                    risk_per_unit=float(data["risk_per_unit"]),
                    partial_exits=data.get("partial_exits", []),
                    opened_at=datetime.fromisoformat(data["opened_at"]),
                    last_updated=datetime.fromisoformat(data["last_updated"]),
                )
                self._positions[pos.coin] = pos
                log.info(
                    "tp_manager: restored %s %s phase=%s remaining=%.6f",
                    pos.coin,
                    pos.side,
                    pos.phase.name,
                    pos.remaining_size,
                )
            except Exception as exc:
                log.warning(
                    "tp_manager: could not load state from %s: %s",
                    state_file,
                    exc,
                )
