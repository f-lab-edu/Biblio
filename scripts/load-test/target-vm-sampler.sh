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

container_cgroup_path() {
  local id="$1"
  local pid
  local relative_path
  [[ -n "$id" ]] || return
  pid="$(sudo -n docker inspect --format '{{.State.Pid}}' "$id" 2>/dev/null || true)"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return
  relative_path="$(awk -F: '$1 == "0" {print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null || true)"
  [[ -n "$relative_path" ]] || return
  printf '/sys/fs/cgroup%s\n' "$relative_path"
}

container_process_pid() {
  local id="$1"
  [[ -n "$id" ]] || return
  sudo -n docker inspect --format '{{.State.Pid}}' "$id" 2>/dev/null || true
}

process_cpu_ticks() {
  local pid="$1"
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/stat" ]] || {
    printf '0\n'
    return
  }
  awk '{print $14 + $15}' "/proc/$pid/stat"
}

process_memory_bytes() {
  local pid="$1"
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/status" ]] || {
    printf '0\n'
    return
  }
  awk '$1 == "VmRSS:" {print $2 * 1024; found=1; exit} END {if (!found) print 0}' \
    "/proc/$pid/status"
}

container_cpu_usage_usec() {
  local cgroup_path="$1"
  [[ -r "$cgroup_path/cpu.stat" ]] || {
    printf '0\n'
    return
  }
  awk '$1 == "usage_usec" {print $2; found=1; exit} END {if (!found) print 0}' \
    "$cgroup_path/cpu.stat"
}

container_memory_bytes() {
  local cgroup_path="$1"
  [[ -r "$cgroup_path/memory.current" ]] || {
    printf '0\n'
    return
  }
  awk 'NR == 1 {print $1}' "$cgroup_path/memory.current"
}

initial_boot_id="$(< /proc/sys/kernel/random/boot_id)"
initial_container_id="$(container_id)"
initial_container_restart_count="$(container_restart_count "$initial_container_id")"
initial_container_cgroup="$(container_cgroup_path "$initial_container_id")"
endpoint_process_pid="$(container_process_pid "$initial_container_id")"
host_cpu_count="$(getconf _NPROCESSORS_ONLN)"
clock_ticks_per_second="$(getconf CLK_TCK)"
host_memory_bytes="$(awk '/MemTotal/ {print $2 * 1024; exit}' /proc/meminfo)"
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
previous_container_cpu_usec="$(container_cpu_usage_usec "$initial_container_cgroup")"
previous_process_cpu_ticks="$(process_cpu_ticks "$endpoint_process_pid")"
printf 'timestamp_utc\tcpu_percent\tmemory_percent\tnetwork_bytes_per_second\tcontainer_running\tcontainer_cpu_percent\tcontainer_memory_percent\tcontainer_memory_bytes\tendpoint_process_cpu_percent\tendpoint_process_memory_percent\tendpoint_process_memory_bytes\n' > "$SAMPLE_PATH"

last_container_running="$(container_running "$initial_container_id")"
sample_number=0
while [[ ! -f "$STOP_FILE" ]]; do
  sleep "$SAMPLE_INTERVAL_SECONDS"
  read -r current_total current_idle < <(
    awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
  )
  current_network="$(network_total)"
  current_container_cpu_usec="$(container_cpu_usage_usec "$initial_container_cgroup")"
  current_container_memory_bytes="$(container_memory_bytes "$initial_container_cgroup")"
  current_process_cpu_ticks="$(process_cpu_ticks "$endpoint_process_pid")"
  current_process_memory_bytes="$(process_memory_bytes "$endpoint_process_pid")"
  memory_percent="$(awk '/MemTotal/ {total=$2} /MemAvailable/ {available=$2} END {printf "%.2f", (total-available)*100/total}' /proc/meminfo)"
  cpu_percent="$(awk -v total="$((current_total - previous_total))" -v idle="$((current_idle - previous_idle))" 'BEGIN {if (total == 0) print "0.00"; else printf "%.2f", (total-idle)*100/total}')"
  container_cpu_percent="$(awk \
    -v current="$current_container_cpu_usec" \
    -v previous="$previous_container_cpu_usec" \
    -v seconds="$SAMPLE_INTERVAL_SECONDS" \
    -v cpus="$host_cpu_count" \
    'BEGIN {if (seconds == 0 || cpus == 0) print "0.00"; else printf "%.2f", (current-previous)*100/(seconds*1000000*cpus)}')"
  container_memory_percent="$(awk \
    -v used="$current_container_memory_bytes" \
    -v total="$host_memory_bytes" \
    'BEGIN {if (total == 0) print "0.00"; else printf "%.2f", used*100/total}')"
  process_cpu_percent="$(awk \
    -v current="$current_process_cpu_ticks" \
    -v previous="$previous_process_cpu_ticks" \
    -v seconds="$SAMPLE_INTERVAL_SECONDS" \
    -v ticks="$clock_ticks_per_second" \
    -v cpus="$host_cpu_count" \
    'BEGIN {if (seconds == 0 || ticks == 0 || cpus == 0) print "0.00"; else printf "%.2f", (current-previous)*100/(seconds*ticks*cpus)}')"
  process_memory_percent="$(awk \
    -v used="$current_process_memory_bytes" \
    -v total="$host_memory_bytes" \
    'BEGIN {if (total == 0) print "0.00"; else printf "%.2f", used*100/total}')"
  network_delta="$((current_network - previous_network))"
  network_per_second="$((network_delta / SAMPLE_INTERVAL_SECONDS))"
  ((sample_number += 1))
  if ((sample_number % 5 == 0)); then
    last_container_running="$(container_running "$(container_id)")"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$cpu_percent" \
    "$memory_percent" \
    "$network_per_second" \
    "$last_container_running" \
    "$container_cpu_percent" \
    "$container_memory_percent" \
    "$current_container_memory_bytes" \
    "$process_cpu_percent" \
    "$process_memory_percent" \
    "$current_process_memory_bytes" >> "$SAMPLE_PATH"
  previous_total="$current_total"
  previous_idle="$current_idle"
  previous_network="$current_network"
  previous_container_cpu_usec="$current_container_cpu_usec"
  previous_process_cpu_ticks="$current_process_cpu_ticks"
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
     if ($6 > max_container_cpu) max_container_cpu=$6;
     if ($7 > max_container_memory) max_container_memory=$7;
     if ($8 > max_container_memory_bytes) max_container_memory_bytes=$8;
     if ($9 > max_process_cpu) max_process_cpu=$9;
     if ($10 > max_process_memory) max_process_memory=$10;
     if ($11 > max_process_memory_bytes) max_process_memory_bytes=$11;
     samples++;
   }
   END {
     utilization = capacity > 0 ? max_network / capacity : 0;
     printf "{\"sample_count\":%d,\"max_cpu_percent\":%.2f,\"max_memory_percent\":%.2f,\"max_network_bytes_per_second\":%.0f,\"max_container_cpu_percent\":%.2f,\"max_container_memory_percent\":%.2f,\"max_container_memory_bytes\":%.0f,\"max_endpoint_process_cpu_percent\":%.2f,\"max_endpoint_process_memory_percent\":%.2f,\"max_endpoint_process_memory_bytes\":%.0f,\"network_capacity_bytes_per_second\":%.0f,\"network_utilization\":%.6f,\"initial_boot_id\":\"%s\",\"final_boot_id\":\"%s\",\"initial_container_id\":\"%s\",\"final_container_id\":\"%s\",\"initial_container_restart_count\":%d,\"final_container_restart_count\":%d,\"container_not_running_samples\":%d,\"container_running_at_end\":%s,\"container_restart_detected\":%s,\"vm_restart_detected\":%s}\n",
       samples, max_cpu, max_memory, max_network,
       max_container_cpu, max_container_memory, max_container_memory_bytes,
       max_process_cpu, max_process_memory, max_process_memory_bytes,
       capacity, utilization,
       initial_boot_id, final_boot_id, initial_container_id, final_container_id,
       initial_container_restart_count, final_container_restart_count,
       container_not_running_samples,
       (final_container_running == "true" ? "true" : "false"),
       (initial_container_id == final_container_id && initial_container_restart_count == final_container_restart_count ? "false" : "true"),
       (initial_boot_id == final_boot_id ? "false" : "true");
   }' "$SAMPLE_PATH" > "$METRICS_PATH"

rm -f "$STOP_FILE"
