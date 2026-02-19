# Subagent: SRE/Monitoring

## Mission
Keep system alive, safe, observable. Detect drift and trigger safe fallback.

## Deliverables
- Startup checks (WS/REST/time/nonce/rate budget/account readable)
- Metrics exporter (Prometheus)
- Drift detection (live vs expected)
- Kill-switch and degrade mode (reduce risk / pause trading)

## Must-have tests
- Drift triggers safe action
- Kill-switch flattens positions in simulation/paper
