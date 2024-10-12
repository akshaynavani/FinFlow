import numpy as np
import pandas as pd
import pytest

from finflow.config import FeatureConfig
from finflow.features import build_features


def _prices(n=150, tickers=("AAA", "BBB")):
    dates = pd.bdate_range("2023-01-02", periods=n)
    rows = []
    for i, t in enumerate(tickers):
        close = 100 * (1.001 + 0.001 * i) ** np.arange(n)
        rows.append(pd.DataFrame({
            "ticker": t,
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "adj_close": close,
            "adj_high": close * 1.01,
            "adj_low": close * 0.99,
            "adj_volume": 1_000_000 + 1000 * np.arange(n),
            "dollar_volume": close * 1_000_000,
        }))
    return pd.concat(rows, ignore_index=True)


def _fundamentals():
    return pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "report_date": ["2023-02-15", "2023-05-15"],
        "net_income_ttm": [10.0, 20.0],
        "revenue_ttm": [100.0, 120.0],
        "total_equity": [50.0, 60.0],
        "shares_outstanding": [1.0, 1.0],
        "net_margin": [0.1, 0.15],
        "debt_to_equity": [0.5, 0.4],
        "roe_ttm": [0.2, 0.33],
        "revenue_growth_yoy": [None, 0.1],
    })


def test_returns_and_momentum():
    cfg = FeatureConfig(momentum_lookback=60, momentum_skip=10)
    df = build_features(_prices(), pd.DataFrame(), cfg)
    aaa = df[df.ticker == "AAA"].reset_index(drop=True)
    assert np.isnan(aaa.ret_1d[0])
    assert aaa.ret_1d[1] == pytest.approx(0.001)
    assert aaa.ret_5d[10] == pytest.approx(1.001**5 - 1)
    assert aaa["mom_60_10"][70] == pytest.approx(1.001**50 - 1)
    # Constant growth => zero volatility once the window is full.
    assert aaa.vol_21d[30] == pytest.approx(0.0, abs=1e-9)


def test_target_is_forward_looking_only():
    df = build_features(_prices(), pd.DataFrame(), FeatureConfig())
    aaa = df[df.ticker == "AAA"].reset_index(drop=True)
    assert aaa.target_fwd_ret_1d[0] == pytest.approx(aaa.ret_1d[1])
    assert np.isnan(aaa.target_fwd_ret_1d.iloc[-1])


def test_fundamentals_joined_point_in_time():
    df = build_features(_prices(), _fundamentals(), FeatureConfig())
    aaa = df[df.ticker == "AAA"].set_index("trade_date")
    # Before first report: no fundamentals (no look-ahead).
    assert np.isnan(aaa.loc["2023-02-14", "earnings_yield"])
    # Between reports: first report values.
    assert aaa.loc["2023-03-01", "net_margin"] == pytest.approx(0.1)
    # After second report.
    assert aaa.loc["2023-05-15", "net_margin"] == pytest.approx(0.15)
    close = aaa.loc["2023-05-15", "close"]
    assert aaa.loc["2023-05-15", "earnings_yield"] == pytest.approx(20.0 / close)
    # Ticker with no fundamentals stays null.
    bbb = df[df.ticker == "BBB"]
    assert bbb.earnings_yield.isna().all()


def test_cross_sectional_rank_bounds():
    cfg = FeatureConfig(momentum_lookback=60, momentum_skip=10)
    df = build_features(_prices(), pd.DataFrame(), cfg)
    ranks = df["mom_60_10_xs_rank"].dropna()
    assert ranks.between(0, 1).all()
    last = df[df.trade_date == df.trade_date.max()].set_index("ticker")
    assert last.loc["BBB", "mom_60_10_xs_rank"] > last.loc["AAA", "mom_60_10_xs_rank"]
