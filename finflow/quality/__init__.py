"""Declarative data quality checks for FinFlow."""

from finflow.quality.checks import CHECKS, register
from finflow.quality.runner import (
    CheckResult,
    DataQualityError,
    SuiteReport,
    run_check,
    run_suite,
)

__all__ = [
    "CHECKS",
    "CheckResult",
    "DataQualityError",
    "SuiteReport",
    "register",
    "run_check",
    "run_suite",
]
