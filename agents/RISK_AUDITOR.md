# RISK_AUDITOR — Agent Instructions

## Mission

Implement and verify risk layer: constraints (portfolio + per-trade), sizing, leverage (systematic selection), exposure, approvals. Ensure immutable constraints and no strategy override of caps.

## Deliverables

- risk/constraints.py, sizing.py, leverage.py, exposure.py, approvals.py.
- Circuit breaker and kill switch triggers integrated with runtime.
- Documentation in RISK_GUARDRAILS.md kept accurate.

## Must-have tests

- test_risk_constraints.py: over-size and over-leverage blocked.
- Leverage selection respects stop distance and liquidation buffer (unit tests).
- Approvals/gates reject when metrics fail thresholds.

## Non-negotiables

- Strategies cannot change caps; risk layer is sole authority.
- Leverage selection is systematic (config + stop distance + buffer), not strategy-chosen.
- All thresholds config-driven or env-based.
