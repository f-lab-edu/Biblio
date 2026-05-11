# Feedback Ingestion Pipeline

Feedback Ingestion Pipeline runs Vector as an ingestion component for validated feedback events.

## Runtime Profiles

- Production/staging uses Vector `http_server` as the source and GCS as the sink.
- Local/CI uses a fixture file source and local file sinks.
- Common transforms stay in `config/common/transforms.yaml` so source and sink adapters can change without changing routing logic.
- Optional observability stays in `config/common/observability.yaml` and exposes sanitized result logs plus Vector internal metrics when that profile is included.

## Required Production Env

- `GCP_PROJECT_ID`
- `FIP_HTTP_ADDRESS`
- `FIP_HTTP_PATH`
- `GCS_FEEDBACK_LOG_BUCKET_NAME`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `FIP_SINK_BATCH_MAX_EVENTS`
- `FIP_SINK_FLUSH_TIMEOUT_SEC`
- `FIP_SINK_TIMEOUT_SEC`
- `FIP_RETRY_MAX_ATTEMPTS`
- `FIP_RETRY_INITIAL_BACKOFF_SEC`
- `FIP_RETRY_MAX_BACKOFF_SEC`
- `FIP_DISK_BUFFER_MAX_SIZE_MB`

## Validate Configs

```bash
./scripts/validate_configs.sh
```

The script validates both production and test profiles. It sets harmless placeholder env values and disables Vector environment checks, so it does not require real GCP credentials.

## Run Local Fixture Profile

```bash
vector \
  --config config/common/transforms.yaml \
  --config config/test/source_fixture.yaml \
  --config config/test/sinks_local.yaml
```

Local output defaults to `/tmp/biblio-fip-output/raw-events.jsonl` and `/tmp/biblio-fip-output/error-events.jsonl`.

For the scripted fixture smoke:

```bash
./scripts/smoke_fixture_local.sh
```

For an HTTP ingress smoke that seeds one valid event and one malformed event:

```bash
./scripts/smoke_http_local.sh
```

This HTTP smoke includes `config/common/observability.yaml`, checks sanitized `raw_log_ready` and `error_log_ready` logs, and requires binding local HTTP and metrics ports.

## Run Production Profile

```bash
vector \
  --config config/common/transforms.yaml \
  --config config/production/source_http.yaml \
  --config config/production/sinks_gcs.yaml
```

Production exposes the Vector HTTP endpoint only on the internal network and requires feedback log bucket write permission.

Raw logs are written under `feedback/raw_logs/schema_version=<version>/ingest_date=<YYYY-MM-DD>/hour=<HH>/`.
Error logs are written under `feedback/error_logs/schema_version=<version-or-unknown>/ingest_date=<YYYY-MM-DD>/hour=<HH>/`.
Object names include the Vector batch timestamp and UUID suffix so repeated delivery of the same `event_id` is preserved instead of overwriting a previous raw log.

`FIP_RETRY_MAX_BACKOFF_SEC` is wired to Vector's `retry_max_duration_secs` because Vector 0.54 does not expose a separate per-attempt max backoff setting for the GCS sink.

## Optional Observability Profile

Use this profile when the deployment has selected a scraper or external monitoring integration for Prometheus-compatible metrics.

Additional env:

- `FIP_METRICS_ADDRESS`
- `FIP_METRICS_SCRAPE_INTERVAL_SEC`

```bash
vector \
  --config config/common/transforms.yaml \
  --config config/common/observability.yaml \
  --config config/production/source_http.yaml \
  --config config/production/sinks_gcs.yaml
```

- Operational logs are JSON lines on stdout.
- Result logs include `component`, `result`, `trace_id`, `event_id`, `req_id`, `schema_version`, `error_code`, and `ingested_at`.
- Result logs intentionally exclude raw payload fields such as `query_text` and `original_payload`.
- Prometheus-format Vector internal metrics are exposed at `FIP_METRICS_ADDRESS`.
- Operators should watch HTTP source request/reject/error signals, sink success/failure signals, retry activity, and disk buffer utilization from the Vector internal metrics endpoint.
