# Risk Guardrails

## Immutable constraints

Strategies **cannot** change or override portfolio or per-trade caps. Constraints are defined in config and risk module; strategies only produce signals; risk layer applies caps.

## Portfolio constraints

- Max total exposure (notional or margin).
- Max per-asset or per-market exposure.
- Max correlation or concentration limits (if implemented).
- Daily/weekly loss limits (circuit breakers).

## Per-trade constraints

- Max size per order.
- Max leverage per position.
- Stop distance and liquidation buffer requirements (used for leverage selection).

## Circuit breakers

- **Daily:** Pause or reduce if daily loss exceeds threshold.
- **Weekly:** Same for weekly loss.
- **MaxDD:** Global max drawdown limit; breach triggers kill switch or pause.

Exact thresholds are config-driven (e.g. in `config/base.yaml` or env).

## Kill switch triggers

- Manual kill switch (runtime/kill_switch).
- Breach of max drawdown or tail-risk limit.
- Repeated order or nonce errors beyond threshold.
- Data staleness or connectivity loss beyond allowed window.

When triggered: cancel open orders, flatten or reduce positions per policy, stop new orders, alert.

## Leverage selection

- **Systematic:** Not chosen by strategy; chosen by risk/sizing module.
- **Inputs:** Stop distance, liquidation buffer, and config caps.
- **Rule:** Leverage set so that stop-out distance is beyond normal volatility and within buffer; never exceed config max leverage.

Details: see risk/leverage and risk/sizing; invariants in tests.
