#!/usr/bin/env bash

# 실행 환경 준비
#  → 테스트 코드 배포
#  → 원격 실행
#  → 부하 발생기 감시
#  → 결과 회수
#  → VM 종료


set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="${TF_DIR:-$REPO_ROOT/infra/terraform/envs/gcp-perf}"
LOAD_TEST_ROOT="$REPO_ROOT/load-tests/k6"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$REPO_ROOT/artifacts/load-tests}"
SYNC_STATE_FILE="$ARTIFACT_ROOT/.sync-state.json"
RUN_STATE_FILE="$ARTIFACT_ROOT/.last-run.json"

PROJECT_ID="${PROJECT_ID:-}"
RUNNER_NETWORK_CAPACITY_BPS="${RUNNER_NETWORK_CAPACITY_BPS:-500000000}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Required command not found: %s\n' "$1" >&2
    exit 1
  }
}

terraform_output() {
  terraform -chdir="$TF_DIR" output -raw "$1"
}

instance_name() {
  if [[ -n "${LOAD_TEST_VM_NAME:-}" ]]; then
    printf '%s\n' "$LOAD_TEST_VM_NAME"
  else
    terraform_output load_test_vm_name
  fi
}

instance_zone() {
  if [[ -n "${LOAD_TEST_VM_ZONE:-}" ]]; then
    printf '%s\n' "$LOAD_TEST_VM_ZONE"
  else
    terraform_output load_test_vm_zone
  fi
}

gcloud_compute() {
  gcloud compute "$@" --project "$PROJECT_ID" --quiet
}

ssh_command() {
  local command="$1"
  gcloud_compute ssh "$(instance_name)" \
    --zone "$(instance_zone)" \
    --tunnel-through-iap \
    --command "$command"
}

vm_status() {
  gcloud_compute instances describe "$(instance_name)" \
    --zone "$(instance_zone)" \
    --format='value(status)'
}

start_runner() {
  local status
  status="$(vm_status)"
  if [[ "$status" == "TERMINATED" ]]; then
    gcloud_compute instances start "$(instance_name)" --zone "$(instance_zone)"
  elif [[ "$status" != "RUNNING" ]]; then
    printf 'Runner is not startable from status %s\n' "$status" >&2
    return 1
  fi

  # VM 시작 후 k6와 운영 도구가 모두 준비될 때까지 기다린다.
  for _ in $(seq 1 36); do
    if ssh_command "test -f /var/lib/biblio-k6/ready && k6 version" >/dev/null 2>&1; then
      ssh_command "k6 version; systemctl is-active google-cloud-ops-agent; systemctl is-active k6-runner-autoshutdown.timer"
      return 0
    fi
    sleep 10
  done

  printf 'Runner startup did not become ready within six minutes.\n' >&2
  stop_runner || true
  return 1
}

stop_runner() {
  local status
  status="$(vm_status)"
  if [[ "$status" != "TERMINATED" ]]; then
    gcloud_compute instances stop "$(instance_name)" --zone "$(instance_zone)"
  fi
}

sync_sources() {
  local git_sha sync_id remote_root
  [[ "$(vm_status)" == "RUNNING" ]] || {
    printf 'Runner must be RUNNING before sync.\n' >&2
    return 1
  }

  git_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  sync_id="${git_sha:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
  remote_root="biblio-load-test-$sync_id"

  # 현재 테스트 코드를 실행별 디렉터리로 복사해 어떤 코드가 실행됐는지 남긴다.
  gcloud_compute scp --recurse "$LOAD_TEST_ROOT" \
    "$(instance_name):~/$remote_root" \
    --zone "$(instance_zone)" \
    --tunnel-through-iap

  mkdir -p "$ARTIFACT_ROOT"
  jq -n \
    --arg git_sha "$git_sha" \
    --arg remote_root "$remote_root" \
    '{git_sha: $git_sha, remote_root: $remote_root}' > "$SYNC_STATE_FILE"
}

remote_run_impl() {
  set -euo pipefail

  local remote_root="$HOME/$1"
  local scenario="$2"
  local run_id="$3"
  local scenario_slug="$4"
  local target_url="$5"
  local iam_audience="$6"
  local expected_status="$7"
  local network_capacity_bps="$8"
  local result_dir="$HOME/biblio-load-results/$run_id/$scenario_slug"
  local raw_path="$result_dir/raw.json"
  local sample_path="$result_dir/runner-samples.tsv"
  local interface initial_boot_id final_boot_id
  local previous_total previous_idle previous_network

  mkdir -p "$result_dir"
  interface="$(ip route show default | awk 'NR == 1 {print $5}')"
  initial_boot_id="$(cat /proc/sys/kernel/random/boot_id)"
  read -r previous_total previous_idle < <(
    awk 'NR == 1 {total=0; for (i=2; i<=NF; i++) total += $i; print total, $5 + $6}' /proc/stat
  )
  previous_network="$(awk -v iface="$interface:" '$1 == iface {print $2 + $10}' /proc/net/dev)"
  printf 'cpu_percent\tmemory_percent\tnetwork_bytes_per_second\n' > "$sample_path"

  export TARGET_URL="$target_url"
  export IAM_AUDIENCE="$iam_audience"
  export EXPECTED_STATUS="$expected_status"

  # k6를 백그라운드로 실행해 테스트와 부하 발생기 상태를 동시에 측정한다.
  set +e
  (
    set -o pipefail
    cd "$remote_root"
    k6 run \
      --summary-export "$result_dir/summary.json" \
      --out "json=$raw_path" \
      "$scenario" 2>&1 | tee "$result_dir/console.log"
  ) &
  local k6_pid=$!

  while kill -0 "$k6_pid" 2>/dev/null; do
    sleep 1
    local current_total current_idle current_network memory_percent cpu_percent network_delta
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
  local k6_status=$?
  set -e

  [[ -f "$raw_path" ]] && gzip -f "$raw_path"
  final_boot_id="$(cat /proc/sys/kernel/random/boot_id)"

  awk \
    -v capacity="$network_capacity_bps" \
    -v initial_boot_id="$initial_boot_id" \
    -v final_boot_id="$final_boot_id" \
    -v fd_error="$(grep -Eci 'too many open files|file descriptor' "$result_dir/console.log" || true)" \
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
  return "$k6_status"
}

run_remote_scenario_impl() {
  local scenario="$1"
  local target_url="${TARGET_URL:-}"
  local iam_audience="${IAM_AUDIENCE:-}"
  local expected_status="${EXPECTED_STATUS:-200}"
  local target_config_json="${TARGET_CONFIG_JSON:-}"
  local run_id scenario_slug remote_root git_sha machine_type k6_version remote_result
  local command remote_status collect_status

  [[ -n "$target_url" ]] || {
    printf 'TARGET_URL is required.\n' >&2
    return 1
  }
  [[ "$scenario" != /* && "$scenario" != *..* && "$scenario" == *.js ]] || {
    printf 'Scenario must be a relative .js path without .. segments.\n' >&2
    return 1
  }
  [[ -f "$LOAD_TEST_ROOT/$scenario" ]] || {
    printf 'Scenario not found: %s\n' "$LOAD_TEST_ROOT/$scenario" >&2
    return 1
  }
  [[ -f "$SYNC_STATE_FILE" ]] || {
    printf 'Run sync before executing a scenario.\n' >&2
    return 1
  }

  remote_root="$(jq -r '.remote_root' "$SYNC_STATE_FILE")"
  git_sha="$(jq -r '.git_sha' "$SYNC_STATE_FILE")"
  run_id="$(date -u +%Y%m%dT%H%M%SZ)"
  scenario_slug="$(basename "$scenario" .js)"
  remote_result="~/biblio-load-results/$run_id/$scenario_slug"
  machine_type="$(gcloud_compute instances describe "$(instance_name)" --zone "$(instance_zone)" --format='value(machineType.basename())')"
  k6_version="$(ssh_command "k6 version | head -n 1")"

  if [[ -z "$target_config_json" ]]; then
    target_config_json='{}'
  fi
  jq -e . >/dev/null <<<"$target_config_json" || {
    printf 'TARGET_CONFIG_JSON must contain valid JSON.\n' >&2
    return 1
  }

  mkdir -p "$ARTIFACT_ROOT"
  jq -n \
    --arg run_id "$run_id" \
    --arg scenario "$scenario_slug" \
    --arg remote_result "$remote_result" \
    --arg git_sha "$git_sha" \
    --arg k6_version "$k6_version" \
    --arg machine_type "$machine_type" \
    --arg target_url "$target_url" \
    --arg corpus_chunk_count "${CORPUS_CHUNK_COUNT:-not-set}" \
    --arg load_profile "${LOAD_PROFILE:-not-set}" \
    --arg query_set_hash "${QUERY_SET_HASH:-not-set}" \
    --arg fixture_manifest_hash "${FIXTURE_MANIFEST_HASH:-not-set}" \
    --arg corpus_manifest_hash "${CORPUS_MANIFEST_HASH:-not-set}" \
    --argjson target_config "$target_config_json" \
    '{run_id: $run_id, scenario: $scenario, remote_result: $remote_result, git_sha: $git_sha,
      k6_version: $k6_version, machine_type: $machine_type, target_url: $target_url,
      corpus_chunk_count: $corpus_chunk_count, load_profile: $load_profile,
      query_set_hash: $query_set_hash, fixture_manifest_hash: $fixture_manifest_hash,
      corpus_manifest_hash: $corpus_manifest_hash, target_config: $target_config}' > "$RUN_STATE_FILE"

  printf -v command 'bash -s -- %q %q %q %q %q %q %q %q' \
    "$remote_root" "$scenario" "$run_id" "$scenario_slug" "$target_url" \
    "$iam_audience" "$expected_status" "$RUNNER_NETWORK_CAPACITY_BPS"

  # 실행 함수와 인자를 IAP SSH로 전달해 k6 VM 안에서 시나리오를 실행한다.
  set +e
  {
    declare -f remote_run_impl
    printf '%s\n' 'remote_run_impl "$@"'
  } | gcloud_compute ssh "$(instance_name)" \
    --zone "$(instance_zone)" \
    --tunnel-through-iap \
    --command "$command"
  remote_status=${PIPESTATUS[1]}
  set -e

  # 테스트 성공 여부와 관계없이 결과를 회수하고 비용이 발생하지 않도록 VM을 정지한다.
  collect_status=0
  collect_results || collect_status=$?
  stop_runner || true

  if (( remote_status != 0 )); then
    printf 'k6 exited with status %s. The runner stop was attempted.\n' "$remote_status" >&2
    return "$remote_status"
  fi
  return "$collect_status"
}

run_remote_scenario() {
  local run_status=0 runner_status
  run_remote_scenario_impl "$@" || run_status=$?
  if ((run_status != 0)); then
    if runner_status="$(vm_status)" && [[ "$runner_status" != "TERMINATED" ]]; then
      stop_runner || true
    fi
  fi
  return "$run_status"
}

collect_results() {
  local run_id scenario remote_result local_parent local_dir dropped_iterations
  [[ -f "$RUN_STATE_FILE" ]] || {
    printf 'No run state is available to collect.\n' >&2
    return 1
  }

  run_id="$(jq -r '.run_id' "$RUN_STATE_FILE")"
  scenario="$(jq -r '.scenario' "$RUN_STATE_FILE")"
  remote_result="$(jq -r '.remote_result' "$RUN_STATE_FILE")"
  local_parent="$ARTIFACT_ROOT/$run_id"
  local_dir="$local_parent/$scenario"
  mkdir -p "$local_parent"

  if [[ ! -d "$local_dir" ]]; then
    # VM에서 생성된 k6 결과와 부하 발생기 측정값을 로컬로 가져온다.
    gcloud_compute scp --recurse \
      "$(instance_name):$remote_result" \
      "$local_parent/" \
      --zone "$(instance_zone)" \
      --tunnel-through-iap
  fi

  for required_file in summary.json raw.json.gz console.log runner-metrics.json; do
    [[ -s "$local_dir/$required_file" ]] || {
      printf 'Collected result is missing or empty: %s\n' "$local_dir/$required_file" >&2
      return 1
    }
  done
  jq -e 'type == "object" and (.max_cpu_percent | type == "number")' \
    "$local_dir/runner-metrics.json" >/dev/null || {
      printf 'runner-metrics.json is invalid.\n' >&2
      return 1
    }

  dropped_iterations="$(jq -r '.metrics.dropped_iterations.values.count // 0' "$local_dir/summary.json")" || return 1
  # 실행 조건과 부하 발생기 상태를 합쳐 이번 결과를 분석에 써도 되는지 판정한다.
  jq -s \
    --arg collected_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson dropped_iterations "$dropped_iterations" \
    '.[0] + {
      collected_at: $collected_at,
      runner_metrics: .[1],
      dropped_iterations: $dropped_iterations,
      acceptance: {
        cpu_below_80_percent: (.[1].max_cpu_percent < 80),
        memory_below_90_percent: (.[1].max_memory_percent < 90),
        dropped_iterations_zero: ($dropped_iterations == 0),
        network_not_saturated: (.[1].network_saturation_detected | not),
        no_file_descriptor_errors: (.[1].file_descriptor_error_detected | not),
        no_vm_restart: (.[1].vm_restart_detected | not),
        accepted: (
          .[1].max_cpu_percent < 80 and
          .[1].max_memory_percent < 90 and
          $dropped_iterations == 0 and
          (.[1].network_saturation_detected | not) and
          (.[1].file_descriptor_error_detected | not) and
          (.[1].vm_restart_detected | not)
        )
      }
    }' "$RUN_STATE_FILE" "$local_dir/runner-metrics.json" > "$local_dir/metadata.json" || return 1
  rm -f "$local_dir/runner-metrics.json"

  printf 'Results collected at %s\n' "$local_dir"
}

collect_command() {
  local started_for_collect=false
  if [[ "$(vm_status)" == "TERMINATED" ]]; then
    start_runner
    started_for_collect=true
  fi

  local collect_status=0
  collect_results || collect_status=$?
  if [[ "$started_for_collect" == true ]]; then
    stop_runner || true
  fi
  return "$collect_status"
}

smoke() {
  local service_url
  service_url="$(terraform_output search_service_url)"
  export TARGET_URL="${TARGET_URL:-${service_url%/}/health}"
  export IAM_AUDIENCE="${IAM_AUDIENCE:-$service_url}"
  export LOAD_PROFILE="1 VU / 10s"
  if [[ -z "${TARGET_CONFIG_JSON:-}" ]]; then
    TARGET_CONFIG_JSON="$(jq -nc \
      --arg service "search-service" \
      --arg endpoint "/health" \
      --arg authentication "cloud-run-iam" \
      '{service: $service, endpoint: $endpoint, authentication: $authentication}')"
  fi
  export TARGET_CONFIG_JSON

  # smoke 테스트 전체 흐름: VM 준비 → 코드 배포 → 실행·수집 → VM 종료.
  start_runner
  sync_sources
  run_remote_scenario smoke.js
}

show_status() {
  gcloud_compute instances describe "$(instance_name)" \
    --zone "$(instance_zone)" \
    --format='table(name,zone.basename(),status,machineType.basename(),networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)'
}

usage() {
  cat <<'EOF'
Usage: runner.sh <command> [arguments]

Commands:
  start             Start the runner and wait for k6, Ops Agent, and the shutdown timer.
  sync              Copy load-tests/k6 to the runner through IAP.
  smoke             Start, sync, run the 1 VU / 10s smoke test, collect, and stop.
  run <scenario>    Run a synced scenario, collect its results, and stop the runner.
  collect           Re-collect the most recent run.
  stop              Stop the runner.
  status            Show runner state and network addresses.
EOF
}

main() {
  require_command gcloud
  require_command jq
  require_command terraform
  if [[ -z "$PROJECT_ID" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
  fi
  [[ -n "$PROJECT_ID" && "$PROJECT_ID" != "(unset)" ]] || {
    printf 'PROJECT_ID or an active gcloud project is required.\n' >&2
    exit 1
  }

  case "${1:-}" in
    start) start_runner ;;
    sync) sync_sources ;;
    smoke) smoke ;;
    run)
      [[ -n "${2:-}" ]] || { usage >&2; exit 1; }
      run_remote_scenario "$2"
      ;;
    collect) collect_command ;;
    stop) stop_runner ;;
    status) show_status ;;
    *) usage; exit 1 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
