# Data Model

## Tables (logical)

- **candles** — OHLCV + timeframe + symbol; timestamp index.
- **fills** — Trade fills (exchange-side); link to order id.
- **orders** — Order lifecycle (created, updated, filled, cancelled).
- **positions** — Snapshot or delta of positions per symbol.
- **funding** — Funding rates and payments; timestamp, symbol.
- **book snapshots** (optional) — L2 snapshots for microstructure; can be sampled.

Exact schema (column names, types) is implementation-defined in store/datastore and postgres modules. Parquet for bulk/analytics where used.

## Dataset manifests and hashing

- **Manifests:** Describe dataset scope (symbols, time range, sources). Stored in `data/manifests/` or in store.
- **Hashing:** Deterministic hash of manifest + critical data (or manifest only) to tag a dataset version. Used in baseline registry and reproducibility. See store/dataset_hash.

## Reproducibility

- Backtest and walk-forward runs should record: config hash or path, dataset hash, strategy bundle version.
- Baseline `current.json` includes dataset_hash where applicable so that promoted candidates are tied to a known dataset.
