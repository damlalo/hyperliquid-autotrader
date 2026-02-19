# MASTER — Hyperliquid Autotrader Agent Plan

## Mission

Implement the Hyperliquid Autonomous Trader system according to PRD and docs: HL integration, data pipeline, features, regimes, strategies, risk, execution, backtest, governance, and monitoring. Every step must be testable and gate-aware; no shortcuts on risk or governance.

## Non-negotiables

- No secrets in config or code; env vars only.
- Live runner loads ONLY approved bundles from baseline registry; no research path in live.
- Immutable risk constraints; strategies cannot override caps.
- Do not invent missing API or product details; use TODO in docs if unknown.
- All deliverables must have tests or explicit "test: none" with reason.

---

## Milestones

### Milestone 0 — Repo structure and docs (DONE in this pass)

- **Deliverables:** Directory structure, README, START_HERE, LICENSE, pyproject.toml, .python-version, .gitignore, .env.example; config skeletons; docs (PRD, ARCHITECTURE, GOVERNANCE, RISK_GUARDRAILS, DATA_MODEL, STRATEGY_LIBRARY, BACKTEST_METHODOLOGY, RUNBOOK, REFERENCES); agents/MASTER + subagents; script skeletons; src/autotrader placeholders; test skeletons; artifacts/baselines; infra placeholders; CI workflow; Git init and first commit.
- **Tests:** Placeholder tests import modules and confirm entrypoints.
- **Done check:** Repo Creation Report generated; `pytest` runs and passes placeholder tests.
- **Commit:** `init: repo structure + PRD + agent master plan`

### Milestone 1 — HL client and config

- **Deliverables:** hl/client.py (REST info + exchange endpoints; env-based URL and keys); hl/types.py (request/response types); config load in utils/config.py; validate required env in startup_checks.
- **Tests:** test_config_load.py, test_hl_info_smoke.py (info endpoint if reachable); test_imports_smoke.
- **Done check:** Config loads; HL info call succeeds in CI or skip with env guard.
- **Commit:** `feat(hl): REST client + config load + startup env check`

### Milestone 2 — Rate limiter and nonces

- **Deliverables:** hl/rate_limiter.py (weight-based, 1200/min or per HL docs); hl/nonces.py (get/advance, optional persist).
- **Tests:** test_rate_limiter.py, test_nonces.py.
- **Done check:** Rate limiter enforces limit; nonce increments correctly.
- **Commit:** `feat(hl): rate limiter + nonces`

### Milestone 3 — WebSocket client

- **Deliverables:** hl/ws.py (connect, subscribe candles/book/trades/user; reconnect with backoff); use rate limits per docs.
- **Tests:** test_ws_subscriptions.py (mock or integration with skip if no key).
- **Done check:** WS connects and receives at least one message in test.
- **Commit:** `feat(hl): WebSocket client + subscriptions`

### Milestone 4 — Store and data collectors

- **Deliverables:** store/datastore.py (interface); store/postgres.py and/or parquet for candles/fills/orders; data/collectors (candles, l2book, funding_oi, user_state) writing to store; store/dataset_hash.py.
- **Tests:** Unit for dataset_hash; integration for store if DB available.
- **Done check:** Candles and funding can be stored and read; dataset hash deterministic.
- **Commit:** `feat(store): datastore + collectors + dataset hash`

### Milestone 5 — Transforms and features

- **Deliverables:** data/transforms (resample, cleaning); features/technical.py, microstructure.py, positioning.py.
- **Tests:** Unit tests for transforms and feature functions.
- **Done check:** Features computed from sample candles; no lookahead.
- **Commit:** `feat(data): transforms + feature pipelines`

### Milestone 6 — Regime classifier and hysteresis

- **Deliverables:** regimes/classifier.py (output regime label); regimes/hysteresis.py (hold N bars before switch).
- **Tests:** test_regime_classifier.py; hysteresis unit tests.
- **Done check:** Classifier + hysteresis produce stable regime series on sample data.
- **Commit:** `feat(regimes): classifier + hysteresis`

### Milestone 7 — Strategy stubs and ensemble

- **Deliverables:** strategies/base.py (interface); trend_breakout, range_meanrev, vol_expansion, funding_extremes (stubs or minimal logic); strategies/ensemble.py (combine signals).
- **Tests:** test_strategy_invariants.py (caps respected; no-trade conditions).
- **Done check:** All strategies implement base; ensemble runs without crash.
- **Commit:** `feat(strategies): base + library + ensemble`

### Milestone 8 — Risk layer

- **Deliverables:** risk/constraints.py (portfolio + per-trade); risk/sizing.py; risk/leverage.py (systematic selection); risk/exposure.py; risk/approvals.py (gate check).
- **Tests:** test_risk_constraints.py; sizing and leverage unit tests.
- **Done check:** Constraints block over-size; leverage respects stop distance.
- **Commit:** `feat(risk): constraints + sizing + leverage + approvals`

### Milestone 9 — Execution

- **Deliverables:** execution/broker.py (place/cancel/modify via HL); execution/order_manager.py; execution/slippage.py; execution/reconciliation.py.
- **Tests:** Mock broker tests; reconciliation with fake fills.
- **Done check:** Order lifecycle and reconciliation pass tests.
- **Commit:** `feat(execution): broker + order manager + slippage + reconciliation`

### Milestone 10 — Backtest engine

- **Deliverables:** backtest/engine.py (event-driven); backtest/cost_model.py; backtest/metrics.py; backtest/reporting.py.
- **Tests:** Golden or deterministic backtest run; metrics match expected.
- **Done check:** Backtest runs on fixture data; PnL and costs consistent.
- **Commit:** `feat(backtest): engine + cost model + metrics + reporting`

### Milestone 11 — Walk-forward and robustness

- **Deliverables:** backtest/walkforward.py; backtest/robustness.py (perturbation, Monte Carlo).
- **Tests:** WFO test on small dataset; robustness test runs.
- **Done check:** Walk-forward produces OOS metrics; robustness runs without error.
- **Commit:** `feat(backtest): walk-forward + robustness`

### Milestone 12 — Governance and baseline

- **Deliverables:** governance/registry.py (load/save current + history); governance/gates.py (utility, MaxDD, CVaR, feasibility); governance/drift.py, probation.py, approvals.py; scripts/promote_candidate.py, rollback.py (real implementation).
- **Tests:** Registry load/save; gate logic unit tests.
- **Done check:** Promote and rollback update baseline; gates reject bad candidates.
- **Commit:** `feat(governance): registry + gates + promote + rollback`

### Milestone 13 — Runtime and kill switch

- **Deliverables:** runtime/scheduler.py (loop); runtime/kill_switch.py (triggers and actions); main.py dispatches paper/canary/live with baseline load.
- **Tests:** Kill switch triggers and cancels in test.
- **Done check:** Paper mode runs one loop; kill switch test passes.
- **Commit:** `feat(runtime): scheduler + kill switch + main dispatch`

### Milestone 14 — Monitoring and alerts

- **Deliverables:** monitoring/logger.py, metrics.py, alerts.py, dashboards.py; optional Prometheus/Grafana in infra.
- **Tests:** Log and metric emission tests.
- **Done check:** Metrics exposed or logged; alert path exists.
- **Commit:** `feat(monitoring): logger + metrics + alerts + dashboards`

### Milestone 15 — Scripts and runbook

- **Deliverables:** scripts/bootstrap_history.py, run_backtest.py, run_walkforward.py, paper_trade.py, live_trade.py, healthcheck.py (implement to spec); RUNBOOK and docs updated with real commands.
- **Tests:** Scripts run with --help and dry-run where applicable.
- **Done check:** All scripts executable; runbook copy-paste correct.
- **Commit:** `feat(scripts): full script suite + runbook`

---

## Execution order

Work in order M0 → M15. Each milestone commits separately. Before live deployment: M0–M15 complete, gates documented, and canary run successful.
