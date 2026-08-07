#!/usr/bin/env bash

set -euo pipefail

run_id="$1"
model_version="$2"
trace_namespace="$3"
result_dir="$HOME/biblio-target-load-results/$run_id"
compose_dir="/opt/biblio/managed-embedding-endpoint"
started_at="$(< "$result_dir/started-at.txt")"
ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ "$trace_namespace" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}$ ]] || {
  printf 'Invalid trace namespace: %s\n' "$trace_namespace" >&2
  exit 1
}
run_trace_pattern="^${trace_namespace}-[0-9a-f]{12}$"
recovery_trace_id="${trace_namespace}-ffffffffffff"

sudo -n docker compose --project-directory "$compose_dir" \
  -f "$compose_dir/docker-compose.yml" logs --no-color --no-log-prefix --since "$started_at" \
  managed-embedding-endpoint > "$result_dir/endpoint.log" 2>&1
jq -Rrc 'fromjson? | select(.msg == "embedding.admission")' \
  "$result_dir/endpoint.log" > "$result_dir/all-admission.jsonl"
jq -c --arg pattern "$run_trace_pattern" \
  --arg recovery_trace_id "$recovery_trace_id" \
  'select(((.trace_id // "") | test($pattern)) and
    ((.trace_id // "") != $recovery_trace_id))' \
  "$result_dir/all-admission.jsonl" > "$result_dir/admission.jsonl"
foreign_workload="$(jq -s --arg pattern "$run_trace_pattern" \
  --arg recovery_trace_id "$recovery_trace_id" \
  'map(select((
    ((.trace_id // "") | test($pattern)) or
    ((.trace_id // "") == $recovery_trace_id)
  ) | not)) | length' \
  "$result_dir/all-admission.jsonl")"
jq -s \
  --arg model_version "$model_version" \
  --argjson foreign_workload "$foreign_workload" \
  '{records: length,
    granted: (map(select(.admission_result == "granted")) | length),
    queue_timeout: (map(select(.admission_result == "queue_timeout")) | length),
    queue_full: (map(select(.admission_result == "queue_full")) | length),
    max_queue_wait_ms: ([.[].queue_wait_ms] | max // 0),
    max_search_queue_depth: ([.[].search_queue_depth] | max // 0),
    foreign_workload_records: $foreign_workload,
    model_version_matches: (all(.model_version == $model_version))}' \
  "$result_dir/admission.jsonl" > "$result_dir/admission-summary.json"

fd_errors="$(grep -Eci 'too many open files|file descriptor' "$result_dir/endpoint.log" || true)"
sudo -n journalctl -k --since "$started_at" --until "$ended_at" --no-pager \
  | grep -Ei 'out of memory|oom-killer|killed process' \
  > "$result_dir/kernel-oom.log" || true
oom_events="$(wc -l < "$result_dir/kernel-oom.log")"
metrics_tmp="$result_dir/target-metrics.json.tmp"
jq \
  --arg started_at "$started_at" \
  --arg ended_at "$ended_at" \
  --argjson fd_errors "$fd_errors" \
  --argjson oom_events "$oom_events" \
  '. + {started_at: $started_at, ended_at: $ended_at,
    file_descriptor_error_detected: ($fd_errors > 0), oom_event_detected: ($oom_events > 0)}' \
  "$result_dir/target-metrics.json" > "$metrics_tmp"
mv "$metrics_tmp" "$result_dir/target-metrics.json"
