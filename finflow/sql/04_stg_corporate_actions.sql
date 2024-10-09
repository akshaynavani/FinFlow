-- Staging: corporate actions (splits and cash dividends).
DROP TABLE IF EXISTS stg_corporate_actions;
CREATE TABLE stg_corporate_actions AS
SELECT DISTINCT
    UPPER(TRIM(ticker))           AS ticker,
    DATE(ex_date)                 AS ex_date,
    LOWER(TRIM(action_type))      AS action_type,
    CAST(ratio AS REAL)           AS ratio,
    CAST(amount AS REAL)          AS amount
FROM raw_corporate_actions
WHERE ticker IS NOT NULL
  AND ex_date IS NOT NULL
  AND LOWER(TRIM(action_type)) IN ('split', 'dividend');
