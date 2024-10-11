import pytest

from finflow.extract import ExtractError, extract_all, normalise_column
from finflow.load import load_all


def test_normalise_column():
    assert normalise_column(" Adj Close ") == "adj_close"
    assert normalise_column("P/E Ratio") == "p_e_ratio"
    assert normalise_column("52wHigh") == "c_52whigh"


def test_extract_reads_all_sources(config, dataset):
    extracted = extract_all(config)
    names = {e.source.name for e in extracted}
    assert names == {"prices", "fundamentals", "securities", "corporate_actions"}
    prices = next(e for e in extracted if e.source.name == "prices")
    assert {"ticker", "date", "close", "volume"} <= set(prices.frame.columns)
    assert len(prices.checksum) == 64


def test_missing_source_raises(config, tmp_path):
    config.data_dir = str(tmp_path / "does-not-exist")
    with pytest.raises(ExtractError):
        extract_all(config)


def test_load_adds_lineage_and_skips_unchanged(pipeline, dataset):
    extracted = extract_all(pipeline.config)
    first = load_all(pipeline.wh, extracted, run_id="r1")
    assert all(not r.skipped for r in first)
    cols = pipeline.wh.columns("raw_prices")
    assert {"_run_id", "_source_file", "_ingested_at"} <= set(cols)

    second = load_all(pipeline.wh, extracted, run_id="r2")
    assert all(r.skipped for r in second)
    assert pipeline.wh.scalar("SELECT COUNT(*) FROM etl_load_audit WHERE status='skipped'") == 4

    forced = load_all(pipeline.wh, extracted, run_id="r3", force=True)
    assert all(not r.skipped for r in forced)
