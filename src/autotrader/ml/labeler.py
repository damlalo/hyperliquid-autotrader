"""
Labels a trade signal with its outcome by simulating forward from entry
in OHLCV data. Used to generate supervised training labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


import pandas as pd


@dataclass
class TradeOutcome:
    won: bool
    r_multiple: float  # actual PnL / initial risk; negative = loss
    bars_to_outcome: int
    outcome_type: str  # "tp" | "sl" | "timeout"


class TradeLabeler:
    """Simulates trade forward in OHLCV data to determine outcome."""

    def label(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        side: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        max_bars: int = 48,
    ) -> TradeOutcome:
        """Walk forward bar-by-bar from entry_idx + 1 and determine outcome.

        Parameters
        ----------
        df:
            Full OHLCV DataFrame with columns t, o, h, l, c, v.
        entry_idx:
            Index of the bar at which the trade was entered (0-based integer).
        side:
            "long" or "short".
        entry_price:
            Actual fill price.
        tp_price:
            Take-profit price level.
        sl_price:
            Stop-loss price level.
        max_bars:
            Maximum bars to simulate before marking as timeout.

        Returns
        -------
        TradeOutcome
        """
        risk = abs(entry_price - sl_price)
        if risk <= 0:
            risk = abs(entry_price) * 0.01 or 1.0  # fallback

        side_sign = 1.0 if side.lower() == "long" else -1.0
        is_long = side_sign > 0

        start = entry_idx + 1
        end = min(start + max_bars, len(df))

        for bar_offset, i in enumerate(range(start, end), start=1):
            row = df.iloc[i]
            bar_high = float(row["h"])
            bar_low = float(row["l"])
            bar_open = float(row["o"])
            bar_close = float(row["c"])

            if is_long:
                hit_sl = bar_low <= sl_price
                hit_tp = bar_high >= tp_price
            else:
                hit_sl = bar_high >= sl_price
                hit_tp = bar_low <= tp_price

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    # Determine which fired first using open price
                    if is_long:
                        # If open is already at or below SL: SL fired first
                        # If open is at or above TP: TP fired first
                        # Otherwise assume SL (conservative)
                        if bar_open <= sl_price:
                            exit_price = sl_price
                            outcome_type = "sl"
                        elif bar_open >= tp_price:
                            exit_price = tp_price
                            outcome_type = "tp"
                        else:
                            exit_price = sl_price
                            outcome_type = "sl"
                    else:  # short
                        if bar_open >= sl_price:
                            exit_price = sl_price
                            outcome_type = "sl"
                        elif bar_open <= tp_price:
                            exit_price = tp_price
                            outcome_type = "tp"
                        else:
                            exit_price = sl_price
                            outcome_type = "sl"
                elif hit_tp:
                    exit_price = tp_price
                    outcome_type = "tp"
                else:
                    exit_price = sl_price
                    outcome_type = "sl"

                pnl = (exit_price - entry_price) * side_sign
                r_multiple = pnl / risk
                won = outcome_type == "tp"
                return TradeOutcome(
                    won=won,
                    r_multiple=r_multiple,
                    bars_to_outcome=bar_offset,
                    outcome_type=outcome_type,
                )

        # Timeout: use close of last bar reached
        last_idx = min(end - 1, len(df) - 1)
        final_price = float(df.iloc[last_idx]["c"])
        pnl = (final_price - entry_price) * side_sign
        r_multiple = pnl / risk
        bars_elapsed = end - start

        return TradeOutcome(
            won=False,
            r_multiple=r_multiple,
            bars_to_outcome=bars_elapsed,
            outcome_type="timeout",
        )

    def label_batch(
        self,
        df: pd.DataFrame,
        trades: List[dict],
    ) -> List[TradeOutcome]:
        """Label a list of trades in bulk.

        Parameters
        ----------
        df:
            Full OHLCV DataFrame shared across all trades.
        trades:
            Each dict must contain keys: entry_idx, side, entry_price,
            tp_price, sl_price.  Optionally: max_bars (default 48).

        Returns
        -------
        list[TradeOutcome] in the same order as trades.
        """
        outcomes: List[TradeOutcome] = []
        for trade in trades:
            outcome = self.label(
                df=df,
                entry_idx=trade["entry_idx"],
                side=trade["side"],
                entry_price=trade["entry_price"],
                tp_price=trade["tp_price"],
                sl_price=trade["sl_price"],
                max_bars=trade.get("max_bars", 48),
            )
            outcomes.append(outcome)
        return outcomes
