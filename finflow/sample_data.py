"""Deterministic synthetic financial dataset generator.

Produces realistic-looking raw files so the pipeline can run fully offline:

* ``securities.json``        – security master (ticker, name, sector, exchange)
* ``prices.csv``             – daily OHLCV bars (geometric Brownian motion)
* ``fundamentals.csv``       – quarterly fundamentals with reporting lag
* ``corporate_actions.csv``  – stock splits and cash dividends

Optionally injects a handful of data defects (duplicates, nulls, negative
prices) so the data quality layer has something to catch.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SECURITIES = [
    ("AAPX", "Apex Devices Inc", "Technology", "NASDAQ"),
    ("BNKR", "Banker Financial Corp", "Financials", "NYSE"),
    ("CRBN", "Carbon Energy Ltd", "Energy", "NYSE"),
    ("DOCS", "Doctor Health Group", "Healthcare", "NYSE"),
    ("EDGE", "Edge Cloud Systems", "Technology", "NASDAQ"),
    ("FOOD", "Fresh Foods Holdings", "Consumer Staples", "NYSE"),
    ("GRID", "Grid Utilities Co", "Utilities", "NYSE"),
    ("HAUL", "Haul Logistics Inc", "Industrials", "NASDAQ"),
]


def _business_days(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=periods)


def generate_prices(
    rng: np.random.Generator, dates: pd.DatetimeIndex, splits: pd.DataFrame
) -> pd.DataFrame:
    frames = []
    for ticker, *_ in SECURITIES:
        mu = rng.uniform(0.02, 0.15) / 252
        sigma = rng.uniform(0.15, 0.45) / np.sqrt(252)
        start_price = rng.uniform(20, 300)
        log_ret = rng.normal(mu - 0.5 * sigma**2, sigma, len(dates))
        close = start_price * np.exp(np.cumsum(log_ret))

        # Apply splits to the *unadjusted* raw series: price drops by the ratio
        # on and after the ex-date.
        for _, split in splits[splits["ticker"] == ticker].iterrows():
            close = np.where(dates >= split["ex_date"], close / split["ratio"], close)

        intraday = np.abs(rng.normal(0, sigma, len(dates)))
        open_ = close * (1 + rng.normal(0, sigma / 2, len(dates)))
        high = np.maximum(open_, close) * (1 + intraday)
        low = np.minimum(open_, close) * (1 - intraday)
        volume = rng.lognormal(mean=14, sigma=0.5, size=len(dates)).astype(int)
        frames.append(
            pd.DataFrame(
                {
                    "date": dates.strftime("%Y-%m-%d"),
                    "ticker": ticker,
                    "open": open_.round(4),
                    "high": high.round(4),
                    "low": low.round(4),
                    "close": close.round(4),
                    "volume": volume,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def generate_fundamentals(rng: np.random.Generator, dates: pd.DatetimeIndex) -> pd.DataFrame:
    quarter_ends = pd.date_range(dates.min(), dates.max(), freq="QE")
    rows = []
    for ticker, *_ in SECURITIES:
        revenue = rng.uniform(500, 20000)
        shares = rng.uniform(100, 3000)
        for qe in quarter_ends:
            revenue *= 1 + rng.normal(0.02, 0.05)
            margin = rng.uniform(0.05, 0.30)
            net_income = revenue * margin
            equity = revenue * rng.uniform(0.8, 2.5)
            debt = equity * rng.uniform(0.1, 1.5)
            rows.append(
                {
                    "ticker": ticker,
                    "period_end": qe.strftime("%Y-%m-%d"),
                    "report_date": (qe + pd.Timedelta(days=int(rng.integers(25, 50)))).strftime(
                        "%Y-%m-%d"
                    ),
                    "revenue": round(revenue, 2),
                    "net_income": round(net_income, 2),
                    "total_equity": round(equity, 2),
                    "total_debt": round(debt, 2),
                    "shares_outstanding": round(shares, 2),
                    "currency": "USD",
                }
            )
    return pd.DataFrame(rows)


def generate_corporate_actions(
    rng: np.random.Generator, dates: pd.DatetimeIndex
) -> pd.DataFrame:
    rows = []
    for i, (ticker, *_) in enumerate(SECURITIES):
        if i % 3 == 0:
            ex_date = dates[int(rng.integers(len(dates) // 4, 3 * len(dates) // 4))]
            rows.append(
                {
                    "ticker": ticker,
                    "ex_date": ex_date.strftime("%Y-%m-%d"),
                    "action_type": "split",
                    "ratio": float(rng.choice([2, 3, 4])),
                    "amount": None,
                }
            )
        for qe in pd.date_range(dates.min(), dates.max(), freq="QE")[1:]:
            if i % 2 == 0:
                ex = dates[dates.searchsorted(qe + pd.Timedelta(days=10)) - 1]
                rows.append(
                    {
                        "ticker": ticker,
                        "ex_date": ex.strftime("%Y-%m-%d"),
                        "action_type": "dividend",
                        "ratio": None,
                        "amount": round(float(rng.uniform(0.1, 1.2)), 2),
                    }
                )
    return pd.DataFrame(rows)


def inject_defects(rng: np.random.Generator, prices: pd.DataFrame) -> pd.DataFrame:
    """Add a small number of realistic data issues to raw prices."""
    prices = prices.copy()
    dup_idx = rng.choice(prices.index, size=5, replace=False)
    prices = pd.concat([prices, prices.loc[dup_idx]], ignore_index=True)
    null_idx = rng.choice(prices.index, size=3, replace=False)
    prices.loc[null_idx, "volume"] = np.nan
    neg_idx = rng.choice(prices.index, size=2, replace=False)
    prices.loc[neg_idx, "close"] = -prices.loc[neg_idx, "close"]
    return prices


def generate_dataset(
    output_dir: str | Path,
    start: str = "2022-01-03",
    days: int = 504,
    seed: int = 42,
    with_defects: bool = False,
) -> dict[str, Path]:
    """Write the synthetic dataset to ``output_dir`` and return file paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    dates = _business_days(start, days)

    actions = generate_corporate_actions(rng, dates)
    splits = actions[actions["action_type"] == "split"].assign(
        ex_date=lambda d: pd.to_datetime(d["ex_date"])
    )
    prices = generate_prices(rng, dates, splits)
    if with_defects:
        prices = inject_defects(rng, prices)
    fundamentals = generate_fundamentals(rng, dates)

    securities = [
        {
            "ticker": t,
            "name": n,
            "sector": s,
            "exchange": e,
            "listed_since": "2010-01-01",
        }
        for t, n, s, e in SECURITIES
    ]

    paths = {
        "prices": out / "prices.csv",
        "fundamentals": out / "fundamentals.csv",
        "securities": out / "securities.json",
        "corporate_actions": out / "corporate_actions.csv",
    }
    prices.to_csv(paths["prices"], index=False)
    fundamentals.to_csv(paths["fundamentals"], index=False)
    actions.to_csv(paths["corporate_actions"], index=False)
    paths["securities"].write_text(json.dumps(securities, indent=2))
    return paths
