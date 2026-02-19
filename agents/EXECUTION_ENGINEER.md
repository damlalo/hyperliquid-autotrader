# EXECUTION_ENGINEER — Agent Instructions

## Mission

Implement execution layer: broker (HL exchange API), order manager, slippage model, reconciliation. Respect rate limits and nonces; ensure order lifecycle and fill reconciliation are correct.

## Deliverables

- execution/broker.py (place/cancel/modify via HL exchange endpoint).
- execution/order_manager.py (lifecycle).
- execution/slippage.py (model for backtest and live).
- execution/reconciliation.py (match fills to orders).

## Must-have tests

- Mock broker: place/cancel/modify return expected shapes.
- Reconciliation: given a set of orders and fills, positions and PnL consistent.
- No hardcoded keys; use env for API wallet.

## Non-negotiables

- Use hl/client and hl/rate_limiter, hl/nonces; no direct HTTP in execution without going through HL layer.
- Do not invent HL order format; use REFERENCES and official SDK/docs; TODO if unclear.
