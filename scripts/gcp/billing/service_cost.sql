WITH billing_rows AS (
  SELECT
    DATE(usage_start_time, "Asia/Seoul") AS usage_date,
    service.description AS service,
    cost + IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0) AS net_cost,
    currency
  FROM
    `project-ed2d3cb0-7d1e-43ef-bb6.billing_export.gcp_billing_export_v1_011E57_170E30_1E0F8E`
  WHERE
    project.id = "project-ed2d3cb0-7d1e-43ef-bb6"
    AND DATE(usage_start_time, "Asia/Seoul") >= DATE_SUB(CURRENT_DATE("Asia/Seoul"), INTERVAL 7 DAY)
)
SELECT
  usage_date,
  service,
  ROUND(SUM(net_cost), 4) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM billing_rows
GROUP BY usage_date, service
HAVING ABS(SUM(net_cost)) >= 0.0001
ORDER BY usage_date DESC, net_cost DESC;
