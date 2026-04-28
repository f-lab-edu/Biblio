#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v vector >/dev/null 2>&1; then
  echo "vector CLI is required. Install Vector or run this script through the timberio/vector Docker image." >&2
  exit 127
fi

export FIP_FIXTURE_INPUT_PATH="${FIP_FIXTURE_INPUT_PATH:-./fixtures/feedback_event.valid.jsonl}"
export FIP_LOCAL_OUTPUT_DIR="${FIP_LOCAL_OUTPUT_DIR:-/tmp/biblio-fip-output}"
export FIP_VECTOR_DATA_DIR="${FIP_VECTOR_DATA_DIR:-/tmp/biblio-fip-vector-data}"
export FIP_SMOKE_TIMEOUT_SEC="${FIP_SMOKE_TIMEOUT_SEC:-5}"

rm -rf "$FIP_LOCAL_OUTPUT_DIR" "$FIP_VECTOR_DATA_DIR"
mkdir -p "$FIP_LOCAL_OUTPUT_DIR"
mkdir -p "$FIP_VECTOR_DATA_DIR"

set +e
timeout --signal INT --kill-after 2s "$FIP_SMOKE_TIMEOUT_SEC" vector \
  --config config/common/transforms.yaml \
  --config config/test/source_fixture.yaml \
  --config config/test/sinks_local.yaml
vector_status=$?
set -e

if [[ "$vector_status" != "0" && "$vector_status" != "124" && "$vector_status" != "137" ]]; then
  exit "$vector_status"
fi

if [[ ! -s "$FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl" && ! -s "$FIP_LOCAL_OUTPUT_DIR/error-events.jsonl" ]]; then
  echo "expected fixture event in $FIP_LOCAL_OUTPUT_DIR/raw-events.jsonl or $FIP_LOCAL_OUTPUT_DIR/error-events.jsonl" >&2
  exit 1
fi
