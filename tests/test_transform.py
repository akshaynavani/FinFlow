import pandas as pd

from finflow.extract import extract_all
from finflow.load import load_all
from finflow.transform import created_tables, discover_models, run_models


def _load(pipeline):
    load_all(pipeline.wh, extract_all(pipeline.config), run_id="test")


def test_models_are_discovered_in_dependency_order():
    names = [p.stem for p in discover_models()]
    assert names == sorted(names)
    assert names.index("02_stg_prices") < names.index("10_mart_daily_prices")


def test_created_tables_ignores_temp_tables():
    sql = "CREATE TEMP TABLE t AS SELECT 1; CREATE TABLE real_one AS SELECT 1;"
    assert created_tables(sql) == ["real_one"]


def test_staging_dedups_and_rejects_bad_rows(pipeline, dirty_dataset):
    _load(pipeline)
    run_models(pipeline.wh)
    wh = pipeline.wh
    dupes = wh.scalar(
        "SELECT COUNT(*) FROM (SELECT ticker, trade_date FROM stg_prices "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    )
    assert dupes == 0
    assert wh.scalar("SELECT COUNT(*) FROM stg_prices WHERE close <= 0") == 0
    assert wh.scalar(
        "SELECT COUNT(*) FROM stg_prices_rejected WHERE reject_reason = 'non_positive_close'"
    ) >= 1


def test_split_adjustment_removes_price_gaps(pipeline, dataset):
    _load(pipeline)
    run_models(pipeline.wh)
    wh = pipeline.wh
    splits = wh.read_df("SELECT ticker, ex_date, ratio FROM stg_corporate_actions "
                        "WHERE action_type = 'split'")
    assert not splits.empty

    prices = wh.read_df("SELECT ticker, trade_date, close, adj_close, split_factor "
                        "FROM mart_daily_prices ORDER BY ticker, trade_date")
    for _, split in splits.iterrows():
        series = prices[prices.ticker == split.ticker].reset_index(drop=True)
        idx = series.index[series.trade_date == split.ex_date][0]
        raw_jump = series.close[idx] / series.close[idx - 1]
        adj_jump = series.adj_close[idx] / series.adj_close[idx - 1]
        assert raw_jump < 0.6  # visible split gap in raw data
        assert 0.8 < adj_jump < 1.25  # gap removed after adjustment
        assert series.split_factor[idx - 1] == split.ratio
        assert series.split_factor[idx] == 1.0


def test_mart_fundamentals_ttm(pipeline, dataset):
    _load(pipeline)
    run_models(pipeline.wh)
    df = pipeline.wh.read_df("SELECT * FROM mart_fundamentals ORDER BY ticker, period_end")
    first = df[df.ticker == df.ticker.iloc[0]].reset_index(drop=True)
    assert pd.isna(first.revenue_ttm[0])  # needs 4 quarters
    expected = first.revenue[0:4].sum()
    assert abs(first.revenue_ttm[3] - expected) < 1e-6
