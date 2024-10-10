"""Data quality check implementations.

Each check takes a warehouse, a table name and check-specific parameters and
returns ``(failing_rows, detail)``. Checks are registered by name in
``CHECKS`` so suites can be declared in YAML.

SQL checks push work down to the warehouse; statistical checks (null ratio,
outliers) pull the relevant columns into Pandas.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import numpy as np

from finflow.warehouse import Warehouse, quote_ident

CheckFn = Callable[..., tuple[int, str]]
CHECKS: dict[str, CheckFn] = {}


def register(name: str) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        CHECKS[name] = fn
        return fn

    return decorator


def _cols(columns: str | list[str]) -> list[str]:
    return [columns] if isinstance(columns, str) else list(columns)


@register("not_null")
def not_null(wh: Warehouse, table: str, columns: str | list[str]) -> tuple[int, str]:
    cols = _cols(columns)
    predicate = " OR ".join(f"{quote_ident(c)} IS NULL" for c in cols)
    failing = wh.scalar(f"SELECT COUNT(*) FROM {quote_ident(table)} WHERE {predicate}")
    return int(failing), f"rows with NULL in {cols}"


@register("unique")
def unique(wh: Warehouse, table: str, columns: str | list[str]) -> tuple[int, str]:
    cols = ", ".join(quote_ident(c) for c in _cols(columns))
    failing = wh.scalar(
        f"SELECT COALESCE(SUM(n - 1), 0) FROM ("
        f"SELECT COUNT(*) AS n FROM {quote_ident(table)} GROUP BY {cols} HAVING COUNT(*) > 1)"
    )
    return int(failing), f"duplicate rows on ({cols})"


@register("range")
def value_range(
    wh: Warehouse,
    table: str,
    column: str,
    min: float | None = None,  # noqa: A002 - YAML-friendly names
    max: float | None = None,  # noqa: A002
    inclusive: bool = True,
) -> tuple[int, str]:
    col = quote_ident(column)
    lo, hi = ("<", ">") if inclusive else ("<=", ">=")
    clauses, params = [], []
    if min is not None:
        clauses.append(f"{col} {lo} ?")
        params.append(min)
    if max is not None:
        clauses.append(f"{col} {hi} ?")
        params.append(max)
    if not clauses:
        raise ValueError("range check requires min and/or max")
    failing = wh.scalar(
        f"SELECT COUNT(*) FROM {quote_ident(table)} WHERE {col} IS NOT NULL AND "
        f"({' OR '.join(clauses)})",
        params,
    )
    return int(failing), f"{column} outside [{min}, {max}]"


@register("accepted_values")
def accepted_values(
    wh: Warehouse, table: str, column: str, values: list[Any]
) -> tuple[int, str]:
    placeholders = ", ".join("?" for _ in values)
    failing = wh.scalar(
        f"SELECT COUNT(*) FROM {quote_ident(table)} "
        f"WHERE {quote_ident(column)} IS NOT NULL AND {quote_ident(column)} NOT IN ({placeholders})",
        list(values),
    )
    return int(failing), f"{column} not in {values}"


@register("row_count")
def row_count(
    wh: Warehouse, table: str, min: int = 1, max: int | None = None  # noqa: A002
) -> tuple[int, str]:
    n = wh.row_count(table)
    ok = n >= min and (max is None or n <= max)
    return (0 if ok else 1), f"row count {n} (expected [{min}, {max}])"


@register("relationships")
def relationships(
    wh: Warehouse, table: str, column: str, to_table: str, to_column: str
) -> tuple[int, str]:
    failing = wh.scalar(
        f"SELECT COUNT(*) FROM {quote_ident(table)} c "
        f"LEFT JOIN {quote_ident(to_table)} p ON c.{quote_ident(column)} = p.{quote_ident(to_column)} "
        f"WHERE c.{quote_ident(column)} IS NOT NULL AND p.{quote_ident(to_column)} IS NULL"
    )
    return int(failing), f"{table}.{column} values missing from {to_table}.{to_column}"


@register("expression")
def expression(wh: Warehouse, table: str, condition: str) -> tuple[int, str]:
    """Every row must satisfy ``condition`` (a trusted SQL boolean expression from config)."""
    failing = wh.scalar(
        f"SELECT COUNT(*) FROM {quote_ident(table)} WHERE NOT ({condition})"
    )
    return int(failing), f"rows violating: {condition}"


@register("custom_sql")
def custom_sql(wh: Warehouse, table: str, sql: str) -> tuple[int, str]:
    """``sql`` must return a single integer: the number of failing rows."""
    failing = wh.scalar(sql.replace("{table}", quote_ident(table)))
    return int(failing or 0), "custom SQL check"


@register("freshness")
def freshness(
    wh: Warehouse,
    table: str,
    column: str,
    max_age_days: int,
    as_of: str | None = None,
) -> tuple[int, str]:
    latest = wh.scalar(f"SELECT MAX({quote_ident(column)}) FROM {quote_ident(table)}")
    if latest is None:
        return 1, f"{column} has no values"
    reference = datetime.fromisoformat(as_of).date() if as_of else date.today()
    age = (reference - datetime.fromisoformat(str(latest)[:10]).date()).days
    return (0 if age <= max_age_days else 1), f"latest {column}={latest}, age {age}d"


@register("null_ratio")
def null_ratio(wh: Warehouse, table: str, column: str, max_ratio: float) -> tuple[int, str]:
    total, nulls = wh.query(
        f"SELECT COUNT(*), SUM(CASE WHEN {quote_ident(column)} IS NULL THEN 1 ELSE 0 END) "
        f"FROM {quote_ident(table)}"
    )[0]
    ratio = (nulls or 0) / total if total else 0.0
    return (0 if ratio <= max_ratio else int(nulls)), f"{column} null ratio {ratio:.4f}"


@register("zscore_outliers")
def zscore_outliers(
    wh: Warehouse,
    table: str,
    column: str,
    threshold: float = 6.0,
    group_by: str | None = None,
    max_outliers: int = 0,
) -> tuple[int, str]:
    """Flag values more than ``threshold`` std devs from their (group) mean."""
    cols = [column] + ([group_by] if group_by else [])
    df = wh.read_df(
        f"SELECT {', '.join(quote_ident(c) for c in cols)} FROM {quote_ident(table)}"
    )
    values = df[column].astype(float)
    if group_by:
        grouped = values.groupby(df[group_by])
        z = (values - grouped.transform("mean")) / grouped.transform("std")
    else:
        z = (values - values.mean()) / values.std()
    n = int((np.abs(z) > threshold).sum())
    return (n if n > max_outliers else 0), f"{n} |z| > {threshold} outliers in {column}"
