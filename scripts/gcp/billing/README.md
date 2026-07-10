# GCP billing queries

Cloud Billing exports Standard usage costs, Detailed usage costs, and account
pricing to the `billing_export` BigQuery dataset. The queries use `Asia/Seoul`
dates and include credits in net cost calculations.

Run a query from the repository root:

```bash
bq query \
  --project_id=project-ed2d3cb0-7d1e-43ef-bb6 \
  --location=US \
  --use_legacy_sql=false \
  < scripts/gcp/billing/daily_cost.sql
```

Available queries:

- `daily_cost.sql`: daily gross cost, credits, and net cost for the last 31 days
- `service_cost.sql`: net cost grouped by service for the last 7 days
- `resource_cost.sql`: resource-level net cost for the last 7 days

The export tables are created by Google after Standard usage cost, Detailed
usage cost, and Pricing exports are enabled for billing account
`011E57-170E30-1E0F8E`. Pricing data is written to `cloud_pricing_export`.
