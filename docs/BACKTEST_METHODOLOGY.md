# Backtest Methodology

## Event-driven simulation

- Backtest engine is event-driven: process bars (or ticks) in time order; no lookahead.
- Signals and orders are generated as of bar close (or defined event time); fills and PnL computed with defined cost model.

## Cost model

- **Fees:** Configurable (maker/taker or flat); applied per fill.
- **Funding:** Apply funding payments per position at funding times (from data or model).
- **Slippage:** Start simple (e.g. fixed bps or linear in size); optional spread model later.

Exact formulas in backtest/cost_model and backtest/engine.

## Walk-forward testing

- Rolling windows: train on window T, test on out-of-sample window T+1.
- Multiple folds or expanding window; avoid overlap between train and test.
- Acceptance: candidate must beat baseline on OOS metric (e.g. utility, risk-adjusted return) and pass risk constraints.

## Robustness tests

- **Param perturbation:** Vary key params slightly; performance should not collapse.
- **Monte Carlo:** Resample or perturb order of events where applicable; check stability of metrics.
- Results feed into governance gates (must pass to be eligible for promotion).

## Acceptance criteria

- Must beat baseline on defined Utility (and any secondary metrics).
- MaxDD and CVaR within limits.
- No obvious overfitting (robustness tests pass).
- Feasibility: execution and slippage assumptions documented and met in simulation.
