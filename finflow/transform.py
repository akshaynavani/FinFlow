"""Transform step: run SQL models inside the warehouse.

Models are ``.sql`` files in ``finflow/sql`` executed in lexical order
(numeric prefixes encode dependencies: ``0x_`` staging, ``1x_`` marts).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from finflow.logging_utils import get_logger
from finflow.warehouse import Warehouse

logger = get_logger(__name__)

_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)


@dataclass
class ModelResult:
    model: str
    tables: list[str]
    row_counts: dict[str, int]


def _safe_ln(x: float | None) -> float | None:
    if x is None or x <= 0:
        return None
    return math.log(x)


def _safe_exp(x: float | None) -> float | None:
    if x is None:
        return None
    return math.exp(x)


def register_sql_functions(wh: Warehouse) -> None:
    wh.conn.create_function("ff_ln", 1, _safe_ln, deterministic=True)
    wh.conn.create_function("ff_exp", 1, _safe_exp, deterministic=True)


def default_models_dir() -> Path:
    return Path(str(resources.files("finflow") / "sql"))


def discover_models(models_dir: Path | None = None) -> list[Path]:
    models_dir = models_dir or default_models_dir()
    return sorted(models_dir.glob("*.sql"))


def created_tables(sql: str) -> list[str]:
    """Return persistent tables a model creates (temp tables excluded)."""
    tables = []
    for statement in sql.split(";"):
        if re.search(r"CREATE\s+TEMP", statement, re.IGNORECASE):
            continue
        match = _CREATE_TABLE.search(statement)
        if match:
            tables.append(match.group(1))
    return tables


def run_model(wh: Warehouse, path: Path) -> ModelResult:
    sql = path.read_text()
    wh.executescript(sql)
    tables = created_tables(sql)
    counts = {t: wh.row_count(t) for t in tables}
    logger.info("model_built", extra={"model": path.stem, "row_counts": counts})
    return ModelResult(model=path.stem, tables=tables, row_counts=counts)


def run_models(
    wh: Warehouse, models_dir: Path | None = None, select: list[str] | None = None
) -> list[ModelResult]:
    """Run all models (or those whose name contains any ``select`` token)."""
    register_sql_functions(wh)
    results = []
    for path in discover_models(models_dir):
        if select and not any(token in path.stem for token in select):
            continue
        results.append(run_model(wh, path))
    return results
