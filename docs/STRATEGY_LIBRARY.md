# Strategy Library

## Strategies

- **Trend breakout** — Entries on breakout signals in trend regimes. Invariants: use regime filter; respect max size and stop.
- **Range mean reversion** — Mean reversion in range regimes. Invariants: only in range regime; bounded position and stop.
- **Vol expansion** — Trade volatility expansion with defined risk. Invariants: vol-based sizing; hard stop.
- **Funding extremes** — Exploit extreme funding with caps. Invariants: funding threshold filters; max exposure and holding period.

Each strategy implements base interface: signal generation, no-trade conditions, and respects risk layer caps (no self-override).

## Regime-to-strategy mapping

- Regime classifier (regimes/classifier) outputs regime (e.g. trend, range, high_vol).
- Mapping: which strategies are allowed in which regime (config or code).
- Hysteresis (regimes/hysteresis) prevents flapping: require regime to hold for N bars or time before switching.

## Strategy selection logic

- Ensemble can combine multiple strategies with weights.
- Selection: regime + hysteresis → active strategies → signals combined → risk layer applies sizing and constraints.
- No-trade conditions: per strategy (e.g. low liquidity, missing data, funding in neutral zone).
