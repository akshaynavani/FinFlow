"""Run YAML-declared data quality suites and persist results.

Example ``checks.yaml``::

    suites:
      raw:
        - table: raw_prices
          checks:
            - type: not_null
              columns: [ticker, date]
            - type: range
              column: close
              min: 0
              severity: warn
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from finflow.logging_utils import get_logger
from finflow.quality.checks import CHECKS
from finflow.warehouse import Warehouse

logger = get_logger(__name__)

SEVERITIES = ("error", "warn")

RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS dq_results (
    run_id        TEXT,
    suite         TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    check_name    TEXT NOT NULL,
    check_type    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    passed        INTEGER NOT NULL,
    failing_rows  INTEGER NOT NULL,
    detail        TEXT,
    checked_at    TEXT NOT NULL
);
"""


class DataQualityError(RuntimeError):
    def __init__(self, failures: list[CheckResult]) -> None:
        self.failures = failures
        names = ", ".join(f"{f.table}.{f.name}" for f in failures)
        super().__init__(f"{len(failures)} blocking data quality check(s) failed: {names}")


@dataclass
class CheckResult:
    suite: str
    table: str
    name: str
    check_type: str
    severity: str
    passed: bool
    failing_rows: int
    detail: str


@dataclass
class SuiteReport:
    suite: str
    results: list[CheckResult]

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def summary(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "checks": len(self.results),
            "passed": sum(r.passed for r in self.results),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


def load_suites(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    with Path(path).open() as fh:
        raw = yaml.safe_load(fh) or {}
    return raw.get("suites", {})


def _check_name(spec: dict[str, Any]) -> str:
    if "name" in spec:
        return spec["name"]
    target = spec.get("column") or spec.get("columns") or ""
    if isinstance(target, list):
        target = "_".join(target)
    return f"{spec['type']}_{target}".rstrip("_")


def run_check(wh: Warehouse, suite: str, table: str, spec: dict[str, Any]) -> CheckResult:
    spec = dict(spec)
    check_type = spec.pop("type")
    severity = spec.pop("severity", "error")
    name = spec.pop("name", None) or _check_name({"type": check_type, **spec})
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown severity {severity!r} for {table}.{name}")
    fn = CHECKS.get(check_type)
    if fn is None:
        raise ValueError(f"Unknown check type {check_type!r}")

    if not wh.table_exists(table):
        return CheckResult(suite, table, name, check_type, severity, False, 0, "table missing")
    try:
        failing, detail = fn(wh, table, **spec)
    except Exception as exc:  # a broken check counts as a failed check
        logger.exception("check_crashed", extra={"table": table, "check": name})
        return CheckResult(suite, table, name, check_type, severity, False, 0, f"error: {exc}")
    return CheckResult(suite, table, name, check_type, severity, failing == 0, failing, detail)


def persist_results(wh: Warehouse, results: list[CheckResult], run_id: str | None) -> None:
    wh.executescript(RESULTS_DDL)
    now = datetime.now(timezone.utc).isoformat()
    with wh.transaction():
        wh.conn.executemany(
            "INSERT INTO dq_results VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (run_id, r.suite, r.table, r.name, r.check_type, r.severity,
                 int(r.passed), r.failing_rows, r.detail, now)
                for r in results
            ],
        )


def run_suite(
    wh: Warehouse,
    suite: str,
    checks_path: str | Path,
    run_id: str | None = None,
    fail_on_error: bool = True,
) -> SuiteReport:
    suites = load_suites(checks_path)
    if suite not in suites:
        raise KeyError(f"Suite {suite!r} not defined in {checks_path}")

    results: list[CheckResult] = []
    for table_block in suites[suite]:
        table = table_block["table"]
        for spec in table_block.get("checks", []):
            result = run_check(wh, suite, table, spec)
            level = "info" if result.passed else ("error" if result.severity == "error" else "warning")
            getattr(logger, level)(
                "dq_check", extra={k: v for k, v in asdict(result).items() if k != "suite"}
            )
            results.append(result)

    persist_results(wh, results, run_id)
    report = SuiteReport(suite, results)
    logger.info("dq_suite_finished", extra=report.summary())
    if fail_on_error and report.errors:
        raise DataQualityError(report.errors)
    return report
