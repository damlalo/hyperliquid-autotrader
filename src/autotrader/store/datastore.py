"""Storage abstraction: IDataStore protocol, DataStore facade, and factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

from autotrader.store.parquet import ParquetStore

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IDataStore(Protocol):
    """Structural interface for all storage back-ends."""

    def write_candles(self, coin: str, interval: str, df: pd.DataFrame) -> None: ...

    def read_candles(
        self,
        coin: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame: ...

    def write_funding(self, coin: str, df: pd.DataFrame) -> None: ...

    def read_funding(
        self,
        coin: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame: ...

    def write_fills(self, df: pd.DataFrame) -> None: ...

    def read_fills(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame: ...


# ---------------------------------------------------------------------------
# DataStore — delegates to a concrete back-end
# ---------------------------------------------------------------------------


class DataStore:
    """Thin facade that delegates to either ParquetStore or a Postgres back-end."""

    def __init__(self, backend: IDataStore) -> None:
        self._backend = backend

    def write_candles(self, coin: str, interval: str, df: pd.DataFrame) -> None:
        self._backend.write_candles(coin, interval, df)

    def read_candles(
        self,
        coin: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        return self._backend.read_candles(coin, interval, start_ms, end_ms)

    def write_funding(self, coin: str, df: pd.DataFrame) -> None:
        self._backend.write_funding(coin, df)

    def read_funding(
        self,
        coin: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        return self._backend.read_funding(coin, start_ms, end_ms)

    def write_fills(self, df: pd.DataFrame) -> None:
        self._backend.write_fills(df)

    def read_fills(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        return self._backend.read_fills(start_ms, end_ms)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _account_safe(account_address: str) -> str:
    """Return a filesystem-safe 16-char identifier for an account address."""
    return account_address.lower().lstrip("0x")[:16] or "default"


def get_datastore(config: dict, account_address: str = "") -> DataStore:
    """Construct and return a ``DataStore`` appropriate for *config*.

    Data is namespaced under ``{base_dir}/{account_id}/`` when
    *account_address* is provided, so multiple accounts running on the same
    machine never share raw candle or fill data.

    Checks ``storage.postgres_dsn`` (config key) and the ``POSTGRES_DSN``
    environment variable.  Falls back to ``ParquetStore`` when neither is set.
    """
    storage_cfg: dict = config.get("storage", {})
    postgres_dsn: str = storage_cfg.get("postgres_dsn", "") or os.environ.get(
        "POSTGRES_DSN", ""
    )

    import logging as _log
    _logger = _log.getLogger(__name__)

    backend: IDataStore | None = None

    if postgres_dsn and not postgres_dsn.startswith("${"):
        try:
            from autotrader.store.postgres import PostgresStore  # type: ignore[import]
            backend = PostgresStore(postgres_dsn)
            _logger.info("datastore: using PostgresStore")
        except ImportError:
            _logger.warning("POSTGRES_DSN set but asyncpg not installed — falling back to ParquetStore")
        except Exception as exc:
            _logger.warning("PostgresStore init failed (%s) — falling back to ParquetStore", exc)

    if backend is None:
        base_dir: str = storage_cfg.get("base_dir", "data/processed")
        if account_address:
            base_dir = str(Path(base_dir) / _account_safe(account_address))
        backend = ParquetStore(base_dir=base_dir)
        _logger.info("datastore: using ParquetStore at %s", base_dir)

    return DataStore(backend=backend)
