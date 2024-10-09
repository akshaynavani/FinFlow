"""Load step: land extracted frames in raw tables with ingestion metadata.

Every raw row carries ``_run_id``, ``_source_file`` and ``_ingested_at`` so
downstream models and quality checks can trace lineage. Each load is recorded
in the ``etl_load_audit`` table, and loads are skipped when the source file
checksum is unchanged since the last successful load (idempotent re-runs).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from finflow.extract import ExtractedSource
from finflow.logging_utils import get_logger
from finflow.warehouse import Warehouse, quote_ident

logger = get_logger(__name__)

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS etl_load_audit (
    run_id       TEXT NOT NULL,
    source       TEXT NOT NULL,
    raw_table    TEXT NOT NULL,
    source_file  TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    rows_loaded  INTEGER NOT NULL,
    loaded_at    TEXT NOT NULL,
    status       TEXT NOT NULL
);
"""

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    detail       TEXT
);
"""


@dataclass
class LoadResult:
    source: str
    raw_table: str
    rows: int
    skipped: bool


def ensure_metadata_tables(wh: Warehouse) -> None:
    wh.executescript(AUDIT_DDL + RUNS_DDL)


def _last_checksum(wh: Warehouse, source: str) -> str | None:
    return wh.scalar(
        "SELECT checksum FROM etl_load_audit WHERE source = ? AND status = 'loaded' "
        "ORDER BY loaded_at DESC LIMIT 1",
        (source,),
    )


def load_source(
    wh: Warehouse, extracted: ExtractedSource, run_id: str, force: bool = False
) -> LoadResult:
    source = extracted.source
    table = source.raw_table
    quote_ident(table)
    now = datetime.now(timezone.utc).isoformat()

    if (
        not force
        and wh.table_exists(table)
        and _last_checksum(wh, source.name) == extracted.checksum
    ):
        logger.info("source_load_skipped", extra={"source": source.name, "reason": "unchanged"})
        wh.execute(
            "INSERT INTO etl_load_audit VALUES (?,?,?,?,?,?,?,?)",
            (run_id, source.name, table, str(extracted.path), extracted.checksum, 0, now,
             "skipped"),
        )
        return LoadResult(source.name, table, wh.row_count(table), skipped=True)

    df = extracted.frame.copy()
    df["_run_id"] = run_id
    df["_source_file"] = str(extracted.path)
    df["_ingested_at"] = now

    # Full refresh of the raw table: research datasets are re-delivered as
    # complete snapshots, so replace-on-change keeps the raw layer simple.
    with wh.transaction():
        rows = wh.write_df(df, table, if_exists="replace")
        wh.conn.execute(
            "INSERT INTO etl_load_audit VALUES (?,?,?,?,?,?,?,?)",
            (run_id, source.name, table, str(extracted.path), extracted.checksum, rows, now,
             "loaded"),
        )
    logger.info("source_loaded", extra={"source": source.name, "table": table, "rows": rows})
    return LoadResult(source.name, table, rows, skipped=False)


def load_all(
    wh: Warehouse, extracted: list[ExtractedSource], run_id: str, force: bool = False
) -> list[LoadResult]:
    ensure_metadata_tables(wh)
    return [load_source(wh, e, run_id, force=force) for e in extracted]
