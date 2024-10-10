"""Feature engineering on top of the mart layer (Pandas).

Produces ``feat_daily_features``: one row per (ticker, trade_date) with
return, volatility, trend, liquidity and point-in-time valuation features.
All features only use information available at ``trade_date`` (fundamentals
are joined as-of their *report* date, not period end).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finflow.config import FeatureConfig
from finflow.logging_utils import get_logger
from finflow.warehouse import Warehouse

logger = get_logger(__name__)

TRADING_DAYS = 252
FEATURE_TABLE = "feat_daily_features"


def add_return_features(df: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    g = df.groupby("ticker", group_keys=False)["adj_close"]
    df["log_ret_1d"] = g.transform(lambda s: np.log(s / s.shift(1)))
    for w in windows:
        df[f"ret_{w}d"] = g.transform(lambda s, w=w: s / s.shift(w) - 1)
    # Forward 1-day return as a research target (explicitly suffixed so it is
    # never mistaken for an input feature).
    df["target_fwd_ret_1d"] = g.transform(lambda s: s.shift(-1) / s - 1)
    return df


def add_volatility_features(df: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    g = df.groupby("ticker", group_keys=False)["log_ret_1d"]
    for w in windows:
        df[f"vol_{w}d"] = g.transform(
            lambda s, w=w: s.rolling(w, min_periods=w).std() * np.sqrt(TRADING_DAYS)
        )
    # Parkinson range-based volatility estimator (uses high/low).
    hl = np.log(df["adj_high"] / df["adj_low"]) ** 2
    df["parkinson_vol_21d"] = (
        hl.groupby(df["ticker"])
        .transform(lambda s: s.rolling(21, min_periods=21).mean())
        .div(4 * np.log(2))
        .pow(0.5)
        * np.sqrt(TRADING_DAYS)
    )
    return df


def add_trend_features(
    df: pd.DataFrame, ma_windows: list[int], lookback: int, skip: int
) -> pd.DataFrame:
    g = df.groupby("ticker", group_keys=False)["adj_close"]
    for w in ma_windows:
        ma = g.transform(lambda s, w=w: s.rolling(w, min_periods=w).mean())
        df[f"price_to_ma_{w}"] = df["adj_close"] / ma - 1
    # Classic "12-1" style momentum: return from t-lookback to t-skip.
    df[f"mom_{lookback}_{skip}"] = g.transform(
        lambda s: s.shift(skip) / s.shift(lookback) - 1
    )
    roll_max = g.transform(lambda s: s.rolling(TRADING_DAYS, min_periods=1).max())
    df["drawdown_252d"] = df["adj_close"] / roll_max - 1
    return df


def add_liquidity_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("ticker", group_keys=False)
    vol = g["adj_volume"]
    mean21 = vol.transform(lambda s: s.rolling(21, min_periods=21).mean())
    std21 = vol.transform(lambda s: s.rolling(21, min_periods=21).std())
    df["volume_z_21d"] = (df["adj_volume"] - mean21) / std21.replace(0, np.nan)
    df["adv_21d"] = g["dollar_volume"].transform(lambda s: s.rolling(21, min_periods=21).mean())
    # Amihud illiquidity: |return| / dollar volume, averaged over 21 days.
    illiq = df["log_ret_1d"].abs() / df["dollar_volume"].replace(0, np.nan)
    df["amihud_21d"] = illiq.groupby(df["ticker"]).transform(
        lambda s: s.rolling(21, min_periods=21).mean()
    ) * 1e6
    return df


def add_fundamental_features(df: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    """As-of join fundamentals on report_date and derive valuation ratios."""
    if fundamentals.empty:
        return df
    fund = fundamentals.copy()
    fund["report_date"] = pd.to_datetime(fund["report_date"])
    fund = fund.dropna(subset=["report_date"]).sort_values("report_date")
    cols = [
        "ticker", "report_date", "net_income_ttm", "revenue_ttm", "total_equity",
        "shares_outstanding", "net_margin", "debt_to_equity", "roe_ttm", "revenue_growth_yoy",
    ]
    df = df.sort_values("trade_date")
    merged = pd.merge_asof(
        df,
        fund[cols],
        left_on="trade_date",
        right_on="report_date",
        by="ticker",
        direction="backward",
    )
    # Unadjusted close x reported shares gives market cap as of that date.
    mcap = merged["close"] * merged["shares_outstanding"]
    merged["market_cap"] = mcap
    merged["earnings_yield"] = merged["net_income_ttm"] / mcap
    merged["sales_to_price"] = merged["revenue_ttm"] / mcap
    merged["book_to_market"] = merged["total_equity"] / mcap
    merged["days_since_report"] = (merged["trade_date"] - merged["report_date"]).dt.days
    return merged.drop(columns=["revenue_ttm", "net_income_ttm", "total_equity"])


def add_cross_sectional_ranks(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    by_date = df.groupby("trade_date")
    for col in columns:
        if col in df.columns:
            df[f"{col}_xs_rank"] = by_date[col].rank(pct=True)
    return df


def build_features(
    prices: pd.DataFrame, fundamentals: pd.DataFrame, cfg: FeatureConfig
) -> pd.DataFrame:
    if prices.empty:
        return prices
    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ticker", "trade_date"]).reset_index(drop=True)

    df = add_return_features(df, cfg.return_windows)
    df = add_volatility_features(df, cfg.volatility_windows)
    df = add_trend_features(
        df, cfg.moving_average_windows, cfg.momentum_lookback, cfg.momentum_skip
    )
    df = add_liquidity_features(df)
    df = add_fundamental_features(df, fundamentals)
    df = add_cross_sectional_ranks(
        df,
        [f"mom_{cfg.momentum_lookback}_{cfg.momentum_skip}", "earnings_yield", "book_to_market"]
        + [f"vol_{w}d" for w in cfg.volatility_windows[:1]],
    )

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.sort_values(["ticker", "trade_date"]).reset_index(drop=True)
    df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")
    if "report_date" in df.columns:
        df["report_date"] = pd.to_datetime(df["report_date"]).dt.strftime("%Y-%m-%d")
    return df


def run_feature_engineering(wh: Warehouse, cfg: FeatureConfig) -> int:
    prices = wh.read_table("mart_daily_prices")
    fundamentals = (
        wh.read_table("mart_fundamentals")
        if wh.table_exists("mart_fundamentals")
        else pd.DataFrame()
    )
    features = build_features(prices, fundamentals, cfg)
    rows = wh.write_df(features, FEATURE_TABLE, if_exists="replace")
    wh.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{FEATURE_TABLE} "
        f"ON {FEATURE_TABLE} (ticker, trade_date)"
    )
    logger.info(
        "features_built",
        extra={"table": FEATURE_TABLE, "rows": rows, "columns": len(features.columns)},
    )
    return rows
