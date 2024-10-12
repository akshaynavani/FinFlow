import pandas as pd
import pytest

from finflow.quality import CHECKS, DataQualityError, run_check, run_suite
from finflow.warehouse import Warehouse


@pytest.fixture
def wh():
    w = Warehouse(":memory:")
    w.write_df(
        pd.DataFrame({
            "id": [1, 2, 2, 4, 5],
            "ticker": ["A", "B", "B", None, "Z"],
            "price": [10.0, -1.0, 12.0, 13.0, 1000.0],
            "kind": ["x", "y", "y", "x", "bad"],
            "d": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        }),
        "t",
    )
    w.write_df(pd.DataFrame({"ticker": ["A", "B"]}), "parent")
    yield w
    w.close()


@pytest.mark.parametrize(
    "spec, expected_failing",
    [
        ({"type": "not_null", "columns": ["ticker"]}, 1),
        ({"type": "unique", "columns": ["id"]}, 1),
        ({"type": "range", "column": "price", "min": 0, "max": 100}, 2),
        ({"type": "accepted_values", "column": "kind", "values": ["x", "y"]}, 1),
        ({"type": "row_count", "min": 10}, 1),
        ({"type": "row_count", "min": 1, "max": 10}, 0),
        ({"type": "relationships", "column": "ticker", "to_table": "parent",
          "to_column": "ticker"}, 1),
        ({"type": "expression", "condition": "price < 500"}, 1),
        ({"type": "custom_sql", "sql": "SELECT COUNT(*) FROM {table} WHERE id > 3"}, 2),
        ({"type": "freshness", "column": "d", "max_age_days": 2, "as_of": "2024-01-06"}, 0),
        ({"type": "freshness", "column": "d", "max_age_days": 2, "as_of": "2024-02-01"}, 1),
        ({"type": "null_ratio", "column": "ticker", "max_ratio": 0.5}, 0),
        ({"type": "null_ratio", "column": "ticker", "max_ratio": 0.1}, 1),
    ],
)
def test_checks(wh, spec, expected_failing):
    result = run_check(wh, "unit", "t", spec)
    assert result.failing_rows == expected_failing
    assert result.passed == (expected_failing == 0)


def test_all_registered_checks_are_covered():
    assert {"not_null", "unique", "range", "accepted_values", "row_count", "relationships",
            "expression", "custom_sql", "freshness", "null_ratio",
            "zscore_outliers"} <= set(CHECKS)


def test_missing_table_fails(wh):
    result = run_check(wh, "unit", "nope", {"type": "row_count"})
    assert not result.passed
    assert result.detail == "table missing"


def test_broken_check_is_reported_not_raised(wh):
    result = run_check(wh, "unit", "t", {"type": "expression", "condition": "no_such_col > 1"})
    assert not result.passed
    assert result.detail.startswith("error:")


def test_suite_severity_and_persistence(wh, tmp_path):
    checks = tmp_path / "checks.yaml"
    checks.write_text(
        "suites:\n"
        "  s:\n"
        "    - table: t\n"
        "      checks:\n"
        "        - {type: unique, columns: [id], severity: warn}\n"
        "        - {type: not_null, columns: [id]}\n"
        "        - {type: not_null, columns: [ticker]}\n"
    )
    with pytest.raises(DataQualityError) as err:
        run_suite(wh, "s", checks, run_id="r1")
    assert len(err.value.failures) == 1

    report = run_suite(wh, "s", checks, run_id="r2", fail_on_error=False)
    assert report.summary() == {"suite": "s", "checks": 3, "passed": 1, "errors": 1,
                                "warnings": 1}
    assert wh.scalar("SELECT COUNT(*) FROM dq_results WHERE run_id = 'r2'") == 3
