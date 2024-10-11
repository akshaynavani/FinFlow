"""Pipeline orchestration: extract -> load -> check(raw) -> transform ->
check(staging) -> features -> check(features).

Each step is timed and logged via ``log_step``; the run's status is recorded
in ``etl_runs``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from finflow.config import PipelineConfig
from finflow.extract import extract_all
from finflow.features import run_feature_engineering
from finflow.load import ensure_metadata_tables, load_all
from finflow.logging_utils import get_logger, log_step, new_run_id, run_context
from finflow.quality import SuiteReport, run_suite
from finflow.transform import run_models
from finflow.warehouse import Warehouse

logger = get_logger(__name__)

STEPS = ("extract_load", "check_raw", "transform", "check_staging", "features", "check_features")


@dataclass
class RunSummary:
    run_id: str
    status: str = "running"
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    quality: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class Pipeline:
    def __init__(self, config: PipelineConfig, warehouse: Warehouse | None = None) -> None:
        self.config = config
        self.wh = warehouse or Warehouse(config.warehouse_path)

    # -- individual steps ------------------------------------------------
    def extract_load(self, run_id: str, force: bool = False) -> dict[str, Any]:
        extracted = extract_all(self.config)
        results = load_all(self.wh, extracted, run_id, force=force)
        return {
            "sources": len(results),
            "rows_loaded": sum(r.rows for r in results if not r.skipped),
            "skipped": [r.source for r in results if r.skipped],
        }

    def check(self, suite: str, run_id: str | None = None) -> SuiteReport:
        return run_suite(
            self.wh,
            suite,
            self.config.checks_path,
            run_id=run_id,
            fail_on_error=self.config.fail_on_error,
        )

    def transform(self, select: list[str] | None = None) -> dict[str, Any]:
        results = run_models(self.wh, select=select)
        return {"models": len(results), "tables": {t: n for r in results for t, n in r.row_counts.items()}}

    def features(self) -> dict[str, Any]:
        return {"rows": run_feature_engineering(self.wh, self.config.features)}

    # -- full run --------------------------------------------------------
    def _record_run(self, summary: RunSummary, started_at: str) -> None:
        ensure_metadata_tables(self.wh)
        self.wh.execute(
            "INSERT OR REPLACE INTO etl_runs VALUES (?,?,?,?,?)",
            (
                summary.run_id,
                started_at,
                datetime.now(timezone.utc).isoformat(),
                summary.status,
                summary.error,
            ),
        )

    def run(
        self,
        force: bool = False,
        skip_checks: bool = False,
        run_id: str | None = None,
    ) -> RunSummary:
        run_id = run_id or new_run_id()
        summary = RunSummary(run_id=run_id)
        started_at = datetime.now(timezone.utc).isoformat()

        with run_context(run_id=run_id):
            logger.info("pipeline_started", extra={"warehouse": self.config.warehouse_path})
            try:
                for step in STEPS:
                    if skip_checks and step.startswith("check_"):
                        continue
                    with log_step(logger, step) as metrics:
                        if step == "extract_load":
                            metrics.update(self.extract_load(run_id, force=force))
                        elif step.startswith("check_"):
                            report = self.check(step.removeprefix("check_"), run_id=run_id)
                            metrics.update(report.summary())
                            summary.quality.append(report.summary())
                        elif step == "transform":
                            metrics.update(self.transform())
                        elif step == "features":
                            metrics.update(self.features())
                    summary.steps[step] = dict(metrics)
                summary.status = "success"
            except Exception as exc:
                summary.status = "failed"
                summary.error = str(exc)
                logger.error("pipeline_failed", extra={"error": str(exc)})
                raise
            finally:
                self._record_run(summary, started_at)
                logger.info("pipeline_finished", extra={"status": summary.status})
        return summary

    def close(self) -> None:
        self.wh.close()
