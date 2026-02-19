# Architecture

## Dataflow (ASCII)

```
  [HL REST/WS] --> data/collectors --> transforms --> store
       |                                                    |
       v                                                    v
  hl/client, ws, rate_limiter, nonces              features, regimes
                                                          |
                                                          v
  strategies <-- regimes (classifier, hysteresis)   risk (constraints, sizing, leverage)
       |                                                                  |
       v                                                                  v
  execution (broker, order_manager, slippage, reconciliation)  <-- approvals
       |
       v
  [HL Exchange API]
```

- **Backtest path:** store → features → regimes → strategies → risk → engine (no live execution).
- **Live path:** Same pipeline; execution sends real orders; governance gates and baseline registry control what runs.

## Module boundaries

| Module | Responsibility |
|--------|-----------------|
| **hl/** | REST/WS client, types, rate limiter, nonces. No business logic. |
| **data/** | Collectors (candles, L2, funding/OI, user state), transforms (resample, cleaning). |
| **store/** | Datastore abstraction, Postgres, Parquet, dataset hash. |
| **features/** | Technical, microstructure, positioning feature computation. |
| **regimes/** | Regime classifier, hysteresis (avoid flapping). |
| **strategies/** | Base + trend_breakout, range_meanrev, vol_expansion, funding_extremes, ensemble. |
| **risk/** | Constraints, sizing, leverage, exposure, approvals. Immutable caps. |
| **execution/** | Broker, order manager, slippage, reconciliation. |
| **backtest/** | Engine, cost model, metrics, walk-forward, robustness, reporting. |
| **governance/** | Registry (baselines), gates, drift, probation, approvals. |
| **monitoring/** | Logger, metrics, alerts, dashboards. |
| **runtime/** | Startup checks, kill switch, scheduler. |

## Live runner constraint

**Live runner loads ONLY approved bundles.** No research code path in live. Strategy and params come from `artifacts/baselines/current.json` (or promoted candidate). All code that can run in live must be gated and versioned via the baseline registry.

## Startup checks

- Config and env loaded; required env vars present.
- HL connectivity (info endpoint); rate limit state acceptable.
- Baseline loaded (for live/canary); dataset hash matches if required.
- Kill switch not triggered; risk limits within bounds.
- (Optional) DB connectivity if Postgres used.

Failures block startup and must be resolved before paper/canary/live run.
