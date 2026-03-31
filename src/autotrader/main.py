"""
Hyperliquid Autotrader — live entrypoint.

Usage:
    python -m autotrader.main [--env paper|canary|live] [--config-dir config/]

Key rule: live never runs research; only loads approved bundles from governance.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def build_strategies(config: dict):
    """Instantiate all trading strategies and wrap in ensemble.

    Only the EnsembleStrategy is returned for the live loop.  The base
    strategies are wired into the ensemble internally — they provide signal
    candidates, but the ensemble is the sole arbiter that produces a final
    signal.  This ensures the scheduler never receives conflicting signals
    from multiple strategies for the same coin.
    """
    from autotrader.strategies.trend_breakout import TrendBreakoutStrategy
    from autotrader.strategies.range_meanrev import RangeMeanRevStrategy
    from autotrader.strategies.vol_expansion import VolExpansionStrategy
    from autotrader.strategies.funding_extremes import FundingExtremesStrategy
    from autotrader.strategies.ensemble import EnsembleStrategy

    base = [
        TrendBreakoutStrategy(),
        RangeMeanRevStrategy(),
        VolExpansionStrategy(),
        FundingExtremesStrategy(),
    ]
    return [EnsembleStrategy(strategies=base)]


async def run_main(
    env: str = "paper",
    config_dir: Path = Path("config"),
    log_level: str = "INFO",
) -> int:
    """Full async startup sequence.

    Returns 0 on clean exit, 1 on startup failure.
    """
    # ------------------------------------------------------------------
    # 1. Load config and configure logging
    # ------------------------------------------------------------------
    from autotrader.utils.config import load_config, ConfigError
    from autotrader.monitoring.logger import configure_logging

    try:
        config = load_config(env=env, config_dir=config_dir)
    except ConfigError as exc:
        print(f"[FATAL] Config load failed: {exc}", file=sys.stderr)
        return 1

    configure_logging(
        log_level=config.get("observability", {}).get("log_level", log_level),
        json_output=(env != "paper"),
    )

    log.info("autotrader starting: env=%s", env)

    # ------------------------------------------------------------------
    # 2. Initialise components
    # ------------------------------------------------------------------
    from autotrader.hl.client import HyperliquidClient
    from autotrader.hl.ws import HyperliquidWS
    from autotrader.hl.nonces import NonceManager
    from autotrader.hl.rate_limiter import RateLimiter
    from autotrader.store.datastore import get_datastore
    from autotrader.execution.broker import Broker
    from autotrader.execution.order_manager import OrderManager
    from autotrader.execution.reconciliation import Reconciler
    from autotrader.execution.tp_manager import TrailingTPManager
    from autotrader.regimes.classifier import RegimeClassifier
    from autotrader.regimes.hysteresis import HysteresisFilter
    from autotrader.risk.approvals import TradeApprover
    from autotrader.risk.sizing import PositionSizer
    from autotrader.risk.leverage import LeverageSelector
    from autotrader.risk.constraints import ConstraintChecker
    from autotrader.risk.exposure import ExposureAggregator
    from autotrader.risk.hedging import PortfolioHedger
    from autotrader.governance.drift import DriftDetector
    from autotrader.levels.detector import LevelDetector
    from autotrader.news.guard import NewsGuard
    from autotrader.ml.model import SignalQualityModel
    from autotrader.ml.trainer import ModelTrainer
    from autotrader.ml.paper_sim import PaperSimulator
    from autotrader.monitoring.alerts import get_alert_manager
    from autotrader.monitoring.metrics import get_metrics
    from autotrader.runtime.kill_switch import KillSwitch
    from autotrader.runtime.startup_checks import run_startup_checks
    from autotrader.runtime.scheduler import TradingScheduler

    hl_cfg = config.get("hyperliquid", {})
    universe_cfg = config.get("universe", {})
    exec_cfg = config.get("execution", {})

    rate_limiter = RateLimiter()
    address = hl_cfg.get("account_address", "")
    nonce_mgr = NonceManager(account_address=address)
    await nonce_mgr.load()

    client = HyperliquidClient(config=config)
    store = get_datastore(config, account_address=address)
    kill_switch = KillSwitch(account_address=address)
    alert_mgr = get_alert_manager(config)
    metrics = get_metrics(account=address)

    # ------------------------------------------------------------------
    # 3. Run startup checks
    # ------------------------------------------------------------------
    check_results = await run_startup_checks(client=client, config=config)
    critical_failures = [r for r in check_results if not r.passed and r.critical]

    if critical_failures:
        for cf in critical_failures:
            log.critical("STARTUP FAILED: %s — %s", cf.name, cf.message)
            await alert_mgr.startup_check_failed(cf.name, cf.message)
        log.critical("%d critical startup check(s) failed — aborting", len(critical_failures))
        return 1

    # ------------------------------------------------------------------
    # 4. Start Prometheus metrics server + live dashboard
    # ------------------------------------------------------------------
    prom_port = int(config.get("observability", {}).get("prometheus_port", 9109))
    try:
        metrics.start_server(port=prom_port)
        log.info("Prometheus metrics server started on port %d", prom_port)
    except Exception as exc:
        log.warning("Could not start Prometheus server: %s", exc)

    dash_port = int(config.get("observability", {}).get("dashboard_port", 8080))
    try:
        from autotrader.monitoring.web import start_dashboard, set_kill_switch
        set_kill_switch(kill_switch)
        start_dashboard(port=dash_port)
        log.info("Dashboard: http://localhost:%d", dash_port)
    except Exception as exc:
        log.warning("Could not start dashboard: %s", exc)

    # ------------------------------------------------------------------
    # 5. Determine active coins
    # ------------------------------------------------------------------
    try:
        meta, asset_ctxs = await client.get_meta_and_asset_ctxs()
        # Sort by 24h notional volume, take top N
        top_n = int(universe_cfg.get("top_n_by_liquidity", 8))
        min_notional = float(universe_cfg.get("min_notional_24h", 1e8))

        coin_vols = [
            (m.name, ctx.dayNtlVlm)
            for m, ctx in zip(meta, asset_ctxs)
            if ctx.dayNtlVlm >= min_notional
        ]
        coin_vols.sort(key=lambda x: x[1], reverse=True)
        coins = [c for c, _ in coin_vols[:top_n]]
        log.info("Active coins (%d): %s", len(coins), coins)
    except Exception as exc:
        log.warning("Could not determine active coins: %s — using fallback", exc)
        coins = ["BTC", "ETH", "SOL", "ARB", "AVAX", "BNB", "DOGE", "MATIC"]

    # ------------------------------------------------------------------
    # 6. Bootstrap data if needed
    # ------------------------------------------------------------------
    intervals = (
        config.get("timeframes", {}).get("regime", ["1h", "4h"]) +
        config.get("timeframes", {}).get("signal", ["15m"])
    )
    intervals = list(dict.fromkeys(intervals))

    try:
        from autotrader.data.collectors.candles import CandleCollector
        candle_collector = CandleCollector(client=client, store=store)
        log.info("Bootstrapping/updating candle data for %d coins × %d intervals...", len(coins), len(intervals))
        await candle_collector.update_all(coins=coins, intervals=intervals)
    except Exception as exc:
        log.warning("Data bootstrap failed (non-fatal in paper mode): %s", exc)

    # ------------------------------------------------------------------
    # 7. Build strategies and risk layer
    # ------------------------------------------------------------------
    strategies = build_strategies(config)
    log.info("Loaded %d strategies: %s", len(strategies), [s.name for s in strategies])

    sizer = PositionSizer()
    lev_selector = LeverageSelector()
    constraint_checker = ConstraintChecker()
    exposure_agg = ExposureAggregator()
    trade_approver = TradeApprover(
        sizer=sizer,
        lev_selector=lev_selector,
        constraint_checker=constraint_checker,
        exposure_agg=exposure_agg,
    )

    # ------------------------------------------------------------------
    # 8. Build execution layer
    # ------------------------------------------------------------------
    paper = (env == "paper")
    broker = Broker(client=client, paper=paper)
    order_manager = OrderManager(
        broker=broker,
        chase_seconds=float(exec_cfg.get("chase_seconds", 8.0)),
        max_chase_attempts=int(exec_cfg.get("max_order_retries", 3)),
    )

    # ------------------------------------------------------------------
    # 9. Reconcile on startup
    # ------------------------------------------------------------------
    try:
        reconciler = Reconciler()
        reconcile_result = await reconciler.reconcile(client, order_manager, address)
        if not reconcile_result.ok:
            log.critical("Reconciliation failed on startup: %s", reconcile_result.discrepancies)
            if env == "live":
                return 1
    except Exception as exc:
        log.warning("Startup reconciliation failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # 10. Start WebSocket streams
    # ------------------------------------------------------------------
    ws_url = hl_cfg.get("ws_url", "wss://api.hyperliquid.xyz/ws")
    ws = HyperliquidWS(ws_url=ws_url, subscriptions=[])
    ws_task: Optional[asyncio.Task] = None
    try:
        await ws.connect()
        ws_task = asyncio.create_task(ws._reader_loop())
        log.info("WebSocket connected to %s", ws_url)
    except Exception as exc:
        log.warning("WebSocket connection failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # 11. Build intelligence + risk enhancement modules
    # ------------------------------------------------------------------
    from autotrader.store.datastore import _account_safe  # type: ignore[attr-defined]
    account_safe = _account_safe(address) if address else "default"

    level_detector = LevelDetector()
    news_guard = NewsGuard()
    tp_manager = TrailingTPManager(order_manager=order_manager)
    portfolio_hedger = PortfolioHedger(
        delta_threshold=float(config.get("risk", {}).get("hedge_delta_threshold", 0.65)),
        target_delta=float(config.get("risk", {}).get("hedge_target_delta", 0.30)),
    )

    # ML signal quality model — load existing or train from paper simulation
    signal_model = SignalQualityModel(account_safe=account_safe)
    simulator = PaperSimulator(store=store, config=config)
    trainer = ModelTrainer(model=signal_model, simulator=simulator, account_safe=account_safe)
    try:
        log.info("ML: initialising signal quality model (account=%s)…", account_safe)
        await trainer.cold_start(coins=coins, intervals=intervals)
        if signal_model.is_trained():
            log.info("ML: model ready — trained on %d samples", signal_model._trained_on)
        else:
            log.info("ML: model not yet trained — signals will pass unfiltered until data accumulates")
    except Exception as exc:
        log.warning("ML: cold-start failed (non-fatal): %s", exc)

    # Kick off nightly retraining in background
    asyncio.create_task(trainer.schedule_nightly_retrain(coins=coins, intervals=intervals))

    # ------------------------------------------------------------------
    # 12. Build and start the trading scheduler
    # ------------------------------------------------------------------
    regime_classifier = RegimeClassifier()
    regime_filters = {coin: HysteresisFilter() for coin in coins}
    drift_detector = DriftDetector()

    scheduler = TradingScheduler(
        regime_classifier=regime_classifier,
        regime_filters=regime_filters,
        trade_approver=trade_approver,
        drift_detector=drift_detector,
        alert_manager=alert_mgr,
        metrics=metrics,
        level_detector=level_detector,
        news_guard=news_guard,
        signal_quality_model=signal_model,
        tp_manager=tp_manager,
        portfolio_hedger=portfolio_hedger,
    )

    loop_interval = float(config.get("loop_interval_seconds", 15.0))
    log.info("Starting trading loop (interval=%.0fs, env=%s)", loop_interval, env)

    try:
        await scheduler.run_loop(
            strategies=strategies,
            client=client,
            store=store,
            order_manager=order_manager,
            broker=broker,
            kill_switch=kill_switch,
            config=config,
            coins=coins,
            interval_seconds=loop_interval,
        )
    except KeyboardInterrupt:
        log.info("Received shutdown signal — cleaning up")
    except Exception as exc:
        log.critical("Trading loop crashed: %s", exc, exc_info=True)
        return 1
    finally:
        # Shutdown sequence
        log.info("Shutdown: cancelling all open orders...")
        try:
            cancelled = await broker.cancel_all()
            log.info("Cancelled %d orders", cancelled)
        except Exception:
            pass

        if ws_task:
            ws_task.cancel()
        try:
            await ws.disconnect()
        except Exception:
            pass
        await client.close()
        log.info("Shutdown complete")

    return 0


def main() -> None:
    """CLI entrypoint supporting both argparse and typer."""
    # Try typer first; fall back to argparse for environments without it
    try:
        import typer
        from typing import Optional as Opt
        app = typer.Typer(name="autotrader", add_completion=False)

        @app.command()
        def _main(
            env: str = typer.Option("paper", help="Trading environment: paper|canary|live"),
            config_dir: Path = typer.Option(Path("config"), help="Config directory"),
            log_level: str = typer.Option("INFO", help="Log level"),
        ) -> None:
            """Hyperliquid Autotrader — autonomous trading system."""
            rc = asyncio.run(run_main(env=env, config_dir=config_dir, log_level=log_level))
            raise typer.Exit(code=rc)

        app()
    except ImportError:
        import argparse
        parser = argparse.ArgumentParser(description="Hyperliquid Autotrader")
        parser.add_argument("--env", choices=["paper", "canary", "live"], default="paper")
        parser.add_argument("--config-dir", type=Path, default=Path("config"))
        parser.add_argument("--log-level", default="INFO")
        args = parser.parse_args()
        rc = asyncio.run(run_main(env=args.env, config_dir=args.config_dir, log_level=args.log_level))
        sys.exit(rc)


if __name__ == "__main__":
    main()
