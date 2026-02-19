# Governance

## Baseline registry

- **Source of truth:** `artifacts/baselines/current.json`.
- **History:** `artifacts/baselines/history/` for rollbacks (timestamped copies).
- Contents: version, created_at, dataset_hash, strategy_bundle, metrics_summary (and any fields required for reproducibility).

## Candidate lifecycle

1. **Research** — Develop strategy/params on frozen or versioned dataset.
2. **Dataset freeze** — Lock dataset hash for the candidate run.
3. **Walk-forward / OOS** — Run WFO and out-of-sample tests.
4. **Robustness** — Param perturbation, Monte Carlo (per BACKTEST_METHODOLOGY.md).
5. **Gates** — Must pass all gate requirements before promotion.
6. **Probation** — Optional canary period with reduced size/caps.
7. **Promote** — Update `current.json` via `promote_candidate.py`; append to history.

## Gate requirements

A candidate may be promoted only if:

- **Utility** — Beats baseline on defined utility metric (e.g. risk-adjusted return).
- **MaxDD / CVaR** — Within acceptable drawdown and tail-risk limits.
- **Feasibility** — Execution and slippage assumptions hold; no overfitting red flags.
- **Robustness** — Passes robustness tests (perturbation, Monte Carlo).

Gates are enforced by scripts and/or manual checklist; no bypass in code.

## Rollback procedure

1. Identify last known-good baseline from `artifacts/baselines/history/`.
2. Run `scripts/rollback.py` (or manually copy history file to `current.json`).
3. Restart live/canary so it loads the reverted baseline.
4. Document reason and timestamp in runbook or incident log.

## Drift response

- **Detect:** Monitoring and governance/drift module detect metric or distribution drift.
- **Actions:** Reduce risk (size/caps), pause trading, or revert to previous baseline.
- Document triggers and actions in RUNBOOK.md and RISK_GUARDRAILS.md.
