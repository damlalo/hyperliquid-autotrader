# Product Requirements: Hyperliquid Autonomous Trader

## Goals

- Automate perpetuals trading on Hyperliquid with regime-aware strategy selection.
- Enforce immutable risk guardrails and leverage selection; no strategy can override caps.
- Support staged rollout: paper → canary → live with governance gates and rollback.
- Reproducible research: dataset hashes, baseline registry, walk-forward and robustness tests.

## Non-goals

- Supporting other chains or CEXs in this repo (HL only).
- Implementing discretionary overrides inside live runner (gates only).
- Real-time research code path in live; live loads only approved bundles.

## Constraints

- All secrets via environment variables; no private keys in config or code.
- Live runner loads ONLY approved strategy/param bundles from baseline registry.
- Backtest and walk-forward must be event-driven and use a defined cost model.

---

## Architecture modules

- **hl/** — Hyperliquid REST/WS client, rate limiter, nonces.
- **data/** — Collectors (candles, L2, funding/OI, user state), transforms (resample, cleaning).
- **store/** — Datastore abstraction, Postgres, Parquet, dataset hashing.
- **features/** — Technical, microstructure, positioning.
- **regimes/** — Regime classifier, hysteresis.
- **strategies/** — Base + trend/range/vol-expansion/funding extremes, ensemble.
- **risk/** — Constraints, sizing, leverage, exposure, approvals.
- **execution/** — Broker, order manager, slippage, reconciliation.
- **backtest/** — Engine, cost model, metrics, walk-forward, robustness, reporting.
- **governance/** — Registry, gates, drift, probation, approvals.
- **monitoring/** — Logger, metrics, alerts, dashboards.
- **runtime/** — Startup checks, kill switch, scheduler.

---

## Strategy framework

- **Liquid universe:** Filter by volume/open interest; configurable.
- **Timeframes:** Multiple (e.g. 1m, 5m, 1h); strategy and regime can depend on timeframe.
- **Regime detection:** Classify market regime; map regime → strategy with hysteresis to avoid flapping.

---

## Strategy library

- **Trend breakout** — Breakout signals in trend regimes.
- **Range mean reversion** — Mean reversion in range regimes.
- **Vol expansion** — Trade volatility expansion with defined risk.
- **Funding extremes** — Exploit extreme funding with caps.
- **Ensemble** — Combine signals with weights; respect risk caps per strategy.

Each strategy has invariants and no-trade conditions (see STRATEGY_LIBRARY.md).

---

## Risk engine and leverage

- **Immutable constraints:** Strategies cannot change portfolio or per-trade caps.
- **Leverage selection:** Systematic; based on stop distance and liquidation buffer (see RISK_GUARDRAILS.md).
- **Circuit breakers:** Daily/weekly/maxDD; kill switch triggers documented.

---

## Execution engine

- Order placement/cancel/modify via HL exchange API.
- Order manager for lifecycle; slippage model; reconciliation with fills.
- Rate limits and nonces respected (HL docs).

---

## Data and backtest

- Candles, funding, OI, optional L2; dataset manifests and hashing for reproducibility.
- Event-driven backtest; cost model: fees, funding, slippage (start simple).
- Walk-forward and robustness (param perturbation, Monte Carlo); must beat baseline to be eligible.

---

## Drift prevention and staging

- **Paper:** Same code path as live, no real orders; config caps apply.
- **Canary:** Live orders with reduced size/caps; short probation.
- **Live:** Full caps only after canary passes and gates met.
- Drift response: reduce risk, pause, or revert to last approved baseline.

---

## Security

- No secrets in config files or code; env vars only.
- API wallet used for trading; account separation as per HL design.
- Audit trail for promotions and rollbacks (registry + history).

---

## Milestones (summary)

- **M0:** Repo structure, docs, config skeletons, agent plan (this deliverable).
- **M1+:** Implement HL client, data collectors, store, then features, regimes, strategies, risk, execution, backtest, governance, monitoring; each with tests and gate criteria.

Detailed milestones and commit names are in **agents/MASTER.md**.
