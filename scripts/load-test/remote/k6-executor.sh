#!/usr/bin/env bash

set -euo pipefail

remote_root="$HOME/$1"
scenario="$2"
run_id="$3"
scenario_slug="$4"
target_url="$5"
iam_audience="$6"
expected_status="$7"
network_capacity_bps="$8"
model_version="$9"
rate="${10}"
time_unit="${11}"
duration="${12}"
client_timeout_seconds="${13}"
pre_allocated_vus="${14}"
max_vus="${15}"
trace_id_namespace="${16}"
result_dir="$HOME/biblio-load-results/$run_id/$scenario_slug"
raw_path="$result_dir/raw.json"
sample_path="$result_dir/runner-samples.tsv"

mkdir -p "$result_dir"
interface="$(ip route show default | awk 'NR == 1 {print $5}')"
initial_boot_id="$(< /proc/sys/kernel/random/boot_id)"
read -r previous_total previous_idle < <(
  awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
)
previous_network="$(awk -v iface="$interface:" '$1 == iface {print $2 + $10}' /proc/net/dev)"
printf 'cpu_percent\tmemory_percent\tnetwork_bytes_per_second\n' > "$sample_path"

export TARGET_URL="$target_url"
export IAM_AUDIENCE="$iam_audience"
export EXPECTED_STATUS="$expected_status"
export MODEL_VERSION="$model_version"
export RUN_ID="$run_id"
export LT_RATE="$rate"
export LT_TIME_UNIT="$time_unit"
export LT_DURATION="$duration"
export LT_CLIENT_TIMEOUT_SECONDS="$client_timeout_seconds"
export LT_PRE_ALLOCATED_VUS="$pre_allocated_vus"
export LT_MAX_VUS="$max_vus"
export TRACE_ID_NAMESPACE="$trace_id_namespace"

set +e
(
  set -o pipefail
  cd "$remote_root"
  k6 run \
    --summary-export "$result_dir/summary.json" \
    --out "json=$raw_path" \
    "$scenario" 2>&1 | tee "$result_dir/console.log"
) &
k6_pid=$!

while kill -0 "$k6_pid" 2>/dev/null; do
  sleep 1
  read -r current_total current_idle < <(
    awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
  )
  current_network="$(awk -v iface="$interface:" '$1 == iface {print $2 + $10}' /proc/net/dev)"
  memory_percent="$(awk '/MemTotal/ {total=$2} /MemAvailable/ {available=$2} END {printf "%.2f", (total-available)*100/total}' /proc/meminfo)"
  cpu_percent="$(awk -v total="$((current_total - previous_total))" -v idle="$((current_idle - previous_idle))" 'BEGIN {if (total == 0) print "0.00"; else printf "%.2f", (total-idle)*100/total}')"
  network_delta="$((current_network - previous_network))"
  printf '%s\t%s\t%s\n' "$cpu_percent" "$memory_percent" "$network_delta" >> "$sample_path"
  previous_total="$current_total"
  previous_idle="$current_idle"
  previous_network="$current_network"
done

wait "$k6_pid"
k6_status=$?
set -e

if [[ -f "$raw_path" ]]; then
  gzip -f "$raw_path"
fi
final_boot_id="$(< /proc/sys/kernel/random/boot_id)"
fd_errors="$(grep -Eci 'too many open files|file descriptor' "$result_dir/console.log" || true)"
awk \
  -v capacity="$network_capacity_bps" \
  -v initial_boot_id="$initial_boot_id" \
  -v final_boot_id="$final_boot_id" \
  -v fd_error="$fd_errors" \
  'NR > 1 {
     if ($1 > max_cpu) max_cpu=$1;
     if ($2 > max_memory) max_memory=$2;
     if ($3 > max_network) max_network=$3;
   }
   END {
     utilization = capacity > 0 ? max_network / capacity : 0;
     printf "{\"max_cpu_percent\":%.2f,\"max_memory_percent\":%.2f,\"max_network_bytes_per_second\":%.0f,\"network_capacity_bytes_per_second\":%.0f,\"network_utilization\":%.6f,\"network_saturation_detected\":%s,\"file_descriptor_error_detected\":%s,\"vm_restart_detected\":%s}\n",
       max_cpu, max_memory, max_network, capacity, utilization,
       (utilization >= 0.9 ? "true" : "false"),
       (fd_error > 0 ? "true" : "false"),
       (initial_boot_id == final_boot_id ? "false" : "true");
   }' "$sample_path" > "$result_dir/runner-metrics.json"
rm -f "$sample_path"
exit "$k6_status"
