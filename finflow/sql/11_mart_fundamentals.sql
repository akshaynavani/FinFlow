-- Mart: fundamentals with trailing-twelve-month aggregates and ratios.
-- Rows are keyed on report_date so they can be joined point-in-time
-- (as-of) against prices without look-ahead bias.
DROP TABLE IF EXISTS mart_fundamentals;
CREATE TABLE mart_fundamentals AS
WITH ttm AS (
    SELECT
        f.*,
        SUM(revenue) OVER w    AS revenue_ttm,
        SUM(net_income) OVER w AS net_income_ttm,
        COUNT(*) OVER w        AS quarters_in_window,
        LAG(revenue, 4) OVER (PARTITION BY ticker ORDER BY period_end) AS revenue_yoy_base
    FROM stg_fundamentals f
    WINDOW w AS (PARTITION BY ticker ORDER BY period_end ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
)
SELECT
    ticker,
    period_end,
    report_date,
    revenue,
    net_income,
    total_equity,
    total_debt,
    shares_outstanding,
    CASE WHEN quarters_in_window = 4 THEN revenue_ttm END    AS revenue_ttm,
    CASE WHEN quarters_in_window = 4 THEN net_income_ttm END AS net_income_ttm,
    CASE WHEN revenue <> 0 THEN net_income / revenue END     AS net_margin,
    CASE WHEN total_equity <> 0 THEN total_debt / total_equity END AS debt_to_equity,
    CASE WHEN quarters_in_window = 4 AND total_equity <> 0
         THEN net_income_ttm / total_equity END               AS roe_ttm,
    CASE WHEN revenue_yoy_base IS NOT NULL AND revenue_yoy_base <> 0
         THEN revenue / revenue_yoy_base - 1 END              AS revenue_growth_yoy
FROM ttm
ORDER BY ticker, report_date;
