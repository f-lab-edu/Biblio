#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
. ./scripts/lib.sh

if ! command -v vector >/dev/null 2>&1; then
  echo "vector CLI is required. Install Vector or run this script through the timberio/vector Docker image." >&2
  exit 127
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for HTTP source smoke." >&2
  exit 127
fi

export FIP_HTTP_ADDRESS="${FIP_HTTP_ADDRESS:-127.0.0.1:18080}"
export FIP_HTTP_PATH="${FIP_HTTP_PATH:-/feedback/events}"
export FIP_METRICS_ADDRESS="${FIP_METRICS_ADDRESS:-127.0.0.1:19598}"
export FIP_METRICS_SCRAPE_INTERVAL_SEC="${FIP_METRICS_SCRAPE_INTERVAL_SEC:-1}"
export FIP_LOCAL_OUTPUT_DIR="${FIP_LOCAL_OUTPUT_DIR:-/tmp/biblio-fip-http-output}"
export FIP_VECTOR_DATA_DIR="${FIP_VECTOR_DATA_DIR:-/tmp/biblio-fip-http-vector-data}"
export FIP_SMOKE_TIMEOUT_SEC="${FIP_SMOKE_TIMEOUT_SEC:-8}"
LOG_PREVIEW_RANGE="${LOG_PREVIEW_RANGE:-1,160p}"

rm -rf "$FIP_LOCAL_OUTPUT_DIR" "$FIP_VECTOR_DATA_DIR"
mkdir -p "$FIP_LOCAL_OUTPUT_DIR"
mkdir -p "$FIP_VECTOR_DATA_DIR"

vector_log="$(mktemp)"

set +e
timeout --signal INT --kill-after 2s "$FIP_SMOKE_TIMEOUT_SEC" vector \
  --config config/common/transforms.yaml \
  --config config/common/observability.yaml \
  --config config/production/source_http.yaml \
  --config config/test/sinks_local.yaml >"$vector_log" 2>&1 &
vector_pid=$!
set -e

sleep 2

# Local smoke only targets the loopback Vector HTTP source.
valid_status="$(curl -sS -o /tmp/biblio-fip-http-valid-response.txt -w "%{http_code}" \
  -X POST \
  --data-binary @fixtures/feedback_event.valid.jsonl \
  "$(loopback_http_url "$FIP_HTTP_ADDRESS" "$FIP_HTTP_PATH")")"

malformed_status="$(curl -sS -o /tmp/biblio-fip-http-malformed-response.txt -w "%{http_code}" \
  -X POST \
  --data-binary @fixtures/feedback_event.malformed.jsonl \
  "$(loopback_http_url "$FIP_HTTP_ADDRESS" "$FIP_HTTP_PATH")")"

# Metrics scraping is intentionally clear-text because the exporter is bound to loopback.
metrics_output="$(mktemp)"
metrics_status="$(curl -sS -o "$metrics_output" -w "%{http_code}" \
  "$(loopback_http_url "$FIP_METRICS_ADDRESS" "/metrics")")"

sleep 1

kill -INT "$vector_pid" >/dev/null 2>&1 || true
wait "$vector_pid" >/dev/null 2>&1 || true

if [[ "$valid_status" != "202" || "$malformed_status" != "202" ]]; then
  echo "expected HTTP 202 for valid and malformed seed events; got valid=$valid_status malformed=$malformed_status" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if [[ "$metrics_status" != "200" ]]; then
  echo "expected metrics endpoint HTTP 200; got $metrics_status" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if ! grep -q "vector_" "$metrics_output"; then
  echo "expected Vector internal metrics at $FIP_METRICS_ADDRESS" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$metrics_output" >&2
  exit 1
fi

if [[ ! -s "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" ]]; then
  echo "expected raw event in $FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if [[ ! -s "$FIP_LOCAL_OUTPUT_DIR/error-events.jsonl" ]]; then
  echo "expected error event in $FIP_LOCAL_OUTPUT_DIR/error-events.jsonl" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if ! grep -q '"result":"raw_log_ready"' "$vector_log"; then
  echo "expected raw_log_ready operational log" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if ! grep -q '"result":"error_log_ready"' "$vector_log"; then
  echo "expected error_log_ready operational log" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if grep -q 'test query' "$vector_log"; then
  echo "operational logs must not include raw query text" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi
