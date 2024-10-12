import json

from finflow.cli import main
from finflow.pipeline import Pipeline
from tests.conftest import make_config


def test_end_to_end_run(pipeline, dataset):
    summary = pipeline.run()
    assert summary.status == "success"
    assert [q["suite"] for q in summary.quality] == ["raw", "staging", "features"]
    assert all(q["errors"] == 0 for q in summary.quality)

    wh = pipeline.wh
    for table in ("stg_prices", "mart_daily_prices", "mart_fundamentals", "feat_daily_features"):
        assert wh.row_count(table) > 0
    assert wh.scalar("SELECT status FROM etl_runs WHERE run_id = ?", (summary.run_id,)) == "success"
    assert wh.row_count("feat_daily_features") == wh.row_count("mart_daily_prices")


def test_dirty_data_is_cleaned_and_warned(pipeline, dirty_dataset):
    summary = pipeline.run()
    assert summary.status == "success"
    raw = summary.quality[0]
    assert raw["warnings"] >= 2  # duplicates + negative prices (+ null volume)
    assert raw["errors"] == 0


def test_rerun_skips_unchanged_sources(pipeline, dataset):
    pipeline.run()
    second = pipeline.run()
    assert sorted(second.steps["extract_load"]["skipped"]) == [
        "corporate_actions", "fundamentals", "prices", "securities"
    ]


def test_failed_run_is_recorded(tmp_path, dataset):
    cfg = make_config(tmp_path)
    (tmp_path / "raw" / "prices.csv").unlink()
    p = Pipeline(cfg)
    try:
        try:
            p.run(run_id="broken")
        except Exception:
            pass
        assert p.wh.scalar("SELECT status FROM etl_runs WHERE run_id='broken'") == "failed"
    finally:
        p.close()


def test_cli_generate_and_run(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text((root / "config" / "pipeline.yaml").read_text())
    monkeypatch.setenv("FINFLOW_WAREHOUSE_PATH", str(tmp_path / "wh.db"))
    monkeypatch.setenv("FINFLOW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("FINFLOW_CHECKS_PATH", str(root / "config" / "checks.yaml"))
    monkeypatch.setenv("FINFLOW_LOG_LEVEL", "WARNING")

    assert main(["-c", str(cfg), "generate", "--days", "260"]) == 0
    capsys.readouterr()
    assert main(["-c", str(cfg), "run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
