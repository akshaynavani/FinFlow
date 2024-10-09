-- Mart: split-adjusted daily prices joined with the security master.
--
-- The cumulative split factor for a bar is the product of all split ratios
-- with ex_date strictly after the bar's date. SQLite has no PRODUCT()
-- aggregate, so we use exp(sum(ln(ratio))) via the ff_ln / ff_exp functions
-- registered by the transform runner.
DROP TABLE IF EXISTS mart_daily_prices;
CREATE TABLE mart_daily_prices AS
WITH split_factor AS (
    SELECT
        p.ticker,
        p.trade_date,
        COALESCE(ff_exp(SUM(ff_ln(s.ratio))), 1.0) AS split_factor
    FROM stg_prices p
    LEFT JOIN stg_corporate_actions s
        ON s.ticker = p.ticker
       AND s.action_type = 'split'
       AND s.ex_date > p.trade_date
    GROUP BY p.ticker, p.trade_date
),
dividends AS (
    SELECT ticker, ex_date, SUM(amount) AS dividend
    FROM stg_corporate_actions
    WHERE action_type = 'dividend'
    GROUP BY ticker, ex_date
)
SELECT
    p.ticker,
    p.trade_date,
    sec.sector,
    sec.exchange,
    p.open,
    p.high,
    p.low,
    p.close,
    p.volume,
    sf.split_factor,
    p.open  / sf.split_factor               AS adj_open,
    p.high  / sf.split_factor               AS adj_high,
    p.low   / sf.split_factor               AS adj_low,
    p.close / sf.split_factor               AS adj_close,
    CAST(p.volume * sf.split_factor AS INTEGER) AS adj_volume,
    COALESCE(d.dividend, 0.0) / sf.split_factor AS adj_dividend,
    p.close * p.volume                      AS dollar_volume
FROM stg_prices p
JOIN split_factor sf
    ON sf.ticker = p.ticker AND sf.trade_date = p.trade_date
LEFT JOIN stg_securities sec
    ON sec.ticker = p.ticker
LEFT JOIN dividends d
    ON d.ticker = p.ticker AND d.ex_date = p.trade_date
ORDER BY p.ticker, p.trade_date;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mart_daily_prices ON mart_daily_prices (ticker, trade_date);
