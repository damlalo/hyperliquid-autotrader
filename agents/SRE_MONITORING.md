# SRE_MONITORING — Agent Instructions

## Mission

Implement monitoring, alerts, dashboards, runbook alignment, and operational scripts (healthcheck, promote, rollback). Ensure observability and safe rollout/rollback.

## Deliverables

- monitoring/logger.py, metrics.py, alerts.py, dashboards.py.
- scripts/healthcheck.py, promote_candidate.py, rollback.py (implement to spec).
- infra: Prometheus/Grafana placeholders or minimal config.
- RUNBOOK.md and docs updated with exact commands and troubleshooting.

## Must-have tests

- Healthcheck script runs and exits 0/1 as expected (config/connectivity).
- Promote and rollback scripts update baseline and history correctly (unit or integration).
- Alert path exercised in test (mock or dry-run).

## Non-negotiables

- No secrets in config; alert webhooks or keys from env.
- Rollback procedure documented and scripted; no manual-only steps without doc.
