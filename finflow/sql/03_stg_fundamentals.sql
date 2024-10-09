-- Staging: quarterly fundamentals, one row per (ticker, period_end) keeping
-- the most recently reported restatement.
DROP TABLE IF EXISTS stg_fundamentals;
CREATE TABLE stg_fundamentals AS
WITH ranked AS (
    SELECT
        UPPER(TRIM(ticker))                 AS ticker,
        DATE(period_end)                    AS period_end,
        DATE(report_date)                   AS report_date,
        CAST(revenue AS REAL)               AS revenue,
        CAST(net_income AS REAL)            AS net_income,
        CAST(total_equity AS REAL)          AS total_equity,
        CAST(total_debt AS REAL)            AS total_debt,
        CAST(shares_outstanding AS REAL)    AS shares_outstanding,
        UPPER(COALESCE(currency, 'USD'))    AS currency,
        ROW_NUMBER() OVER (
            PARTITION BY UPPER(TRIM(ticker)), DATE(period_end)
            ORDER BY DATE(report_date) DESC, _ingested_at DESC
        ) AS rn
    FROM raw_fundamentals
    WHERE ticker IS NOT NULL AND period_end IS NOT NULL
)
SELECT ticker, period_end, report_date, revenue, net_income, total_equity,
       total_debt, shares_outstanding, currency
FROM ranked
WHERE rn = 1;
