# OpenClaw Master Plan — Hyperliquid Autonomous Trader (Parity-First, Gated Deployment)

## Mission
Build a fully autonomous trading system for Hyperliquid that:
- Trades only the most liquid markets (dynamic universe)
- Can long and short
- Detects market regime and selects the right strategy
- Automatically selects leverage (systematically, safely)
- Never deploys a strategy change unless backtests beat the current baseline AND robustness checks pass
- Has hard, immutable risk guardrails and drift protection

This repo is the source of truth. Implementation must be deterministic, reproducible, and test-backed.

---

## Non-negotiables (hard rules)
1. **No live trading until:**
   - startup checks pass
   - nonce manager is persistent and verified
   - rate limiter is enforced
   - baseline exists
   - candidate gating pipeline exists
   - paper mode runs end-to-end successfully

2. **No strategy deployment unless:**
   - candidate beats baseline on the configured Utility metric
   - candidate passes walk-forward OOS and robustness suite
   - candidate stays within MaxDD/CVaR caps
   - operational feasibility checks pass (expected order rate, expected slippage)

3. **Parity-first:** backtest engine must share the same core logic as live execution (same signal objects, same sizing rules, same risk constraints).

4. **Immutable risk constraints:** strategies cannot change risk caps. Any attempt to do so must fail hard and trigger kill-switch.

5. **Never hand-roll signing:** use the official/recommended SDK client for signing/exchange calls. Wrap it with thin adapter code.

6. **Every commit is small, test-backed, and runnable.** No "big bang" changes.

---

## Build Order (atomic milestones)
You must follow this order. Each milestone must be merged (and green) before starting the next.

### Milestone 0 — Repo scaffolding + toolchain
**Goal:** Create project skeleton, config loading, lint/test pipeline.

Deliverables:
- Project structure under `src/autotrader`
- `config/base.yaml`, `paper.yaml`, `canary.yaml`, `live.yaml`
- `scripts/healthcheck.py` skeleton
- CI workflow running: format/lint + unit tests

Tests:
- `tests/unit/test_config_load.py` validates config schema + env var substitution
- `tests/unit/test_imports_smoke.py` ensures modules import cleanly

Done checks:
- `pytest` passes
- lint passes
- `python -m autotrader.main --help` works (or equivalent)

Commit naming:
- `m0: scaffolding + config loader + ci`

---

### Milestone 1 — Hyperliquid client wrapper (REST /info + exchange stub)
**Goal:** A minimal HL adapter that can safely call /info and (in paper mode) no-op exchange.

Deliverables:
- `src/autotrader/hl/client.py` with:
  - `info()` calls
  - typed request builders
  - safe retries/backoff
- `src/autotrader/hl/types.py` minimal dataclasses for meta/candles

Tests:
- Unit: payload parsing fixtures
- Integration (optional, guarded by env var): `tests/integration/test_hl_info_smoke.py` hits /info

Done checks:
- can fetch meta/asset context with a single script without trading

Commit:
- `m1: hyperliquid client (info) + typed parsing`

---

### Milestone 2 — Websocket manager (reconnect + subscriptions)
**Goal:** Stable WS connection, subscribe to candles and (optionally) user events.

Deliverables:
- `src/autotrader/hl/ws.py`
  - reconnect loop with exponential backoff
  - subscription registry
  - message routing callbacks

Tests:
- Unit: message router against fixture payloads
- Integration (guarded): connects and subscribes to a public stream

Done checks:
- `scripts/ws_smoke.py` (or similar) runs for 60s without crashing
- reconnect works (simulate disconnect)

Commit:
- `m2: websocket manager + candle subscription`

---

### Milestone 3 — Rate limiter (central budget)
**Goal:** Enforce REST weight budgets; degrade gracefully under load.

Deliverables:
- `src/autotrader/hl/rate_limiter.py`
  - token bucket / leaky bucket abstraction
  - weight accounting per endpoint
  - `with budget():` guard used by all REST calls

Tests:
- `tests/unit/test_rate_limiter.py`

Done checks:
- If budget exceeded, system delays/degrades instead of 429 spiraling

Commit:
- `m3: rate limiter + enforced in hl client`

---

### Milestone 4 — Persistent nonce manager (per signer)
**Goal:** Safe nonces across restarts; no collisions.

Deliverables:
- `src/autotrader/hl/nonces.py`
  - persistent store (postgres or local file in dev)
  - monotonic increment
  - validity checks (time window policy as per HL docs)

Tests:
- `tests/unit/test_nonces.py`:
  - increments monotonic
  - survives restart (reload)
  - rejects invalid backwards nonce

Done checks:
- `scripts/healthcheck.py` verifies nonce readiness and blocks trading if failing

Commit:
- `m4: persistent nonce manager + healthcheck enforcement`

---

### Milestone 5 — Storage layer + dataset hashing
**Goal:** Store candles and snapshots with dataset manifests that allow reproducible backtests.

Deliverables:
- `src/autotrader/store/postgres.py` (or parquet for dev)
- `src/autotrader/store/dataset_hash.py`
- `data/manifests/*.json` generation

Tests:
- Unit: dataset hash stable given same inputs
- Unit: storage read/write roundtrip

Done checks:
- can bootstrap 5000 candles per market per interval and store them
- can generate dataset manifest hash

Commit:
- `m5: storage + dataset hashing + manifest writer`

---

### Milestone 6 — Data collectors (candles first)
**Goal:** Build history bootstrap and live continuation.

Deliverables:
- `src/autotrader/data/collectors/candles.py`
  - `bootstrap_candles()` uses candleSnapshot
  - `stream_candles()` appends from WS candle stream
  - dedup + gap detection

Scripts:
- `scripts/bootstrap_history.py`

Tests:
- Unit: dedup, gap detection, resample correctness (if used)

Done checks:
- After running, DB has continuous candles and manifest is updated

Commit:
- `m6: candle collectors + history bootstrap script`

---

### Milestone 7 — Backtest engine (event-driven, parity-first)
**Goal:** A backtest engine that can replay candles, simulate fills (basic), and compute metrics.

Deliverables:
- `src/autotrader/backtest/engine.py`
- `src/autotrader/backtest/metrics.py`
- `src/autotrader/backtest/reporting.py`
- `scripts/run_backtest.py` producing `reports/backtests/<run_id>/report.json`

Tests:
- Golden tests: feed known candle series, expect known trades/equity curve
- Unit tests for metrics (DD, Sharpe, etc.)

Done checks:
- End-to-end backtest runs on stored candles and outputs report artifact

Commit:
- `m7: backtest engine + metrics + report artifacts`

---

### Milestone 8 — Cost model (fees + funding + slippage approximation)
**Goal:** Add realistic costs so backtests are not fantasy.

Deliverables:
- `src/autotrader/backtest/cost_model.py`
  - maker/taker fees (configurable)
  - funding payments/receipts (use available funding data; if missing, model conservatively)
  - slippage model: spread + depth proxy (configurable; can start simple)

Tests:
- Unit: cost math sanity (funding adds/subtracts correctly)
- Unit: slippage is monotonic with size

Done checks:
- backtest report includes gross vs net performance and cost breakdown

Commit:
- `m8: cost model (fees+funding+slippage) integrated into backtests`

---

### Milestone 9 — Strategy interface + 2 strategies + invariants
**Goal:** Implement strategy library as pluggable modules.

Deliverables:
- `src/autotrader/strategies/base.py` (Signal + interface)
- Strategies:
  - `trend_breakout.py`
  - `range_meanrev.py`
- Invariant checks:
  - stop exists when side != flat
  - stop distance > minimum tick distance
  - expected RR meets min threshold if required

Tests:
- `tests/unit/test_strategy_invariants.py`

Done checks:
- can backtest each strategy independently end-to-end

Commit:
- `m9: strategy interface + trend/range strategies + invariants`

---

### Milestone 10 — Regime classifier + hysteresis + strategy selection
**Goal:** Detect market condition and choose the right strategy.

Deliverables:
- `src/autotrader/regimes/classifier.py`
- `src/autotrader/regimes/hysteresis.py`
- `src/autotrader/strategies/ensemble.py` (simple selector: pick best for regime)

Tests:
- Unit: regime transitions obey hysteresis
- Unit: classifier returns stable outputs on fixtures

Done checks:
- backtest uses regime switching without churn

Commit:
- `m10: regime classifier + hysteresis + regime-based strategy selection`

---

### Milestone 11 — Risk engine + leverage selection (systematic)
**Goal:** Hard guardrails + systematic sizing/leverage.

Deliverables:
- `src/autotrader/risk/constraints.py` (immutable)
- `src/autotrader/risk/sizing.py`
- `src/autotrader/risk/leverage.py` (liquidation buffer policy)
- `src/autotrader/risk/approvals.py` (preflight gate)

Tests:
- `tests/unit/test_risk_constraints.py`
- leverage selection tests: reduces leverage under high vol; respects caps

Done checks:
- any trade failing constraints is rejected with explicit reasons

Commit:
- `m11: risk engine + leverage selection + immutable guardrails`

---

### Milestone 12 — Execution engine (paper first, then live wiring)
**Goal:** Order manager, lifecycle, reconciliation; paper mode must match live behavior.

Deliverables:
- `src/autotrader/execution/broker.py` (adapter; in paper mode it simulates fills)
- `src/autotrader/execution/order_manager.py`
- `src/autotrader/execution/reconciliation.py`

Tests:
- Unit: order lifecycle state machine
- Golden: simulated fills reproduce expected PnL

Done checks:
- `scripts/paper_trade.py` runs continuously and writes fills/trades ledger

Commit:
- `m12: execution engine (paper parity) + reconciliation`

---

### Milestone 13 — Governance: baselines + walk-forward + robustness + gates
**Goal:** The central promise: no deploy unless candidate beats baseline.

Deliverables:
- `src/autotrader/governance/registry.py`
- `src/autotrader/backtest/walkforward.py`
- `src/autotrader/backtest/robustness.py`
- `src/autotrader/governance/gates.py`
- `scripts/run_walkforward.py`
- `scripts/promote_candidate.py`
- `artifacts/baselines/current.json` creation on first successful baseline

Tests:
- Unit: gate rejects worse candidate
- Unit: WFO splits correct and reproducible
- Unit: robustness runs and reports failures

Done checks:
- `scripts/run_walkforward.py` produces candidate report
- `scripts/promote_candidate.py` refuses promotion unless gates pass

Commit:
- `m13: governance gating (baseline + WFO + robustness + promote)`

---

### Milestone 14 — Live canary + probation + rollback
**Goal:** Controlled live deployment with fast rollback.

Deliverables:
- `src/autotrader/governance/probation.py`
- `scripts/live_trade.py` loads ONLY approved bundle
- `scripts/rollback.py`

Tests:
- Unit: live runner refuses to start without baseline and passing healthcheck
- Unit: rollback updates baseline pointers correctly

Done checks:
- canary config trades minimal size with daily loss cap
- rollback is one command

Commit:
- `m14: canary live + probation evaluator + rollback tooling`

---

### Milestone 15 — Monitoring + drift detection + degrade mode
**Goal:** Detect drift and protect capital automatically.

Deliverables:
- `src/autotrader/governance/drift.py`
- `src/autotrader/runtime/kill_switch.py`
- `src/autotrader/monitoring/metrics.py` (Prometheus)
- `src/autotrader/monitoring/logger.py` (structured logs)
- optional alert routing

Tests:
- Unit: drift triggers risk reduction or pause
- Unit: kill-switch flattens positions in simulation

Done checks:
- drift signal logged + triggers safe action
- metrics exposed on port

Commit:
- `m15: monitoring + drift detection + kill-switch + degrade mode`

---

## "Definition of Done" for the whole system
- Paper mode runs 72 hours without crash (local)
- Backtests are reproducible from dataset manifest hash
- Governance pipeline can:
  - create candidate
  - run WFO + robustness
  - compare to baseline
  - promote only if candidate wins
- Live canary:
  - startup checks enforced
  - risk caps enforced
  - rollback works
- Drift detection:
  - reduces risk or pauses automatically
  - does not "learn" itself into riskier behavior

---

## Operating Rules for OpenClaw
- Never modify risk constraints without explicit change request in docs + tests.
- Never ship code without:
  - updating docs if behavior changes
  - adding tests proving invariants and gates remain intact
- Prefer fewer, higher-quality strategies over many weak ones.
- Optimize for net performance after costs, not raw signal win rate.

---

## How to use subagents
OpenClaw may spawn subagents, but must keep ownership of integration.
Subagent outputs must be merged only via small PRs/commits that pass CI.

Subagents:
- agents/DATA_ENGINEER.md
- agents/QUANT_RESEARCHER.md
- agents/RISK_AUDITOR.md
- agents/EXECUTION_ENGINEER.md
- agents/SRE_MONITORING.md
