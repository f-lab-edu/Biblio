#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
. ./scripts/lib.sh

if ! command -v vector >/dev/null 2>&1; then
  echo "vector CLI is required. Install Vector or run this script through the timberio/vector Docker image." >&2
  exit 127
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for metrics endpoint smoke." >&2
  exit 127
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI is required to verify GCS objects." >&2
  exit 127
fi

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required (e.g. project-6cf705ea-d0de-4a65-b46)}"
: "${GCS_FEEDBACK_LOG_BUCKET_NAME:?GCS_FEEDBACK_LOG_BUCKET_NAME is required (e.g. biblio-feedback-logs-dev-001)}"

export GCP_PROJECT_ID
export GCS_FEEDBACK_LOG_BUCKET_NAME

ADC_DEFAULT_PATH="$HOME/.config/gcloud/application_default_credentials.json"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$ADC_DEFAULT_PATH}"
if [[ ! -r "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
  echo "ADC file not readable at $GOOGLE_APPLICATION_CREDENTIALS." >&2
  echo "Run 'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS." >&2
  exit 2
fi

export FIP_HTTP_ADDRESS="${FIP_HTTP_ADDRESS:-127.0.0.1:18080}"
export FIP_HTTP_PATH="${FIP_HTTP_PATH:-/feedback/events}"
export FIP_FEEDBACK_DELIVERY_URL="${FIP_FEEDBACK_DELIVERY_URL:-$(loopback_http_url "$FIP_HTTP_ADDRESS" "$FIP_HTTP_PATH")}"
export FIP_METRICS_ADDRESS="${FIP_METRICS_ADDRESS:-127.0.0.1:19598}"
export FIP_METRICS_SCRAPE_INTERVAL_SEC="${FIP_METRICS_SCRAPE_INTERVAL_SEC:-1}"
export FIP_VECTOR_DATA_DIR="${FIP_VECTOR_DATA_DIR:-/tmp/biblio-staging-fip-vector-data}"
export FIP_SMOKE_TIMEOUT_SEC="${FIP_SMOKE_TIMEOUT_SEC:-25}"

export FIP_SINK_BATCH_MAX_EVENTS="${FIP_SINK_BATCH_MAX_EVENTS:-1}"
export FIP_SINK_FLUSH_TIMEOUT_SEC="${FIP_SINK_FLUSH_TIMEOUT_SEC:-2}"
export FIP_SINK_TIMEOUT_SEC="${FIP_SINK_TIMEOUT_SEC:-15}"
export FIP_RETRY_MAX_ATTEMPTS="${FIP_RETRY_MAX_ATTEMPTS:-3}"
export FIP_RETRY_INITIAL_BACKOFF_SEC="${FIP_RETRY_INITIAL_BACKOFF_SEC:-1}"
export FIP_RETRY_MAX_BACKOFF_SEC="${FIP_RETRY_MAX_BACKOFF_SEC:-15}"
export FIP_DISK_BUFFER_MAX_SIZE_MB="${FIP_DISK_BUFFER_MAX_SIZE_MB:-512}"

export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"
LOG_PREVIEW_RANGE="${LOG_PREVIEW_RANGE:-1,200p}"

CORE_API_ROOT="${CORE_API_ROOT:-../core-api}"
CORE_API_PYTHON="${CORE_API_PYTHON:-$CORE_API_ROOT/.venv/bin/python}"

if [[ ! -x "$CORE_API_PYTHON" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 or CORE_API_PYTHON is required for Core API delivery smoke." >&2
    exit 127
  fi
  CORE_API_PYTHON="$(command -v python3)"
fi

bucket_uri="gs://$GCS_FEEDBACK_LOG_BUCKET_NAME"
feedback_prefix="$bucket_uri/feedback/"

before_list="$(mktemp)"
after_list="$(mktemp)"
new_list="$(mktemp)"
fetched_object="$(mktemp)"
vector_log="$(mktemp)"
metrics_output="$(mktemp)"

gcloud storage ls -r "$feedback_prefix" --project="$GCP_PROJECT_ID" 2>/dev/null \
  | grep -v '/$' \
  | sort >"$before_list" || true

rm -rf "$FIP_VECTOR_DATA_DIR"
mkdir -p "$FIP_VECTOR_DATA_DIR"

set +e
timeout --signal INT --kill-after 3s "$FIP_SMOKE_TIMEOUT_SEC" vector \
  --config config/common/transforms.yaml \
  --config config/common/observability.yaml \
  --config config/production/source_http.yaml \
  --config config/production/sinks_gcs.yaml >"$vector_log" 2>&1 &
vector_pid=$!
set -e

cleanup_vector() {
  kill -INT "$vector_pid" >/dev/null 2>&1 || true
  wait "$vector_pid" >/dev/null 2>&1 || true
  return 0
}

new_objects=""
cleanup_objects() {
  if [[ -s "$new_list" ]]; then
    while IFS= read -r obj; do
      [[ -n "$obj" ]] && gcloud storage rm "$obj" --project="$GCP_PROJECT_ID" --quiet >/dev/null 2>&1 || true
    done <"$new_list"
  fi
  return 0
}

trap 'cleanup_vector; cleanup_objects' EXIT

sleep 3

(
  cd "$CORE_API_ROOT"
  FIP_FEEDBACK_DELIVERY_URL="$FIP_FEEDBACK_DELIVERY_URL" \
    PYTHONPATH=. \
    "$CORE_API_PYTHON" scripts/smoke_feedback_delivery_to_fip.py
)

# Allow time for batch flush + GCS round-trip.
sleep "$((FIP_SINK_FLUSH_TIMEOUT_SEC + 6))"

metrics_status="$(curl -sS -o "$metrics_output" -w "%{http_code}" \
  "$(loopback_http_url "$FIP_METRICS_ADDRESS" "/metrics")")"

cleanup_vector

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

gcloud storage ls -r "$feedback_prefix" --project="$GCP_PROJECT_ID" 2>/dev/null \
  | grep -v '/$' \
  | sort >"$after_list" || true

comm -13 "$before_list" "$after_list" >"$new_list" || true

if [[ ! -s "$new_list" ]]; then
  echo "expected at least one new GCS object under $feedback_prefix" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

raw_object="$(grep '/feedback/raw_logs/' "$new_list" | head -1 || true)"
if [[ -z "$raw_object" ]]; then
  echo "expected new raw_log object under feedback/raw_logs/ prefix" >&2
  echo "new objects:" >&2
  cat "$new_list" >&2
  sed -n "$LOG_PREVIEW_RANGE" "$vector_log" >&2
  exit 1
fi

echo "raw object: $raw_object"

gcloud storage cp "$raw_object" "$fetched_object" --project="$GCP_PROJECT_ID" --quiet >/dev/null

for needle in \
  '"req_id":"44444444-4444-4444-8444-444444444444"' \
  '"trace_id":"66666666-6666-4666-8666-666666666666"' \
  '"rating":"LIKE"'
do
  if ! grep -q "$needle" "$fetched_object"; then
    echo "expected $needle in raw GCS object body" >&2
    sed -n '1,40p' "$fetched_object" >&2
    exit 1
  fi
done

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

echo "staging smoke OK: GCS object validated and cleaned up"
