#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export GCP_PROJECT_ID="${GCP_PROJECT_ID:-local-validation-project}"
export FIP_HTTP_ADDRESS="${FIP_HTTP_ADDRESS:-0.0.0.0:8080}"
export FIP_HTTP_PATH="${FIP_HTTP_PATH:-/feedback/events}"
export FIP_METRICS_ADDRESS="${FIP_METRICS_ADDRESS:-0.0.0.0:9598}"
export FIP_METRICS_SCRAPE_INTERVAL_SEC="${FIP_METRICS_SCRAPE_INTERVAL_SEC:-5}"
export GCS_FEEDBACK_LOG_BUCKET_NAME="${GCS_FEEDBACK_LOG_BUCKET_NAME:-local-validation-feedback-logs}"
export FIP_SINK_BATCH_MAX_EVENTS="${FIP_SINK_BATCH_MAX_EVENTS:-100}"
export FIP_SINK_FLUSH_TIMEOUT_SEC="${FIP_SINK_FLUSH_TIMEOUT_SEC:-10}"
export FIP_SINK_TIMEOUT_SEC="${FIP_SINK_TIMEOUT_SEC:-30}"
export FIP_RETRY_MAX_ATTEMPTS="${FIP_RETRY_MAX_ATTEMPTS:-5}"
export FIP_RETRY_INITIAL_BACKOFF_SEC="${FIP_RETRY_INITIAL_BACKOFF_SEC:-1}"
export FIP_RETRY_MAX_BACKOFF_SEC="${FIP_RETRY_MAX_BACKOFF_SEC:-60}"
export FIP_DISK_BUFFER_MAX_SIZE_MB="${FIP_DISK_BUFFER_MAX_SIZE_MB:-512}"
export FIP_FIXTURE_INPUT_PATH="${FIP_FIXTURE_INPUT_PATH:-./fixtures/feedback_event.valid.jsonl}"
export FIP_LOCAL_OUTPUT_DIR="${FIP_LOCAL_OUTPUT_DIR:-/tmp/biblio-fip-output}"
export FIP_VECTOR_DATA_DIR="${FIP_VECTOR_DATA_DIR:-/tmp/biblio-fip-vector-data}"

if ! command -v vector >/dev/null 2>&1; then
  echo "vector CLI is required. Install Vector or run the same command through the timberio/vector Docker image." >&2
  exit 127
fi

mkdir -p "$FIP_VECTOR_DATA_DIR"

vector validate --no-environment \
  config/common/transforms.yaml \
  config/common/observability.yaml \
  config/production/source_http.yaml \
  config/production/sinks_gcs.yaml

vector validate --no-environment \
  config/common/transforms.yaml \
  config/common/observability.yaml \
  config/test/source_fixture.yaml \
  config/test/sinks_local.yaml
