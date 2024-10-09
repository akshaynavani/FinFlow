-- Staging: security master, typed and deduplicated on ticker.
DROP TABLE IF EXISTS stg_securities;
CREATE TABLE stg_securities AS
WITH ranked AS (
    SELECT
        UPPER(TRIM(ticker))            AS ticker,
        TRIM(name)                     AS name,
        COALESCE(TRIM(sector), 'Unknown') AS sector,
        UPPER(TRIM(exchange))          AS exchange,
        DATE(listed_since)             AS listed_since,
        ROW_NUMBER() OVER (
            PARTITION BY UPPER(TRIM(ticker)) ORDER BY _ingested_at DESC, rowid DESC
        ) AS rn
    FROM raw_securities
    WHERE ticker IS NOT NULL AND TRIM(ticker) <> ''
)
SELECT ticker, name, sector, exchange, listed_since
FROM ranked
WHERE rn = 1;
