WITH billing_rows AS (
  SELECT
    DATE(usage_start_time, "Asia/Seoul") AS usage_date,
    cost,
    IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0) AS credits,
    currency
  FROM
    `project-ed2d3cb0-7d1e-43ef-bb6.billing_export.gcp_billing_export_v1_011E57_170E30_1E0F8E`
  WHERE
    project.id = "project-ed2d3cb0-7d1e-43ef-bb6"
    AND DATE(usage_start_time, "Asia/Seoul") >= DATE_SUB(CURRENT_DATE("Asia/Seoul"), INTERVAL 31 DAY)
)
SELECT
  usage_date,
  ROUND(SUM(cost), 4) AS gross_cost,
  ROUND(SUM(credits), 4) AS credits,
  ROUND(SUM(cost + credits), 4) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM billing_rows
GROUP BY usage_date
ORDER BY usage_date DESC;
