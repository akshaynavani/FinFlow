# FinFlow — Implementation Plan

**FinFlow** is a Python ELT research pipeline that ingests structured financial datasets
(daily OHLCV prices, company fundamentals, corporate actions), loads them raw into a SQL
warehouse, transforms them with SQL + Pandas, validates them with automated data quality
checks, and produces engineered features for research. Structured (JSON) logging and GitHub
Actions CI support iterative research workflows.

## Key design decisions

- **Warehouse:** SQLite via the standard library (`sqlite3`) — zero infrastructure, file-based,
  supports window functions (SQLite >= 3.25). The warehouse layer is isolated behind a small
  `Warehouse` class so it could be swapped for Postgres/DuckDB.
- **ELT, not ETL:** raw files are loaded as-is into `raw_*` tables (with ingestion metadata),
  then SQL models build `stg_*` (typed, deduplicated) and `mart_*` (joined/aggregated) tables.
- **SQL models** live as `.sql` files under `finflow/sql/`, executed in dependency order.
- **Feature engineering** in Pandas (returns, rolling volatility, momentum, moving-average
  ratios, valuation ratios) written back to `feat_daily_features`.
- **Data quality**: declarative checks (not-null, unique, range, accepted values, freshness,
  row-count, referential integrity, custom SQL) defined in YAML, run after load and after
  transform; failures at `error` severity halt the pipeline.
- **Structured logging:** JSON lines via stdlib `logging` with a custom formatter and a
  per-run `run_id` context.
- **Sample data:** a deterministic synthetic data generator so the pipeline runs offline
  without credentials.
- **CLI:** `finflow` (argparse) with `generate`, `run`, `extract`, `transform`, `check`,
  `features` subcommands.
- **CI:** GitHub Actions — ruff lint, pytest on a Python version matrix, and a smoke run of
  the full pipeline on generated data.

## Phases

### Phase 1 — Project scaffold, config, logging, sample data
Deliverables: package layout, `pyproject.toml`, YAML config loader, JSON structured logger
with run context, deterministic synthetic financial dataset generator.
Files: `pyproject.toml`, `finflow/__init__.py`, `finflow/config.py`, `finflow/logging_utils.py`,
`finflow/sample_data.py`, `config/pipeline.yaml`, `.env.example`.

### Phase 2 — Extract & Load (raw layer)
Deliverables: source readers for CSV/JSON (with schema hints), `Warehouse` wrapper over
SQLite, raw loader that adds `_ingested_at`, `_source_file`, `_run_id` metadata, run-audit
table.
Files: `finflow/warehouse.py`, `finflow/extract.py`, `finflow/load.py`.

### Phase 3 — SQL transforms + Pandas feature engineering
Deliverables: SQL model runner with ordered models; staging models (typing, dedup, split
adjustment), mart models (adjusted prices, latest fundamentals); feature engineering module.
Files: `finflow/transform.py`, `finflow/sql/*.sql`, `finflow/features.py`.

### Phase 4 — Data quality framework
Deliverables: check registry and implementations, YAML-driven check suites, results
persisted to `dq_results`, severity handling and a `DataQualityError`.
Files: `finflow/quality/__init__.py`, `finflow/quality/checks.py`, `finflow/quality/runner.py`,
`config/checks.yaml`.

### Phase 5 — Orchestration, CLI, tests, CI
Deliverables: pipeline orchestrator with step timing, CLI entry point, pytest suite
(extract/load, transforms, features, quality checks, end-to-end), GitHub Actions workflow.
Files: `finflow/pipeline.py`, `finflow/cli.py`, `tests/*`, `.github/workflows/ci.yml`.

After phases: `.gitignore`, `README.md` (architecture diagram, run/test instructions).
