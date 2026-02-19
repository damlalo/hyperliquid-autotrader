# PRD: Hyperliquid Autonomous Trading System (OpenClaw-Driven)

## 0) One-sentence product definition

Build a fully autonomous, self-improving trading system that trades only the most liquid Hyperliquid markets (long + short), selects leverage dynamically, detects market regime, chooses the best strategy for that regime, and will not deploy any strategy change unless backtests beat the current baseline under strict guardrails.

---

## 1) Goals, non-goals, constraints

### 1.1 Goals

- **Autonomous profitability:** run end-to-end (data → research → backtest → deploy → monitor → improve) without human intervention.
- **Regime-adaptive:** detect market conditions (trend / range / high-vol crash / squeeze) and switch between strategies accordingly.
- **Liquid-first:** trade the most liquid assets/timeframes to minimize slippage and maximize fill quality.
- **Strict gating:** never deploy unless strategy update passes:
  - out-of-sample performance
  - robustness tests (walk-forward, parameter stability, Monte Carlo)
  - beats current production baseline on the chosen objective (see §6)
- **Operational safety:** rate-limit safe, nonce-safe, failure-safe, kill-switch safe.
- **Leverage selection:** automatically pick leverage per trade based on volatility, stop distance, liquidation buffer, and account constraints (cross/isolated considerations).

### 1.2 Non-goals

- No "YOLO mode" that can override guardrails.
- No trading illiquid assets or obscure markets unless they pass liquidity filters.
- No deployment without passing tests, even if a strategy looks good on recent data.

### 1.3 Hard constraints from Hyperliquid API

- Use official endpoints:
  - `POST https://api.hyperliquid.xyz/info` (market/user data)
  - `POST https://api.hyperliquid.xyz/exchange` (trading actions)
- Candle history: max 5000 candles, supported intervals from "1m" to "1M"
- WebSocket mainnet: `wss://api.hyperliquid.xyz/ws` (testnet also exists)
- IP rate limits: REST weight budget 1200/min and websocket caps; plus address-based limits
- Signing is easy to get wrong; use an SDK (recommended by docs)
- API wallets (agent wallets) can act without withdrawal permissions

---

## 2) Users and "personas"

- **Primary user (Owner):** you. Sets high-level targets + risk caps once, then the system runs itself.
- **Primary operator (OpenClaw + subagents):**
  - **Researcher:** proposes/iterates strategies
  - **Data engineer:** builds and maintains historical dataset
  - **Risk auditor:** enforces guardrails and approves/rejects deployments
  - **Execution engineer:** order management, slippage, latency, retries
  - **SRE/Observer:** monitors live system health and performance drift
- **Agent (Orchestrator)** should assign the most appropriate models available for the task to sub-agents.

---

## 3) Product success metrics (KPIs)

### 3.1 Trading KPIs (evaluated on out-of-sample + live)

- **Objective score (primary):** maximize Utility = CAGR – λ*MaxDD – μ*CVaR – ν*TurnoverCost
- **Secondary:** profit factor; Sharpe + Sortino; max drawdown and drawdown duration; win rate and average R-multiple; tail metrics (CVaR 95/99, worst day/week); slippage (bps) vs predicted; fill quality (% maker vs taker, effective spread capture).

### 3.2 Operational KPIs

- Uptime, reconnect success (websocket)
- No nonce collisions (see §9)
- Rate limit violations near-zero
- Trade loop latency distribution
- Incident count and automated recoveries

---

## 4) System architecture (high-level)

### 4.1 Modules

- **Market Data Layer:** Websocket subscribers (candle, trades, l2Book, bbo, activeAssetCtx); REST snapshot pullers (candleSnapshot, l2Book, metaAndAssetCtxs, etc.)
- **Historical Store:** Time-series storage (Postgres+Timescale or Parquet); versioned datasets (immutable snapshots) for reproducible backtests
- **Feature Engine:** Technical + microstructure + funding/OI + cross-asset signals; optional external signals behind strict reliability scoring
- **Regime Classifier:** Produces regime label + confidence per asset
- **Strategy Library:** Multiple strategies per regime (trend, range, volatility, funding)
- **Portfolio + Risk Engine:** Position sizing, leverage selection, exposure limits, kill-switch
- **Execution Engine:** Order decision → placement → lifecycle mgmt → fill reconciliation; uses /exchange, supports limit/trigger (TP/SL)
- **Backtest + Simulation Harness:** "Same logic" parity: live execution logic must be simulatable
- **Model Governance + Deployment Gate:** Baseline registry; only deploy if "beats baseline" and passes robustness
- **Monitoring + Alerting:** Metrics, logs, post-trade analytics, drift detection

---

## 5) Hyperliquid integration requirements

### 5.1 Authentication / API wallet

- Create/authorize an API wallet: `account_address` = main wallet public address; `secret_key` = API wallet private key (agent wallet).
- **Nonce management:** nonces stored per signer; each process should have its own signing key to avoid collisions.
- **Signing:** use SDK signing methods (docs warn about signature pitfalls).

### 5.2 Data endpoints needed

- meta and metaAndAssetCtxs (universe + funding + OI)
- candleSnapshot (bootstrapping history, 5000 candles max)
- WebSocket candle for continuous history
- clearinghouseState (positions/margin/account value)
- userFills, openOrders, orderStatus (reconciliation)

### 5.3 Trading endpoints needed

- Place orders (limit + trigger TP/SL, TIF, grouping options)
- Cancel/modify orders, update isolated margin (if used)

### 5.4 Rate-limit compliance

- Central "rate limit budgeter" for REST weights + websocket message counts
- Prefer websocket streaming for realtime data

---

## 6) Strategy framework: liquid assets + best timeframes

### 6.1 Asset universe selection (liquidity gate)

- Every hour (or faster): compute Liquidity Score per market (metaAndAssetCtxs + l2Book + recent volume): dayNtlVlm, openInterest, markPx, funding; spread + depth from l2Book.
- Trade only top N markets by Liquidity Score; exclude: spread above threshold, insufficient depth, abnormal funding volatility (optional).

### 6.2 Timeframe policy (multi-timeframe, liquid-first)

- **Regime detection:** 1h + 4h
- **Signal generation:** 15m (primary), 5m (tactical)
- **Execution timing / microstructure:** 1m (entry timing + slippage avoidance)

---

## 7) Regime detection (must exist before strategy switching)

### 7.1 Inputs (per asset)

- Returns + volatility (realized vol, ATR, Parkinson vol); trend strength (ADX, MA slope, Hurst proxy); range-ness (BB width percentile, mean reversion half-life); volume spikes, wick ratio, gap behavior; funding + OI from activeAssetCtx / metaAndAssetCtxs.

### 7.2 Outputs

- regime ∈ {TREND, RANGE, VOLATILE_BREAKOUT, MEAN_REVERT_CRASH, SQUEEZE_RISK, UNKNOWN}; confidence ∈ [0,1]; expected_slippage_bps (from l2Book depth).

### 7.3 Switching rule

- Switch only if: confidence > threshold; regime persists for K bars (hysteresis); not during no-trade windows (extreme vol / rate limit degraded / data stale).

---

## 8) Strategy library (multiple strategies per regime)

Starter strategies for hyper-liquid perps (long+short). Each has: entry logic, exit logic (TP/SL + trailing), position sizing & leverage rule, no-trade invalidations.

- **TREND:** Breakout + pullback continuation (4h trend filter; 15m entry; ATR stop/trail).
- **RANGE:** Mean reversion with vol-scaled bands (1h range filter; 15m entry at bands + RSI).
- **VOLATILE_BREAKOUT:** Compression → expansion (BB width + vol collapse; trigger on break + volume).
- **FUNDING / POSITIONING:** Extreme funding fade with price exhaustion (funding percentile + exhaustion; tight invalidation).
- **Meta-strategy:** pick top strategy for regime; cap correlated exposure; optional small ensemble.

---

## 9) Risk engine (guardrails that cannot be bypassed)

### 9.1 Portfolio constraints (global)

- Max concurrent positions; max total notional; max correlated cluster; daily/weekly loss limit; max drawdown circuit breaker; max order frequency (rate-limit safe).

### 9.2 Per-trade constraints

- Min R:R at entry (e.g. ≥ 1.8R); max slippage forecast; always place protective exits (trigger SL/TP); no averaging down unless strategy-defined and tested.

### 9.3 Leverage auto-selection (systematic)

- Respect: volatility, stop distance, liquidation buffer, margin availability (clearinghouseState). Rule: compute %stop, risk per trade r, target notional N; Lmax from margin tables; L = clamp with vol haircut; ensure liquidation price beyond stop + buffer. If constraints fail → reduce notional or skip.

### 9.4 Nonce + signer safety

- One Signer Service per process; nonce manager with persistent storage.

---

## 10) Execution engine (order quality is alpha)

- **Order types:** limit (maker bias) vs market/IOC when urgent; trigger orders for TP/SL; grouping.
- **Smart routing:** query l2Book; maker vs taker; chase logic with bounded retries.
- **Reconciliation:** orderUpdates WS + orderStatus REST.
- **Failure handling:** WS disconnect → reconnect + snapshot replay; partial fill → update stops; rate limit → degrade gracefully.

---

## 11) Data + backtesting requirements ("never deploy without beating baseline")

### 11.1 Data acquisition plan

- candleSnapshot capped at 5000: bootstrap last 5000 per interval per asset; then record via websocket candle stream. Store: candles (multi-TF), optional trades/l2, funding & OI. Deeper archival: HL monthly S3 (limited); bot should not depend on S3 completeness.

### 11.2 Backtest engine (must match live behavior)

- Event-driven (bar-by-bar + optional 1m/trades); limit fills (queue/slippage); market/IOC (spread + impact); trigger simulation; HL tick/lot from metadata; costs: maker/taker + funding. Outputs: trade ledger, equity curve, metrics + regime breakdown, parameter sensitivity.

### 11.3 Gating policy (deployment only if all pass)

- Walk-forward: train/opt on past window, test on next, roll forward.
- OOS superiority: utility beats baseline by Δ (config).
- Robustness: param perturbation + Monte Carlo within risk limits.
- Tail: MaxDD < threshold, CVaR < threshold.
- Operational feasibility: order rate within limits, slippage acceptable.
- Baseline: current production strategy set (versioned); new deployment updates baseline only after probation succeeds.

---

## 12) Drift prevention ("agent must not drift from goal")

### 12.1 Immutable guardrails

- Risk caps cannot be changed by strategy code; Risk Auditor must validate; override → shutdown + alert.

### 12.2 Continuous evaluation

- Daily: realized vs expected slippage; live vs backtest distribution; regime misclassification. If drift: reduce risk, revert to last known-good bundle, or research-only mode.

### 12.3 Change management

- Every bundle: version id, git commit hash, dataset version hash, backtest report. Rollback is one command.

---

## 13) Environments and deployment stages

- **Stage A:** Local research (no keys); backtests only.
- **Stage B:** Paper / shadow; no send or testnet; measure would-have performance.
- **Stage C:** Canary live; smallest sizes; strict daily loss cap; 1–2 assets.
- **Stage D:** Full live; expand assets; regime switching and ensemble if validated.

---

## 14) Security requirements

- API wallet keys in secrets manager (not in repo); rotation policy; least privilege (no withdrawals); audit logs for every order action.

---

## 15) Implementation milestones (what OpenClaw should build)

- **P0 — Connectivity + Market Data:** HL Python SDK; WS subscriber + reconnect; REST snapshot fetchers; storage schema + dataset versioning.
- **P1 — Backtest harness (parity-first):** Event-driven simulator; cost + funding; metrics/report; baseline registry.
- **P2 — Regime + core strategies:** Classifier + confidence/hysteresis; 3–5 strategy modules (§8); strategy invariant tests.
- **P3 — Risk + leverage + execution:** Portfolio/per-trade constraints; leverage selector (§9.3); order manager + reconciliation.
- **P4 — Governance + self-improvement:** Auto research loop (propose → walk-forward → compare → deploy only if gates pass); drift monitoring + rollback.

---

## 16) OpenClaw agent decomposition (subagent responsibilities)

- **Data Engineer:** candleSnapshot bootstrap + WS candle; metaAndAssetCtxs universe refresh; l2Book (rate-aware); dataset versioning + integrity.
- **Quant Researcher:** Strategies + regime classifier; optimization + robustness; backtest reports and candidate diffs.
- **Risk Auditor (veto):** Guardrails, gating metrics, operational feasibility; reject deployments that do not beat baseline.
- **Execution Engineer:** Place/cancel/modify (TIF/trigger grouping); reconcile fills (userFills, WS); slippage prediction + maker/taker.
- **SRE/Monitoring:** Alerts (disconnects, rate limits, PnL variance); auto degrade under infra stress.

---

## 17) Concrete acceptance criteria (definition of done)

- Runs continuously, reconnects safely, respects rate limits.
- Trades only top-liquidity markets from on-exchange data.
- Long + short with protective exits.
- Leverage selection systematic, logged, test-covered.
- Reproducible backtests (dataset hash + report artifact).
- No deploy unless change beats baseline via gates (§11.3).
- One-command rollback and automated drift response.

---

## 18) Minimal config spec (deterministic build)

Single config (e.g. config/base.yaml) must control: universe selection (top N, thresholds); risk caps (global + per trade); strategy toggles + param ranges (for optimization); gating thresholds (utility delta, DD cap, robustness); execution policy (maker bias, max retries, max slippage bps); environment (paper/canary/live), API endpoints, websocket URLs.
