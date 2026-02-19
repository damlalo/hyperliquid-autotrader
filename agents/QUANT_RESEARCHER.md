# QUANT_RESEARCHER — Agent Instructions

## Mission

Implement features, regime classification, strategy library, and backtest (engine, cost model, walk-forward, robustness). Ensure event-driven backtest, no lookahead, and acceptance criteria per BACKTEST_METHODOLOGY.md.

## Deliverables

- features/* (technical, microstructure, positioning).
- regimes/classifier.py, hysteresis.py.
- strategies/base.py and library (trend_breakout, range_meanrev, vol_expansion, funding_extremes, ensemble).
- backtest/engine.py, cost_model.py, metrics.py, walkforward.py, robustness.py, reporting.py.

## Must-have tests

- Backtest event-driven and deterministic on fixture data.
- Regime classifier and hysteresis unit tests.
- Strategy invariants: no override of risk caps; no-trade conditions respected (test_strategy_invariants).
- Walk-forward and robustness run and produce metrics.

## Non-negotiables

- No lookahead in features or backtest.
- Cost model: fees, funding, slippage (start simple).
- Must beat baseline for candidate eligibility (gates in governance).
