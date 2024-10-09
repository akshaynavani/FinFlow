-- Staging: daily bars. Types columns, removes duplicate (ticker, date) rows
-- and drops bars that are physically impossible (non-positive prices,
-- high < low). Rejected rows are kept in stg_prices_rejected for auditing.
DROP TABLE IF EXISTS stg_prices_typed;
CREATE TEMP TABLE stg_prices_typed AS
SELECT
    UPPER(TRIM(ticker))        AS ticker,
    DATE(date)                 AS trade_date,
    CAST(open   AS REAL)       AS open,
    CAST(high   AS REAL)       AS high,
    CAST(low    AS REAL)       AS low,
    CAST(close  AS REAL)       AS close,
    CAST(volume AS INTEGER)    AS volume,
    _ingested_at,
    rowid                      AS _raw_rowid
FROM raw_prices;

DROP TABLE IF EXISTS stg_prices_rejected;
CREATE TABLE stg_prices_rejected AS
SELECT
    ticker, trade_date, open, high, low, close, volume,
    CASE
        WHEN trade_date IS NULL                 THEN 'invalid_date'
        WHEN close IS NULL OR close <= 0        THEN 'non_positive_close'
        WHEN open  IS NULL OR open  <= 0        THEN 'non_positive_open'
        WHEN high < low                         THEN 'high_below_low'
    END AS reject_reason
FROM stg_prices_typed
WHERE trade_date IS NULL
   OR close IS NULL OR close <= 0
   OR open  IS NULL OR open  <= 0
   OR high < low;

DROP TABLE IF EXISTS stg_prices;
CREATE TABLE stg_prices AS
WITH valid AS (
    SELECT *
    FROM stg_prices_typed
    WHERE trade_date IS NOT NULL
      AND close > 0 AND open > 0
      AND high >= low
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY ticker, trade_date ORDER BY _ingested_at DESC, _raw_rowid DESC
        ) AS rn
    FROM valid
)
SELECT ticker, trade_date, open, high, low, close, volume
FROM ranked
WHERE rn = 1
ORDER BY ticker, trade_date;

CREATE UNIQUE INDEX IF NOT EXISTS ux_stg_prices ON stg_prices (ticker, trade_date);
DROP TABLE IF EXISTS stg_prices_typed;
