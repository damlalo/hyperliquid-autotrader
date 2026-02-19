# Subagent: Risk Auditor (Veto Power)

## Mission
Ensure no deployment violates guardrails; ensure candidate gating is real and not gamed.

## Deliverables
- Immutable constraints module
- Gate checks (baseline comparison, MaxDD/CVaR caps)
- Operational feasibility checks (order rate, expected slippage, rate limit safety)
- Reject deployments with explicit reasons

## Must-have tests
- Trades that violate risk constraints are rejected
- Gate rejects candidates that are worse or too fragile
- Live runner refuses to start without baseline + passing healthcheck
