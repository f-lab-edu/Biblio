#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v vector >/dev/null 2>&1; then
  echo "vector CLI is required. Install Vector or run this script through the timberio/vector Docker image." >&2
  exit 127
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for metrics endpoint smoke." >&2
  exit 127
fi

export FIP_HTTP_ADDRESS="${FIP_HTTP_ADDRESS:-127.0.0.1:18080}"
export FIP_HTTP_PATH="${FIP_HTTP_PATH:-/feedback/events}"
export FIP_FEEDBACK_DELIVERY_URL="${FIP_FEEDBACK_DELIVERY_URL:-http://$FIP_HTTP_ADDRESS$FIP_HTTP_PATH}"
export FIP_METRICS_ADDRESS="${FIP_METRICS_ADDRESS:-127.0.0.1:19598}"
export FIP_METRICS_SCRAPE_INTERVAL_SEC="${FIP_METRICS_SCRAPE_INTERVAL_SEC:-1}"
export FIP_LOCAL_OUTPUT_DIR="${FIP_LOCAL_OUTPUT_DIR:-/tmp/biblio-core-api-fip-output}"
export FIP_VECTOR_DATA_DIR="${FIP_VECTOR_DATA_DIR:-/tmp/biblio-core-api-fip-vector-data}"
export FIP_SMOKE_TIMEOUT_SEC="${FIP_SMOKE_TIMEOUT_SEC:-8}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"
LOG_PREVIEW_RANGE="${LOG_PREVIEW_RANGE:-1,160p}"

CORE_API_ROOT="${CORE_API_ROOT:-../core-api}"
CORE_API_PYTHON="${CORE_API_PYTHON:-$CORE_API_ROOT/.venv/bin/python}"

if [[ ! -x "$CORE_API_PYTHON" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 or CORE_API_PYTHON is required for Core API delivery smoke." >&2
    exit 127
  fi
  CORE_API_PYTHON="$(command -v python3)"
fi

rm -rf "$FIP_LOCAL_OUTPUT_DIR" "$FIP_VECTOR_DATA_DIR"
mkdir -p "$FIP_LOCAL_OUTPUT_DIR"
mkdir -p "$FIP_VECTOR_DATA_DIR"

vector_log="$(mktemp)"
metrics_output="$(mktemp)"

set +e
timeout --signal INT --kill-after 2s "$FIP_SMOKE_TIMEOUT_SEC" vector \
  --config config/common/transforms.yaml \
  --config config/common/observability.yaml \
  --config config/production/source_http.yaml \
  --config config/test/sinks_local.yaml >"$vector_log" 2>&1 &
vector_pid=$!
set -e

cleanup() {
  kill -INT "$vector_pid" >/dev/null 2>&1 || true
  wait "$vector_pid" >/dev/null 2>&1 || true
  return 0
}
trap cleanup EXIT

sleep 2

(
  cd "$CORE_API_ROOT"
  FIP_FEEDBACK_DELIVERY_URL="$FIP_FEEDBACK_DELIVERY_URL" \
    PYTHONPATH=. \
    "$CORE_API_PYTHON" scripts/smoke_feedback_delivery_to_fip.py
)

metrics_status="$(curl -sS -o "$metrics_output" -w "%{http_code}" \
  "http://$FIP_METRICS_ADDRESS/metrics")"

sleep 1
cleanup
trap - EXIT

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

if ! grep -q '"req_id":"44444444-4444-4444-8444-444444444444"' "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl"; then
  echo "expected Core API smoke req_id in raw event output" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" >&2
  exit 1
fi

if ! grep -q '"trace_id":"66666666-6666-4666-8666-666666666666"' "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl"; then
  echo "expected Core API smoke trace_id in raw event output" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" >&2
  exit 1
fi

if ! grep -q '"rating":"LIKE"' "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl"; then
  echo "expected Core API smoke rating in raw event output" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" >&2
  exit 1
fi

if ! grep -q '"result":"raw_log_ready"' "$vector_log"; then
  echo "expected raw_log_ready operational log" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

if grep -q 'local smoke query text' "$vector_log"; then
  echo "operational logs must not include raw query text" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi
