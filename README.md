# Hyperliquid Autonomous Trader

Automated trading system for Hyperliquid perpetuals with regime detection, strategy library, risk guardrails, and staged rollout (paper → canary → live).

## Safety disclaimers

- **Automated trading involves substantial risk.** Loss of capital is possible. Past backtest performance does not guarantee future results.
- **Never deploy without passing governance gates.** Research and backtest first; use paper and canary before live.
- All secrets must be provided via environment variables. Never commit private keys.

## Setup

1. **Python environment**
   - Use Python 3.11+ (see `.python-version`).
   - Create and activate a virtualenv: `python -m venv .venv && source .venv/bin/activate` (or `\.venv\Scripts\activate` on Windows).

2. **Install**
   - `pip install -e .`

3. **Config**
   - Copy `.env.example` to `.env` and fill in placeholder values (never commit `.env`).
   - Ensure `config/base.yaml` exists; override with `config/paper.yaml`, `config/canary.yaml`, or `config/live.yaml` as needed.

4. **Env vars**
   - See `config/secrets.schema.md` for required and optional variables. At minimum: `HL_ACCOUNT_ADDRESS`, `HL_API_WALLET_PRIVATE_KEY` (for trading), `POSTGRES_DSN` (if using Postgres).

## How to run (placeholders)

- **Paper mode:** `python -m autotrader.main --env paper` (or use `scripts/paper_trade.py` when implemented).
- **Backtests:** `python scripts/run_backtest.py --config config/paper.yaml` (placeholder; validate config and exit).
- **Walk-forward:** `python scripts/run_walkforward.py --config config/paper.yaml` (placeholder).

See **START_HERE.md** for agent reading order and full workflow. Never deploy to live without passing gates and following GOVERNANCE.md.
