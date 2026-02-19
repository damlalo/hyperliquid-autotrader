# Start Here

This repo is **agent-consumable**. Follow the reading order below so agents and humans share the same mental model.

## Reading order

1. **agents/MASTER.md** — Mission, non-negotiables, and milestone plan. Read first.
2. **docs/PRD.md** — Product and system goals, constraints, strategy framework.
3. **docs/GOVERNANCE.md** — Baseline registry, candidate lifecycle, gates, rollback.
4. **docs/RISK_GUARDRAILS.md** — Immutable constraints, circuit breakers, kill switch.
5. **docs/BACKTEST_METHODOLOGY.md** — Event-driven backtest, cost model, walk-forward, acceptance criteria.

Then reference as needed: ARCHITECTURE.md, DATA_MODEL.md, STRATEGY_LIBRARY.md, RUNBOOK.md, REFERENCES.md.

## How to run in paper mode (placeholder)

- Activate venv, set env vars from `.env`.
- Run: `python -m autotrader.main --env paper --config config/paper.yaml`
- Or: `python scripts/paper_trade.py --config config/paper.yaml` (when implemented).
- Paper mode uses the same code path as live but does not send real orders; config caps and risk limits apply.

## How to run backtests (placeholder)

- `python scripts/run_backtest.py --config config/paper.yaml`
- `python scripts/run_walkforward.py --config config/paper.yaml`
- Scripts currently validate config and print intent; full backtest logic is TODO.

## Never deploy without gates

- Candidates must pass governance gates (Utility, MaxDD/CVaR, feasibility, robustness) before promotion.
- Use staging: research → paper → canary → live. See docs/GOVERNANCE.md and docs/RUNBOOK.md.
