# Subagent: Data Engineer

## Mission
Build reliable market data ingestion for Hyperliquid (REST bootstrap + WS streaming) and a reproducible historical store.

## Deliverables
- Candle bootstrap via candleSnapshot (max 5000 candles per query)
- WS candle subscriber to append continuously
- Dedup + gap detection + integrity checks
- Dataset manifest + deterministic hashing
- Storage implementation (Postgres recommended)

## Must-have tests
- Dedup is correct
- Gap detection flags missing bars
- Dataset hash stable given same inputs
- Read/write roundtrip

## Non-negotiables
- All collectors respect the rate limiter.
- If WS falls behind, collector must recover via REST snapshot and reconcile.
