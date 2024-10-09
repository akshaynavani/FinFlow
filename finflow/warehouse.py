"""Thin wrapper around a SQLite warehouse.

Keeps all database access in one place so the backend could be swapped for
Postgres or DuckDB without touching the rest of the pipeline.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    """Validate and quote a SQL identifier (table / column name)."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{name}"'


class Warehouse:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- execution -------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def executescript(self, sql: str) -> None:
        self.conn.executescript(sql)
        self.conn.commit()

    def query(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def read_df(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.conn, params=params)

    def read_table(self, table: str) -> pd.DataFrame:
        return self.read_df(f"SELECT * FROM {quote_ident(table)}")

    def write_df(self, df: pd.DataFrame, table: str, if_exists: str = "replace") -> int:
        quote_ident(table)
        df.to_sql(table, self.conn, if_exists=if_exists, index=False)
        self.conn.commit()
        return len(df)

    # -- introspection ---------------------------------------------------
    def table_exists(self, table: str) -> bool:
        return bool(
            self.scalar(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                (table,),
            )
        )

    def tables(self) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            )
        ]

    def columns(self, table: str) -> list[str]:
        return [r[1] for r in self.query(f"PRAGMA table_info({quote_ident(table)})")]

    def row_count(self, table: str) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {quote_ident(table)}"))
