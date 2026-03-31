"""Main trading loop orchestrator."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from autotrader.execution.broker import Broker
    from autotrader.execution.order_manager import OrderManager
    from autotrader.execution.tp_manager import TrailingTPManager
    from autotrader.governance.drift import DriftDetector, DriftResult
    from autotrader.hl.client import HyperliquidClient
    from autotrader.hl.types import MarketContext
    from autotrader.hl.ws import HyperliquidWS
    from autotrader.levels.detector import LevelDetector
    from autotrader.ml.model import SignalQualityModel
    from autotrader.monitoring.alerts import AlertManager
    from autotrader.monitoring.metrics import TradingMetrics
    from autotrader.news.guard import NewsGuard
    from autotrader.regimes.classifier import RegimeClassifier
    from autotrader.regimes.hysteresis import HysteresisFilter
    from autotrader.risk.approvals import TradeApprover
    from autotrader.risk.hedging import PortfolioHedger
    from autotrader.runtime.kill_switch import KillSwitch
    from autotrader.store.datastore import DataStore
    from autotrader.strategies.base import BaseStrategy

log = logging.getLogger(__name__)


def _abs_diff(a: float, b: float) -> float:
    return abs(a - b)


class TradingScheduler:
    """Orchestrates the full trading loop.

    Responsibilities per iteration:
    1. Kill switch check (abort immediately if triggered)
    2. Incremental candle update
    3. Account state refresh (equity, positions, open orders)
    4. Portfolio constraint health check
    5. Per-coin: regime classification → strategy signals → risk approval → order submission
    6. Open order management (chasing, expiry)
    7. Drift detection
    8. Kill switch condition evaluation
    9. Metrics emission
    """

    def __init__(
        self,
        regime_classifier: "RegimeClassifier",
        regime_filters: dict[str, "HysteresisFilter"],  # coin -> HysteresisFilter
        trade_approver: "TradeApprover",
        drift_detector: Optional["DriftDetector"] = None,
        alert_manager: Optional["AlertManager"] = None,
        metrics: Optional["TradingMetrics"] = None,
        # New: intelligence + risk modules
        level_detector: Optional["LevelDetector"] = None,
        news_guard: Optional["NewsGuard"] = None,
        signal_quality_model: Optional["SignalQualityModel"] = None,
        tp_manager: Optional["TrailingTPManager"] = None,
        portfolio_hedger: Optional["PortfolioHedger"] = None,
    ) -> None:
        self._regime_classifier = regime_classifier
        self._regime_filters = regime_filters
        self._trade_approver = trade_approver
        self._drift_detector = drift_detector
        self._alerts = alert_manager
        self._metrics = metrics
        self._level_detector = level_detector
        self._news_guard = news_guard
        self._signal_quality_model = signal_quality_model
        self._tp_manager = tp_manager
        self._portfolio_hedger = portfolio_hedger

        self._error_timestamps: list[float] = []
        self._peak_equity: float = 0.0
        self._pnl_today: float = 0.0
        self._last_data_ts: int = 0
        self._live_trade_log_today: list[dict] = []
        # Cache news state so we only refresh once per loop, not per coin
        self._news_state: Optional[Any] = None

    # ------------------------------------------------------------------
    # Single loop iteration
    # ------------------------------------------------------------------

    async def run_once(
        self,
        strategies: list["BaseStrategy"],
        client: "HyperliquidClient",
        store: "DataStore",
        order_manager: "OrderManager",
        broker: "Broker",
        kill_switch: "KillSwitch",
        config: dict,
        coins: list[str],
    ) -> None:
        """Execute one full loop iteration."""
        import pandas as pd
        from autotrader.data.collectors.candles import CandleCollector
        from autotrader.data.collectors.user_state import UserStateCollector
        from autotrader.hl.types import MarketContext
        from autotrader.risk.approvals import TradeApprover
        from autotrader.risk.constraints import ConstraintChecker

        loop_start = time.monotonic()

        # Step 1: Kill switch guard
        if kill_switch.is_triggered():
            event = kill_switch.get_event()
            log.critical("loop: kill switch active — %s — halting", event.trigger)
            return

        intervals: list[str] = config.get("timeframes", {}).get("regime", ["1h"]) + \
                               config.get("timeframes", {}).get("signal", ["15m"])
        intervals = list(dict.fromkeys(intervals))  # deduplicate, preserve order

        # Step 2: Incremental candle update
        try:
            collector = CandleCollector(client=client, store=store)
            await collector.update_all(coins=coins, intervals=intervals)
            self._last_data_ts = int(time.time() * 1_000)
        except Exception as exc:
            self._record_error()
            log.error("loop: candle update failed: %s", exc)

        # Step 3: Account state
        equity = 0.0
        positions: dict = {}
        try:
            address = config.get("hyperliquid", {}).get("account_address", "")
            user_collector = UserStateCollector(client=client, account_address=address)
            equity = await user_collector.get_account_value()
            positions = await user_collector.get_positions()
        except Exception as exc:
            self._record_error()
            log.error("loop: account state fetch failed: %s", exc)

        if equity <= 0.0:
            equity = 1.0  # avoid division by zero in paper mode

        if self._peak_equity <= 0.0:
            self._peak_equity = equity
        else:
            self._peak_equity = max(self._peak_equity, equity)

        # Step 3b: News guard — refresh once per loop, apply to all coins
        news_size_mult = 1.0
        if self._news_guard is not None:
            try:
                self._news_state = await self._news_guard.get_state()
                news_size_mult = self._news_guard.position_size_multiplier(self._news_state)
                if not self._news_guard.should_allow_new_entries(self._news_state):
                    log.warning(
                        "loop: news guard BLOCKING new entries — %s (defensiveness=%.2f)",
                        self._news_state.reason,
                        self._news_state.defensiveness,
                    )
                elif news_size_mult < 1.0:
                    log.info(
                        "loop: news guard reducing sizes to %.0f%% — %s",
                        news_size_mult * 100,
                        self._news_state.reason,
                    )
                # Inject into config so ConstraintChecker + PositionSizer see it
                config = {**config, "_news_size_mult": news_size_mult}
            except Exception as exc:
                log.debug("loop: news guard refresh failed: %s", exc)

        # Step 4: Portfolio health check
        constraint_checker = ConstraintChecker()
        current_notional = sum(
            abs(getattr(p, "marginUsed", 0.0) * getattr(p, "leverage", 1.0))
            for p in positions.values()
        )
        portfolio_check = constraint_checker.check_portfolio(
            equity=equity,
            peak_equity=self._peak_equity,
            open_positions=positions,
            pnl_today=self._pnl_today,
            pnl_this_week=self._pnl_today,  # simplified for now
            config=config,
        )
        if not portfolio_check.ok:
            log.warning("loop: portfolio constraint violations: %s", portfolio_check.violations)
            for v in portfolio_check.violations:
                log.warning("  VIOLATION: %s", v)

        # Step 5: Per-coin signal computation (parallel) → approval + submission (serial)
        #
        # Phase A runs concurrently: candle reads, regime classification, strategy
        # signal computation.  These are CPU/IO-bound with no shared mutations.
        #
        # Phase B is serial: TradeApprover evaluates portfolio-level constraints
        # using the same `positions` snapshot, so serialising prevents two coins
        # from both being approved against a limit that only one can satisfy.
        signal_interval = config.get("timeframes", {}).get("signal", ["15m"])[0]
        regime_interval = config.get("timeframes", {}).get("regime", ["1h"])[0]

        coin_results = await asyncio.gather(
            *[
                self._compute_coin_signal(
                    coin=coin,
                    store=store,
                    intervals=intervals,
                    regime_interval=regime_interval,
                    signal_interval=signal_interval,
                    strategies=strategies,
                    positions=positions,
                    equity=equity,
                )
                for coin in coins
            ],
            return_exceptions=True,
        )

        for coin, result in zip(coins, coin_results):
            if isinstance(result, Exception):
                log.debug("loop: coin %s signal error: %s", coin, result)
                continue

            signal, current_price, _analysis_extras = result
            # Push analysis state to dashboard (best-effort, non-blocking)
            try:
                from autotrader.monitoring.web import update_analysis_state
                _news_def = round(getattr(self._news_state, "defensiveness", 0.0), 3) if self._news_state else 0.0
                _news_reason = getattr(self._news_state, "reason", "") if self._news_state else ""
                update_analysis_state(coin, {
                    **_analysis_extras,
                    "news_defensiveness": _news_def,
                    "news_reason": _news_reason,
                })
            except Exception:
                pass
            if signal is None or signal.side == "flat" or current_price <= 0.0:
                continue

            try:
                approval = self._trade_approver.evaluate(
                    signal=signal,
                    coin=coin,
                    current_price=current_price,
                    equity=equity,
                    peak_equity=self._peak_equity,
                    positions=positions,
                    asset_ctx=None,
                    config=config,
                )
            except Exception as exc:
                log.warning("loop: approval error for %s: %s", coin, exc)
                continue

            if not approval.approved:
                log.debug("loop: signal rejected for %s — %s", coin, approval.rejection_reason)
                continue

            try:
                await order_manager.submit_entry(
                    signal=signal,
                    approval=approval,
                    coin=coin,
                    current_px=current_price,
                )
                log.info("loop: entry submitted: %s side=%s", coin, signal.side)
                # Register with trailing TP manager
                if self._tp_manager is not None and signal.entry and signal.stop and signal.take_profit:
                    size = getattr(approval.size_result, "size_native", 0.0) if approval.size_result else 0.0
                    if size > 0:
                        self._tp_manager.open_position(
                            coin=coin,
                            side=signal.side,
                            entry_price=signal.entry,
                            size=size,
                            stop=signal.stop,
                            tp=signal.take_profit,
                        )
                # Update local positions snapshot so the next coin's approval
                # sees the reduced available notional from this submission.
                positions[coin] = signal
            except Exception as exc:
                self._record_error()
                log.error("loop: submit_entry failed for %s: %s", coin, exc)

        # Step 6: Manage open orders (chasing, expiry) + trailing TP/SL
        try:
            current_prices: dict[str, float] = {}
            current_atrs: dict[str, float] = {}
            for coin in coins:
                df = store.read_candles(coin, signal_interval)
                if df is not None and len(df) > 0:
                    current_prices[coin] = float(df["c"].iloc[-1])
                    if len(df) >= 15:
                        import numpy as np
                        tr = np.maximum(
                            df["h"].values[-14:] - df["l"].values[-14:],
                            np.abs(df["h"].values[-14:] - df["c"].values[-15:-1]),
                        )
                        current_atrs[coin] = float(np.mean(np.maximum(tr, np.abs(df["l"].values[-14:] - df["c"].values[-15:-1]))))
            await order_manager.manage_open_orders(current_prices)

            # Trailing TP/SL updates
            if self._tp_manager is not None:
                for coin in list(self._tp_manager.active_coins()):
                    price = current_prices.get(coin)
                    atr = current_atrs.get(coin, 0.0)
                    if price and atr:
                        try:
                            actions = await self._tp_manager.update(coin, price, atr)
                            if actions:
                                await self._tp_manager.process_actions(actions, current_prices)
                        except Exception as exc:
                            log.debug("loop: tp_manager update failed for %s: %s", coin, exc)
        except Exception as exc:
            self._record_error()
            log.error("loop: manage_open_orders failed: %s", exc)

        # Step 6b: Portfolio hedging check
        if self._portfolio_hedger is not None:
            try:
                snap = self._portfolio_hedger.compute_delta(positions, equity)
                if self._portfolio_hedger.should_hedge(snap):
                    rec = self._portfolio_hedger.recommend_hedge(snap, equity, coins)
                    if rec is not None:
                        log.warning(
                            "loop: hedge recommended — %s %s $%.0f (net_delta=%.1f%%)",
                            rec.side, rec.coin, rec.size_usd, snap.net_delta_pct * 100,
                        )
                        # Build a minimal hedge signal and submit via order_manager
                        from autotrader.strategies.base import Signal
                        hedge_signal = Signal(
                            side=rec.side,
                            entry=current_prices.get(rec.coin),
                            stop=None,
                            take_profit=None,
                            confidence=0.8,
                            metadata={"is_hedge": True, "reason": rec.reason},
                        )
                        if hedge_signal.entry:
                            hedge_approval = self._trade_approver.evaluate(
                                signal=hedge_signal,
                                coin=rec.coin,
                                current_price=hedge_signal.entry,
                                equity=equity,
                                peak_equity=self._peak_equity,
                                positions=positions,
                                asset_ctx=None,
                                config={**config, "risk": {**config.get("risk", {}), "max_risk_per_trade_pct": 0.01}},
                            )
                            if hedge_approval.approved:
                                await order_manager.submit_entry(
                                    signal=hedge_signal,
                                    approval=hedge_approval,
                                    coin=rec.coin,
                                    current_px=hedge_signal.entry,
                                )
                                self._portfolio_hedger.register_hedge(rec.coin, rec.side)
                                log.info("loop: hedge order submitted for %s", rec.coin)
            except Exception as exc:
                log.debug("loop: portfolio hedge check failed: %s", exc)

        # Step 6c: Feed closed trades to ML trainer for incremental learning
        # (We proxy "closed" as any TP-manager position that reached CLOSED phase)
        if self._signal_quality_model is not None and self._tp_manager is not None:
            try:
                from autotrader.ml.features import FeatureExtractor
                from autotrader.execution.tp_manager import PositionPhase
                fe = FeatureExtractor()
                for coin, pos in list(self._tp_manager._positions.items()):
                    if pos.phase == PositionPhase.CLOSED and len(pos.partial_exits) > 0:
                        # Compute outcome: net R from all partial exits
                        r_sum = sum(e.get("r", 0.0) for e in pos.partial_exits)
                        label = 1 if r_sum > 0 else 0
                        # We don't have the original feature vector, so skip for now
                        # (the paper_sim and live trade recorder will supply proper vectors)
                        pass
            except Exception:
                pass

        # Step 7: Drift detection
        drift_result = None
        if self._drift_detector is not None and self._live_trade_log_today:
            try:
                import pandas as pd
                live_df = pd.DataFrame(self._live_trade_log_today)
                current_dd = (equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0.0
                max_allowed_dd = float(config.get("risk", {}).get("max_drawdown_pct", 0.18))
                drift_result = self._drift_detector.check(
                    live_trades=live_df,
                    backtest_trades=pd.DataFrame(),
                    current_drawdown=current_dd,
                    max_allowed_drawdown=max_allowed_dd,
                    live_slippage_bps=3.0,
                )
                if drift_result.detected:
                    log.warning("drift: %s — action=%s", drift_result.severity, drift_result.action)
                    if self._alerts:
                        await self._alerts.drift_detected(drift_result.severity, drift_result.action)
            except Exception as exc:
                log.debug("loop: drift check failed: %s", exc)

        # Step 8: Kill switch condition evaluation
        errors_last_hour = self._errors_last_hour()
        try:
            ks_event = await kill_switch.check_and_trigger(
                equity=equity,
                peak_equity=self._peak_equity,
                pnl_today=self._pnl_today,
                last_data_timestamp=self._last_data_ts,
                error_count_last_hour=errors_last_hour,
                drift_result=drift_result,
                config=config,
            )
            if ks_event.triggered and not kill_switch.is_triggered():
                await kill_switch.execute(broker, ks_event.trigger, ks_event.details)
        except Exception as exc:
            log.error("loop: kill switch check failed: %s", exc)

        # Step 9: Metrics
        if self._metrics:
            try:
                acct = self._metrics._account
                self._metrics.account_equity.labels(account=acct).set(equity)
                self._metrics.active_positions.labels(account=acct).set(len(positions))
                loop_latency = time.monotonic() - loop_start
                self._metrics.loop_latency.labels(account=acct).observe(loop_latency)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Per-coin signal computation (parallelisable)
    # ------------------------------------------------------------------

    async def _compute_coin_signal(
        self,
        coin: str,
        store: "DataStore",
        intervals: list[str],
        regime_interval: str,
        signal_interval: str,
        strategies: list["BaseStrategy"],
        positions: dict,
        equity: float,
    ) -> "tuple[Any, float, dict]":
        """Read candles, classify regime, and compute the best signal for *coin*.

        Returns ``(signal, current_price)``.  Returns ``(None, 0.0)`` when
        there is insufficient data or the coin already has an open position.

        This method is designed to run concurrently for all coins via
        ``asyncio.gather`` — it has no shared mutable state.
        """
        import pandas as pd
        from autotrader.hl.types import MarketContext

        # Ensure hysteresis filter exists (dict write is GIL-safe in CPython)
        if coin not in self._regime_filters:
            from autotrader.regimes.hysteresis import HysteresisFilter
            self._regime_filters[coin] = HysteresisFilter()

        # Load candles for all required intervals
        candles_by_interval: dict[str, pd.DataFrame] = {}
        for iv in intervals:
            df = store.read_candles(coin, iv)
            if df is not None and len(df) > 0:
                candles_by_interval[iv] = df

        if not candles_by_interval:
            return None, 0.0, {}

        # Push candles to the live dashboard (non-blocking, best-effort)
        try:
            from autotrader.monitoring.web import update_coin_data
            sig_df = candles_by_interval.get(signal_interval)
            if sig_df is not None and len(sig_df) > 0:
                update_coin_data(coin, signal_interval, sig_df)
        except Exception:
            pass

        # Skip if already in an open position (don't pyramid)
        position = positions.get(coin)
        if position is not None and abs(getattr(position, "szi", 0.0)) > 1e-8:
            return None, 0.0, {}

        # Current price check
        sig_df = candles_by_interval.get(signal_interval)
        if sig_df is None or len(sig_df) == 0:
            return None, 0.0, {}
        current_price = float(sig_df["c"].iloc[-1])
        if current_price <= 0.0:
            return None, 0.0, {}

        # Regime classification
        try:
            primary_df = candles_by_interval.get(
                regime_interval,
                next(iter(candles_by_interval.values())),
            )
            df_4h = candles_by_interval.get("4h")
            if primary_df is not None and len(primary_df) >= 50:
                regime_result = self._regime_classifier.classify(primary_df, df_4h)
                stable_regime = self._regime_filters[coin].update(regime_result)
            else:
                from autotrader.regimes.classifier import Regime
                stable_regime = Regime.UNKNOWN
        except Exception as exc:
            log.debug("loop: regime classification failed for %s: %s", coin, exc)
            from autotrader.regimes.classifier import Regime
            stable_regime = Regime.UNKNOWN

        # Build MarketContext
        ctx = MarketContext(
            coin=coin,
            candles=candles_by_interval,
            l2book=None,
            asset_ctx=None,
            position=position,
            account_value=equity,
            timestamp=int(time.time() * 1_000),
        )
        if hasattr(ctx, "__dict__"):
            ctx.__dict__["regime"] = stable_regime

        # Detect S/R levels for this coin — strategies use these to anchor TP/SL
        levels: list = []
        if self._level_detector is not None:
            try:
                levels = self._level_detector.detect(sig_df, n_levels=8)
                if hasattr(ctx, "__dict__"):
                    ctx.__dict__["levels"] = levels
            except Exception as exc:
                log.debug("loop: level detection failed for %s: %s", coin, exc)

        # Build analysis extras dict (populated incrementally below)
        _regime_str = stable_regime.value if hasattr(stable_regime, "value") else str(stable_regime)
        _level_list = [
            {"price": lv.price, "kind": lv.kind, "strength": round(lv.strength, 3)}
            for lv in (levels or [])[:8]
        ] if levels else []
        # Nearest support / resistance
        _sup_levels = [lv for lv in (levels or []) if lv.kind in ("support", "both")]
        _res_levels = [lv for lv in (levels or []) if lv.kind in ("resistance", "both")]
        _nearest_sup = min(
            (_abs_diff(lv.price, current_price), lv.price) for lv in _sup_levels
        )[1] if _sup_levels else None
        _nearest_res = min(
            (_abs_diff(lv.price, current_price), lv.price) for lv in _res_levels
        )[1] if _res_levels else None

        _extras: dict = {
            "regime": _regime_str,
            "signal_side": None,
            "signal_confidence": None,
            "signal_strategy": None,
            "ml_gate_passed": None,
            "ml_probability": None,
            "levels": _level_list,
            "nearest_support": _nearest_sup,
            "nearest_resistance": _nearest_res,
            "candles_15m": len(sig_df) if sig_df is not None else 0,
            "current_price": current_price,
        }

        # Run strategies — pass only the ensemble; it aggregates sub-strategies
        # internally.  The first approved non-flat signal wins.
        for strategy in strategies:
            applicable = strategy.applicable_regimes()
            if applicable and stable_regime.value not in applicable:
                continue
            try:
                signal = strategy.compute_signal(ctx)
            except Exception as exc:
                log.debug("loop: strategy %s error on %s: %s", strategy.name, coin, exc)
                continue
            if signal.side == "flat":
                continue
            if not strategy.invariants_ok(signal):
                continue

            # ML signal quality gate — filter low-probability setups
            _ml_passed: bool | None = None
            _ml_prob: float | None = None
            if self._signal_quality_model is not None and self._signal_quality_model.is_trained():
                try:
                    from autotrader.ml.features import FeatureExtractor
                    news_fg = getattr(self._news_state, "fear_greed", 50) if self._news_state else 50
                    feat_vec = FeatureExtractor().extract(
                        df=sig_df,
                        signal_side=signal.side,
                        signal_confidence=signal.confidence,
                        regime=stable_regime.value,
                        atr=float((sig_df["h"] - sig_df["l"]).rolling(14).mean().iloc[-1]),
                        fear_greed=news_fg,
                    )
                    passes, prob = self._signal_quality_model.quality_gate(feat_vec)
                    _ml_passed = bool(passes)
                    _ml_prob = float(prob)
                    if not passes:
                        log.debug(
                            "loop: ML gate rejected %s %s (p_win=%.2f)",
                            coin, signal.side, prob,
                        )
                        _extras["signal_side"] = signal.side
                        _extras["signal_confidence"] = round(signal.confidence, 3)
                        _extras["signal_strategy"] = signal.metadata.get("strategy") if signal.metadata else None
                        _extras["ml_gate_passed"] = _ml_passed
                        _extras["ml_probability"] = round(_ml_prob, 3) if _ml_prob is not None else None
                        continue
                    # Scale confidence by ML quality multiplier
                    mult = self._signal_quality_model.confidence_multiplier(prob)
                    if mult < 1.0:
                        from dataclasses import replace
                        signal = replace(signal, confidence=min(signal.confidence * mult, 1.0))
                    log.debug("loop: ML gate passed %s %s (p_win=%.2f, mult=%.2f)", coin, signal.side, prob, mult)
                except Exception as exc:
                    log.debug("loop: ML quality gate error for %s: %s", coin, exc)

            _extras["signal_side"] = signal.side
            _extras["signal_confidence"] = round(signal.confidence, 3)
            _extras["signal_strategy"] = signal.metadata.get("strategy") if signal.metadata else None
            _extras["ml_gate_passed"] = _ml_passed
            _extras["ml_probability"] = round(_ml_prob, 3) if _ml_prob is not None else None
            return signal, current_price, _extras

        return None, current_price, _extras

    # ------------------------------------------------------------------
    # Continuous loop
    # ------------------------------------------------------------------

    async def run_loop(
        self,
        strategies: list["BaseStrategy"],
        client: "HyperliquidClient",
        store: "DataStore",
        order_manager: "OrderManager",
        broker: "Broker",
        kill_switch: "KillSwitch",
        config: dict,
        coins: list[str],
        interval_seconds: float = 15.0,
    ) -> None:
        """Continuous trading loop with error recovery.

        Runs indefinitely until:
        - Kill switch is triggered (exits cleanly)
        - Unrecoverable error or shutdown signal
        """
        log.info(
            "trading loop started: interval=%.0fs coins=%s",
            interval_seconds,
            coins,
        )

        while True:
            if kill_switch.is_triggered():
                log.critical("trading loop: kill switch triggered — exiting")
                break

            iter_start = time.monotonic()

            try:
                await self.run_once(
                    strategies=strategies,
                    client=client,
                    store=store,
                    order_manager=order_manager,
                    broker=broker,
                    kill_switch=kill_switch,
                    config=config,
                    coins=coins,
                )
            except Exception as exc:
                self._record_error()
                log.error("trading loop: unhandled exception: %s", exc, exc_info=True)
                if self._errors_last_hour() >= int(config.get("max_errors_per_hour", 10)):
                    log.critical(
                        "trading loop: too many errors (%d/hr) — triggering kill switch",
                        self._errors_last_hour(),
                    )
                    await kill_switch.execute(
                        broker=broker,
                        trigger=__import__(
                            "autotrader.runtime.kill_switch", fromlist=["KillSwitchTrigger"]
                        ).KillSwitchTrigger.REPEATED_ERRORS,
                        details=f"Exceeded max_errors_per_hour: {exc}",
                    )
                    break

            elapsed = time.monotonic() - iter_start
            sleep_time = max(0.0, interval_seconds - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        log.info("trading loop exited")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_error(self) -> None:
        self._error_timestamps.append(time.monotonic())

    def _errors_last_hour(self) -> int:
        cutoff = time.monotonic() - 3600.0
        self._error_timestamps = [t for t in self._error_timestamps if t > cutoff]
        return len(self._error_timestamps)
