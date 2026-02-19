# DATA_ENGINEER — Agent Instructions

## Mission

Implement and maintain the data pipeline: collectors (candles, L2, funding/OI, user state), transforms (resample, cleaning), store (datastore, Postgres, Parquet), and dataset hashing. Ensure no lookahead, deterministic hashes, and reproducibility.

## Deliverables

- data/collectors/* (candles, l2book, funding_oi, user_state) writing to store.
- data/transforms/* (resample, cleaning).
- store/datastore.py, postgres.py, parquet.py, dataset_hash.py.
- Dataset manifests and hash used in governance and backtest.

## Must-have tests

- Dataset hash deterministic for same inputs.
- Transforms and feature pipelines do not use future data (unit test with known series).
- Store read/write for candles and funding (unit or integration with skip if no DB).

## Non-negotiables

- All secrets (e.g. Postgres) from env vars.
- Data model aligns with docs/DATA_MODEL.md.
- Do not invent HL endpoints; use REFERENCES.md and TODO if unknown.
