#!/usr/bin/env bash

set -euo pipefail

OUTPUT_DIR=""
NETWORK_CAPACITY_BPS="0"
COMPOSE_DIR=""
SAMPLE_INTERVAL_SECONDS="1"

usage() {
  cat <<'EOF'
Usage: target-vm-sampler.sh --output-dir <path> --compose-dir <path> [options]

Options:
  --network-capacity-bps <bytes>  Network capacity used only for metadata.
  --sample-interval <seconds>     Sampling interval. Default: 1.
EOF
}

while (($# > 0)); do
  case "$1" in
    --output-dir|--compose-dir|--network-capacity-bps|--sample-interval)
      (($# >= 2)) || {
        printf '%s requires a value.\n' "$1" >&2
        exit 1
      }
      case "$1" in
        --output-dir) OUTPUT_DIR="$2" ;;
        --compose-dir) COMPOSE_DIR="$2" ;;
        --network-capacity-bps) NETWORK_CAPACITY_BPS="$2" ;;
        --sample-interval) SAMPLE_INTERVAL_SECONDS="$2" ;;
      esac
      shift 2
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "$OUTPUT_DIR" && -n "$COMPOSE_DIR" ]] || {
  usage >&2
  exit 1
}
[[ "$NETWORK_CAPACITY_BPS" =~ ^[0-9]+$ ]] || {
  printf 'Network capacity must be a non-negative integer.\n' >&2
  exit 1
}
[[ "$SAMPLE_INTERVAL_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  printf 'Sample interval must be a positive integer.\n' >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"
STOP_FILE="$OUTPUT_DIR/stop"
SAMPLE_PATH="$OUTPUT_DIR/target-samples.tsv"
METRICS_PATH="$OUTPUT_DIR/target-metrics.json"
PID_PATH="$OUTPUT_DIR/sampler.pid"
STARTED_AT_PATH="$OUTPUT_DIR/started-at.txt"
rm -f "$STOP_FILE"
trap 'rm -f "$PID_PATH"' EXIT

container_id() {
  sudo -n docker compose \
    --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" \
    ps -q managed-embedding-endpoint 2>/dev/null || true
}

container_running() {
  local id="$1"
  [[ -n "$id" ]] || {
    printf 'false\n'
    return
  }
  sudo -n docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null || printf 'false\n'
}

container_restart_count() {
  local id="$1"
  [[ -n "$id" ]] || {
    printf '0\n'
    return
  }
  sudo -n docker inspect --format '{{.RestartCount}}' "$id" 2>/dev/null || printf '0\n'
}

initial_boot_id="$(< /proc/sys/kernel/random/boot_id)"
initial_container_id="$(container_id)"
initial_container_restart_count="$(container_restart_count "$initial_container_id")"
interface="$(ip route show default 2>/dev/null | awk 'NR == 1 {print $5}' || true)"
if [[ -z "$interface" ]]; then
  interface="$(awk '$2 == "00000000" {print $1; exit}' /proc/net/route)"
fi
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n' "$started_at" > "$STARTED_AT_PATH"
printf '%s\n' "$$" > "$PID_PATH"

read -r previous_total previous_idle < <(
  awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
)
network_total() {
  if [[ -z "$interface" ]]; then
    printf '0\n'
  else
    awk -v iface="$interface:" '$1 == iface {print $2 + $10}' /proc/net/dev
  fi
}

previous_network="$(network_total)"
printf 'timestamp_utc\tcpu_percent\tmemory_percent\tnetwork_bytes_per_second\tcontainer_running\n' > "$SAMPLE_PATH"

last_container_running="$(container_running "$initial_container_id")"
sample_number=0
while [[ ! -f "$STOP_FILE" ]]; do
  sleep "$SAMPLE_INTERVAL_SECONDS"
  read -r current_total current_idle < <(
    awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
  )
  current_network="$(network_total)"
  memory_percent="$(awk '/MemTotal/ {total=$2} /MemAvailable/ {available=$2} END {printf "%.2f", (total-available)*100/total}' /proc/meminfo)"
  cpu_percent="$(awk -v total="$((current_total - previous_total))" -v idle="$((current_idle - previous_idle))" 'BEGIN {if (total == 0) print "0.00"; else printf "%.2f", (total-idle)*100/total}')"
  network_delta="$((current_network - previous_network))"
  network_per_second="$((network_delta / SAMPLE_INTERVAL_SECONDS))"
  ((sample_number += 1))
  if ((sample_number % 5 == 0)); then
    last_container_running="$(container_running "$(container_id)")"
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$cpu_percent" \
    "$memory_percent" \
    "$network_per_second" \
    "$last_container_running" >> "$SAMPLE_PATH"
  previous_total="$current_total"
  previous_idle="$current_idle"
  previous_network="$current_network"
done

final_boot_id="$(< /proc/sys/kernel/random/boot_id)"
final_container_id="$(container_id)"
final_container_running="$(container_running "$final_container_id")"
final_container_restart_count="$(container_restart_count "$final_container_id")"

awk \
  -v capacity="$NETWORK_CAPACITY_BPS" \
  -v initial_boot_id="$initial_boot_id" \
  -v final_boot_id="$final_boot_id" \
  -v initial_container_id="$initial_container_id" \
  -v final_container_id="$final_container_id" \
  -v initial_container_restart_count="$initial_container_restart_count" \
  -v final_container_restart_count="$final_container_restart_count" \
  -v final_container_running="$final_container_running" \
  'NR > 1 {
     if ($2 > max_cpu) max_cpu=$2;
     if ($3 > max_memory) max_memory=$3;
     if ($4 > max_network) max_network=$4;
     if ($5 != "true") container_not_running_samples++;
     samples++;
   }
   END {
     utilization = capacity > 0 ? max_network / capacity : 0;
     printf "{\"sample_count\":%d,\"max_cpu_percent\":%.2f,\"max_memory_percent\":%.2f,\"max_network_bytes_per_second\":%.0f,\"network_capacity_bytes_per_second\":%.0f,\"network_utilization\":%.6f,\"initial_boot_id\":\"%s\",\"final_boot_id\":\"%s\",\"initial_container_id\":\"%s\",\"final_container_id\":\"%s\",\"initial_container_restart_count\":%d,\"final_container_restart_count\":%d,\"container_not_running_samples\":%d,\"container_running_at_end\":%s,\"container_restart_detected\":%s,\"vm_restart_detected\":%s}\n",
       samples, max_cpu, max_memory, max_network, capacity, utilization,
       initial_boot_id, final_boot_id, initial_container_id, final_container_id,
       initial_container_restart_count, final_container_restart_count,
       container_not_running_samples,
       (final_container_running == "true" ? "true" : "false"),
       (initial_container_id == final_container_id && initial_container_restart_count == final_container_restart_count ? "false" : "true"),
       (initial_boot_id == final_boot_id ? "false" : "true");
   }' "$SAMPLE_PATH" > "$METRICS_PATH"

rm -f "$STOP_FILE"
