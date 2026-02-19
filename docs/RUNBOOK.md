# Runbook

## Common incidents

### Websocket disconnect loops

- **Symptom:** Repeated WS disconnect/reconnect; high message loss or lag.
- **Actions:** Check rate limits; reduce subscription set if needed; backoff and reconnect with jitter; see hl/ws and REFERENCES (WS limits).
- **Escalation:** If persistent, pause trading and fix client or infra.

### Rate limit pressure

- **Symptom:** 429 or rate limit errors from HL REST.
- **Actions:** Respect hl/rate_limiter; backoff; reduce request frequency; check weight limits (see REFERENCES).
- **Escalation:** Reduce trading frequency or scale down strategies until within limits.

### Nonce errors

- **Symptom:** Nonce too low or invalid from exchange.
- **Actions:** Use hl/nonces; ensure single writer or serialized order submission; refresh nonce from /info if needed (see REFERENCES).
- **Escalation:** Pause new orders until nonce state corrected; do not retry with same nonce.

### Data gaps

- **Symptom:** Missing candles or funding for a period; backtest or live state inconsistent.
- **Actions:** Backfill from HL historical or S3 archive if available; or mark dataset invalid and do not trade on that period.
- **Escalation:** Halt strategies that depend on missing data until resolved.

### Unexpected drawdown

- **Symptom:** Realized drawdown exceeds expectation or threshold.
- **Actions:** Trigger kill switch or circuit breaker per RISK_GUARDRAILS; reduce size or pause; investigate cause.
- **Escalation:** Rollback to last known-good baseline if needed; document in incident log.

## Recovery actions

- **Kill switch:** Run kill switch routine (cancel orders, flatten/reduce per policy); restart only after clearance.
- **Rollback:** Use scripts/rollback.py and restart with previous baseline.
- **Data repair:** Backfill or exclude bad period; update dataset hash if scope changed.

## Canary / live rollout checklist

- Baseline promoted and current.json updated.
- Gates passed (Utility, MaxDD/CVaR, feasibility, robustness).
- Canary: reduced size/caps; run for defined period; monitor alerts and PnL.
- Live: Increase to full caps only after canary success; continue monitoring.
