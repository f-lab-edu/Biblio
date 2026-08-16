#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from batch_embedding import BatchEmbeddingSession, BatchRunConfig
from embedding_target import TargetMonitor
from infrastructure import (
    CommandRunner,
    Infrastructure,
    LoadTestError,
    Settings,
    VIDEO_PIPELINE_ARTIFACT_TYPE,
)
from k6_runner import ArtifactManager, K6Runner, compact_utc_timestamp, utc_timestamp
from search_embedding import SearchEmbeddingSession, SearchRunConfig
from scripts.test_support.cloud_auth import user_identity_token_command
from scripts.test_support.http import JsonHttpClient, make_jwt
from scripts.test_support.video_api import VideoApiClient
from video_pipeline.artifacts import (
    remove_intermediate_artifacts,
    write_raw_log_archive,
    write_video_pipeline_artifacts,
)
from video_pipeline.environment import resolve_video_run_environment
from video_pipeline.fixtures import fixture_workload, load_fixture_manifest
from video_pipeline.observability import (
    WorkerLogDatasets,
    collect_worker_logs,
    parse_embedding_endpoint_log,
    select_run_traces,
)
from video_pipeline.models import ScenarioOverrides
from video_pipeline.monitoring import (
    collect_cloud_run_monitoring_samples,
)
from video_pipeline.scenarios import build_scenario_plan
from video_pipeline.session import ScenarioProgress, execute_scenario
from video_pipeline.timeline import (
    build_timeline,
    read_csv_samples,
    write_timeline_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Biblio k6 load-test VMs, runs, and artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("start", "Start the k6 runner and wait until it is ready."),
        ("sync", "Copy k6 sources and the remote executor to the runner."),
        ("smoke", "Run the search-service health smoke test."),
        ("collect", "Re-collect the most recent run."),
        ("stop", "Stop the k6 runner."),
        ("status", "Show the k6 runner state and network addresses."),
        ("search-embedding-stop", "Restore the search test session VM states."),
        ("batch-embedding-stop", "Restore the batch test session VM states."),
    ):
        subparsers.add_parser(command, help=help_text)

    run_parser = subparsers.add_parser("run", help="Run one already-synced k6 scenario.")
    run_parser.add_argument("scenario")

    start_parser = subparsers.add_parser(
        "search-embedding-start",
        help="Prepare and verify a multi-rate search embedding session.",
    )
    start_parser.add_argument("--model-version", required=True)

    search_run_parser = subparsers.add_parser(
        "search-embedding-run",
        help="Run one search embedding arrival rate.",
    )
    search_run_parser.add_argument("--rate", required=True, type=int)
    search_run_parser.add_argument("--time-unit", default="1s")
    search_run_parser.add_argument("--duration", default="2m")
    search_run_parser.add_argument("--client-timeout", default=15, type=int)
    search_run_parser.add_argument("--pre-allocated-vus", type=int)
    search_run_parser.add_argument("--max-vus", type=int)

    batch_start_parser = subparsers.add_parser(
        "batch-embedding-start",
        help="Prepare and verify an isolated batch embedding session.",
    )
    batch_start_parser.add_argument("--model-version", required=True)

    batch_run_parser = subparsers.add_parser(
        "batch-embedding-run",
        help="Run one batch endpoint capacity scenario.",
    )
    batch_run_parser.add_argument(
        "--scenario", choices=("capacity",), required=True
    )
    batch_run_parser.add_argument(
        "--input-set",
        choices=("capacity", "truncation", "observed-mix"),
        default="capacity",
    )
    batch_run_parser.add_argument("--input-bucket", default="balanced")
    batch_run_parser.add_argument("--content-profile", default="all")
    batch_run_parser.add_argument("--batch-size", type=int, default=4)
    batch_run_parser.add_argument("--client-timeout", type=int, default=180)
    batch_run_parser.add_argument("--verify-response", action="store_true")
    batch_run_parser.add_argument(
        "--response-verification", choices=("none", "sampled", "all"), default="none"
    )
    batch_run_parser.add_argument(
        "--retry-profile", choices=("raw", "worker-client"), default="raw"
    )
    batch_run_parser.add_argument("--retry-seed", type=int, default=104)
    batch_run_parser.add_argument("--graceful-stop", default="30s")
    batch_run_parser.add_argument("--vus", type=int, default=1)
    batch_run_parser.add_argument("--duration", default="2m")

    stress_parser = subparsers.add_parser(
        "batch-embedding-stress-run",
        help="Run one fixed VM-only batch embedding stress preset.",
    )
    stress_parser.add_argument("--preset", choices=("S1", "S2", "S3", "S4"), required=True)

    video_plan_parser = subparsers.add_parser(
        "video-pipeline-plan",
        help="Validate and print a video pipeline workload without sending requests.",
    )
    _add_video_pipeline_arguments(video_plan_parser)

    video_run_parser = subparsers.add_parser(
        "video-pipeline-run",
        help="Run one approved video pipeline workload.",
    )
    _add_video_pipeline_arguments(video_run_parser)
    video_run_parser.add_argument("--biblio-project-id", required=True)
    video_run_parser.add_argument("--terminal-timeout", type=float, default=7200.0)
    video_run_parser.add_argument("--poll-interval", type=float, default=5.0)
    video_run_parser.add_argument("--run-id")
    video_run_parser.add_argument("--cloud-run-auth", action="store_true")
    video_run_parser.add_argument("--gcp-project-id")
    video_run_parser.add_argument("--cloud-monitoring-samples", type=Path)
    video_run_parser.add_argument(
        "--embedding-vm-samples",
        type=Path,
        help="Use an existing embedding VM sampler TSV instead of collecting one.",
    )
    video_run_parser.add_argument("--monitoring-settle-seconds", type=float, default=120.0)
    video_run_parser.add_argument("--log-window-grace-seconds", type=float, default=5.0)
    return parser


def _add_video_pipeline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preset", choices=("S1", "S2", "S3", "S4"), required=True)
    parser.add_argument("--fixtures-manifest", required=True, type=Path)
    parser.add_argument("--repeat-count", type=int)
    parser.add_argument("--request-count", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--fixture", choices=("short", "medium", "long"))
    parser.add_argument("--phase-delay", type=float)


def search_run_config(arguments: argparse.Namespace) -> SearchRunConfig:
    return SearchRunConfig(
        rate=arguments.rate,
        time_unit=arguments.time_unit,
        duration=arguments.duration,
        client_timeout_seconds=arguments.client_timeout,
        pre_allocated_vus=arguments.pre_allocated_vus,
        max_vus=arguments.max_vus,
    ).validated()


def batch_run_config(arguments: argparse.Namespace) -> BatchRunConfig:
    return BatchRunConfig(
        scenario=arguments.scenario,
        input_set=arguments.input_set,
        input_bucket=arguments.input_bucket,
        content_profile=arguments.content_profile,
        batch_size=arguments.batch_size,
        client_timeout_seconds=arguments.client_timeout,
        response_verification=(
            "all" if arguments.verify_response else arguments.response_verification
        ),
        retry_profile=arguments.retry_profile,
        retry_seed=arguments.retry_seed,
        graceful_stop=arguments.graceful_stop,
        vus=arguments.vus,
        duration=arguments.duration,
    ).validated()


def run_with_signal_cleanup(
    operation: Callable[[], object],
    session: SearchEmbeddingSession | BatchEmbeddingSession,
) -> None:
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}

    def cleanup(signum: int, _frame) -> None:
        print("Signal received; restoring the embedding test session.", file=sys.stderr)
        try:
            session.stop()
        finally:
            raise SystemExit(128 + signum)

    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[handled_signal] = signal.signal(handled_signal, cleanup)
    try:
        operation()
    finally:
        for handled_signal, previous_handler in previous_handlers.items():
            signal.signal(handled_signal, previous_handler)


def dispatch(arguments: argparse.Namespace) -> None:
    if arguments.command in {"video-pipeline-plan", "video-pipeline-run"}:
        _dispatch_video_pipeline(arguments)
        return
    run_config = (
        search_run_config(arguments)
        if arguments.command == "search-embedding-run"
        else None
    )
    batch_config = (
        batch_run_config(arguments)
        if arguments.command == "batch-embedding-run"
        else BatchRunConfig.stress(arguments.preset)
        if arguments.command == "batch-embedding-stress-run"
        else None
    )
    settings = Settings.from_environment()
    commands = CommandRunner()
    infrastructure = Infrastructure(settings, commands)
    infrastructure.prepare()
    artifacts = ArtifactManager(settings, infrastructure)
    k6_runner = K6Runner(settings, commands, infrastructure, artifacts)
    session = SearchEmbeddingSession(
        settings, commands, infrastructure, k6_runner, artifacts
    )
    batch_session = (
        BatchEmbeddingSession(
            settings, commands, infrastructure, k6_runner, artifacts
        )
        if arguments.command.startswith("batch-embedding-")
        else None
    )
    operations: dict[str, Callable[[], object]] = {
        "start": infrastructure.start_runner,
        "sync": k6_runner.sync_sources,
        "smoke": k6_runner.smoke,
        "collect": k6_runner.collect_latest,
        "stop": infrastructure.stop_runner,
        "status": infrastructure.show_runner_status,
        "search-embedding-stop": session.stop,
    }
    if arguments.command == "run":
        k6_runner.run_from_environment(arguments.scenario)
    elif arguments.command == "search-embedding-start":
        run_with_signal_cleanup(lambda: session.start(arguments.model_version), session)
    elif arguments.command == "search-embedding-run":
        assert run_config is not None
        run_with_signal_cleanup(lambda: session.run(run_config), session)
    elif arguments.command == "batch-embedding-start":
        assert batch_session is not None
        run_with_signal_cleanup(
            lambda: batch_session.start(arguments.model_version), batch_session
        )
    elif arguments.command in {"batch-embedding-run", "batch-embedding-stress-run"}:
        assert batch_session is not None and batch_config is not None
        run_with_signal_cleanup(
            lambda: batch_session.run(batch_config), batch_session
        )
    elif arguments.command == "batch-embedding-stop":
        assert batch_session is not None
        batch_session.stop()
    else:
        operations[arguments.command]()


def _dispatch_video_pipeline(arguments: argparse.Namespace) -> None:
    plan = build_scenario_plan(
        arguments.preset,
        ScenarioOverrides(
            repeat_count=arguments.repeat_count,
            request_count=arguments.request_count,
            concurrency=arguments.concurrency,
            fixture=arguments.fixture,
            phase_delay_seconds=arguments.phase_delay,
        ),
    )
    fixtures = load_fixture_manifest(arguments.fixtures_manifest)
    workload = fixture_workload(plan.phases, plan.repeat_count, fixtures)
    print(json.dumps({"plan": asdict(plan), "workload": workload}, default=str, indent=2))
    if arguments.command == "video-pipeline-plan":
        return

    if arguments.monitoring_settle_seconds < 0:
        raise LoadTestError("--monitoring-settle-seconds cannot be negative.")
    if arguments.log_window_grace_seconds < 0:
        raise LoadTestError("--log-window-grace-seconds cannot be negative.")

    if (
        arguments.embedding_vm_samples is not None
        and not arguments.embedding_vm_samples.is_file()
    ):
        raise LoadTestError(
            "--embedding-vm-samples must point to an existing target sampler TSV."
        )
    if (
        arguments.cloud_monitoring_samples is not None
        and not arguments.cloud_monitoring_samples.is_file()
    ):
        raise LoadTestError("--cloud-monitoring-samples does not exist.")

    settings = Settings.from_environment()
    commands = CommandRunner()
    infrastructure = Infrastructure(settings, commands)
    infrastructure.prepare()
    run_environment = resolve_video_run_environment(
        commands,
        infrastructure,
        biblio_project_id=arguments.biblio_project_id,
        requested_gcp_project_id=arguments.gcp_project_id,
    )
    identity_provider = _GCloudIdentityTokenProvider(commands)
    client = VideoApiClient(
        run_environment.core_api_url,
        JsonHttpClient(
            application_token_provider=_ApplicationJwtProvider(
                run_environment.requester_user_id,
                run_environment.app_jwt_secret,
            ),
            identity_token_provider=identity_provider,
            use_cloud_run_identity_token=arguments.cloud_run_auth,
        ),
    )
    run_id = arguments.run_id or f"{compact_utc_timestamp()}-video-{plan.preset.lower()}"
    result_directory = settings.artifact_run_directory(
        VIDEO_PIPELINE_ARTIFACT_TYPE,
        run_id,
    )
    result_directory.mkdir(parents=True, exist_ok=True)
    progress = ScenarioProgress()
    workload_errors: list[str] = []
    observability_errors: list[str] = []
    runtime_config: dict[str, object] = {}
    sampler_monitor, sampler_artifacts, embedding_vm_samples_path = (
        _start_video_embedding_sampler(
            arguments.embedding_vm_samples,
            settings,
            infrastructure,
            run_id,
            result_directory,
        )
    )
    if sampler_monitor is not None:
        try:
            runtime_config["embedding_vm"] = sampler_monitor.deployment_snapshot()
        except Exception as error:
            observability_errors.append(
                f"Embedding VM runtime configuration collection failed: {error}"
            )
    try:
        runtime_config["pipeline_worker"] = _worker_runtime_config(
            commands,
            run_environment.gcp_project_id,
        )
    except Exception as error:
        observability_errors.append(
            f"Pipeline Worker runtime configuration collection failed: {error}"
        )
    started_at = utc_timestamp()
    try:
        execute_scenario(
            client,
            plan,
            fixtures,
            project_id=arguments.biblio_project_id,
            run_label=run_id,
            terminal_timeout_seconds=arguments.terminal_timeout,
            poll_interval_seconds=arguments.poll_interval,
            progress=progress,
        )
    except Exception as error:
        workload_errors.append(str(error))
    finally:
        finished_at = utc_timestamp()
        observability_errors.extend(
            _finish_video_embedding_sampler(
                sampler_monitor,
                sampler_artifacts,
                run_id,
            )
        )
    time.sleep(arguments.monitoring_settle_seconds)
    log_query_end_at = _timestamp_after(
        finished_at,
        arguments.log_window_grace_seconds,
    )
    datasets = WorkerLogDatasets((), (), (), ())
    cloud_monitoring_samples: tuple[dict[str, object], ...] = ()
    embedding_vm_samples: tuple[dict[str, object], ...] = ()
    endpoint_events: tuple[dict[str, object], ...] = ()
    coverage: dict[str, object] = {}
    try:
        datasets = select_run_traces(
            collect_worker_logs(
                commands,
                project_id=run_environment.gcp_project_id,
                start_time=started_at,
                end_time=log_query_end_at,
            ),
            {record.trace_id for record in progress.requests},
        )
        cloud_monitoring_samples = _cloud_monitoring_samples(
            arguments,
            commands,
            run_environment.gcp_project_id,
            started_at,
            log_query_end_at,
        )
        embedding_vm_samples = read_csv_samples(
            embedding_vm_samples_path,
            source="embedding-vm",
            delimiter="\t",
        )
        endpoint_events = parse_embedding_endpoint_log(
            result_directory / "target-vm" / "endpoint.log",
            trace_ids={record.trace_id for record in progress.requests},
        )
        _validate_collected_observability(
            datasets,
            embedding_vm_samples,
            endpoint_events,
        )
        timeline_rows, coverage = build_timeline(
            stage_events=datasets.stage_events,
            queue_samples=datasets.queue_samples,
            resource_samples=(
                *datasets.worker_process_samples,
                *cloud_monitoring_samples,
                *embedding_vm_samples,
            ),
        )
        write_timeline_artifacts(result_directory, timeline_rows)
    except Exception as error:
        observability_errors.append(str(error))

    all_samples = (
        *datasets.samples,
        *cloud_monitoring_samples,
        *embedding_vm_samples,
    )
    run_metadata = {
        "artifact_schema_version": 2,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": _run_status(workload_errors, observability_errors),
        "workload_status": "failed" if workload_errors else "complete",
        "observability_status": (
            "incomplete" if observability_errors else "complete"
        ),
        "observability_errors": observability_errors,
        "collection": {
            "monitoring_settle_seconds": arguments.monitoring_settle_seconds,
            "log_window_grace_seconds": arguments.log_window_grace_seconds,
            "log_query_end_at": log_query_end_at,
            "event_count": len(datasets.events) + len(endpoint_events),
            "sample_count": len(all_samples),
        },
        "runtime_config": runtime_config,
        "resource_coverage": coverage,
        "target_vm_summary": _read_json_object(
            result_directory / "target-vm" / "target-metrics.json"
        ),
        "plan": asdict(plan),
        "workload": workload,
        "git_sha": commands.output(["git", "rev-parse", "HEAD"]),
    }
    write_video_pipeline_artifacts(
        result_directory,
        run_metadata=run_metadata,
        fixture_manifest={kind: asdict(fixture) for kind, fixture in fixtures.items()},
        requests=tuple(progress.requests),
        terminal_statuses=tuple(progress.terminal_statuses),
        pipeline_timings=datasets.pipeline_timings,
        events=(*datasets.events, *endpoint_events),
        samples=all_samples,
        errors=tuple((*workload_errors, *observability_errors)),
    )
    write_raw_log_archive(
        result_directory,
        worker_entries=datasets.raw_entries,
        endpoint_log_path=result_directory / "target-vm" / "endpoint.log",
        sampler_log_path=result_directory / "target-vm" / "sampler-console.log",
    )
    remove_intermediate_artifacts(result_directory)
    print(f"Video pipeline results: {result_directory}")
    all_errors = (*workload_errors, *observability_errors)
    if all_errors:
        raise LoadTestError("Video pipeline run incomplete: " + " | ".join(all_errors))


def _cloud_monitoring_samples(
    arguments: argparse.Namespace,
    commands: CommandRunner,
    project_id: str,
    start_time: str,
    end_time: str,
) -> tuple[dict[str, object], ...]:
    if arguments.cloud_monitoring_samples is not None:
        return read_csv_samples(
            arguments.cloud_monitoring_samples,
            source="cloud-monitoring",
        )
    return collect_cloud_run_monitoring_samples(
        commands,
        project_id=project_id,
        service_name="pipeline-worker",
        start_time=start_time,
        end_time=end_time,
    )


def _timestamp_after(timestamp: str, seconds: float) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _run_status(workload_errors: list[str], observability_errors: list[str]) -> str:
    if workload_errors:
        return "failed"
    if observability_errors:
        return "incomplete"
    return "complete"


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _worker_runtime_config(
    commands: CommandRunner,
    project_id: str,
) -> dict[str, object]:
    raw_service = commands.output(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            "pipeline-worker",
            "--project",
            project_id,
            "--region",
            "asia-northeast3",
            "--format=json",
        ]
    )
    service = json.loads(raw_service)
    template = service.get("spec", {}).get("template", {})
    template_spec = template.get("spec", {})
    containers = template_spec.get("containers", [])
    container = containers[0] if containers else {}
    metadata = template.get("metadata", {})
    safe_environment_names = {
        "WORKER_CONCURRENCY",
        "QUEUE_VISIBILITY_TIMEOUT_SEC",
        "DELETE_QUEUE_VISIBILITY_TIMEOUT_SEC",
        "STALE_PROCESSING_RECLAIM_SEC",
        "STT_PART_CONCURRENCY",
        "STT_SUBMIT_TIMEOUT_SEC",
        "STT_OPERATION_TIMEOUT_SEC",
        "EMBEDDING_TIMEOUT_SEC",
        "EMBEDDING_BATCH_SIZE",
        "CHUNK_MAX_TOKENS",
        "QUEUE_SAMPLE_INTERVAL_SEC",
        "WORKER_PROCESS_SAMPLE_INTERVAL_SEC",
    }
    environment = {
        str(item["name"]): item.get("value")
        for item in container.get("env", [])
        if item.get("name") in safe_environment_names
    }
    return {
        "latest_ready_revision": service.get("status", {}).get(
            "latestReadyRevisionName"
        ),
        "container_image": container.get("image"),
        "container_concurrency": template_spec.get("containerConcurrency"),
        "resources": container.get("resources", {}),
        "annotations": metadata.get("annotations", {}),
        "environment": environment,
    }


def _start_video_embedding_sampler(
    provided_samples: Path | None,
    settings: Settings,
    infrastructure: Infrastructure,
    run_id: str,
    result_directory: Path,
) -> tuple[TargetMonitor | None, ArtifactManager | None, Path]:
    if provided_samples is not None:
        return None, None, provided_samples

    monitor = TargetMonitor(
        settings,
        infrastructure,
        target_name=infrastructure.batch_target_name,
        target_zone=infrastructure.batch_target_zone,
    )
    artifacts = ArtifactManager(settings, infrastructure)
    _start_target_monitor(monitor, run_id)
    return monitor, artifacts, result_directory / "target-vm" / "target-samples.tsv"


def _start_target_monitor(monitor: TargetMonitor, run_id: str) -> None:
    try:
        monitor.start(run_id)
    except Exception as start_error:
        try:
            monitor.stop(run_id)
        except Exception as stop_error:
            raise LoadTestError(
                "Embedding VM sampler start failed and cleanup also failed: "
                f"{start_error} | {stop_error}"
            ) from start_error
        raise


def _finish_video_embedding_sampler(
    monitor: TargetMonitor | None,
    artifacts: ArtifactManager | None,
    run_id: str,
) -> list[str]:
    if monitor is None or artifacts is None:
        return []

    errors: list[str] = []
    try:
        monitor.stop(run_id)
    except Exception as error:
        errors.append(f"Embedding VM sampler stop failed: {error}")
    try:
        monitor.collect_raw_endpoint_log(run_id)
    except Exception as error:
        errors.append(f"Embedding endpoint log collection failed: {error}")
    try:
        artifacts.collect_target_sampler_results(
            run_id,
            test_type=VIDEO_PIPELINE_ARTIFACT_TYPE,
            target_name=monitor.target_name,
            target_zone=monitor.target_zone,
        )
    except Exception as error:
        errors.append(f"Embedding VM sampler collection failed: {error}")
    return errors


def _validate_collected_observability(
    datasets: WorkerLogDatasets,
    embedding_vm_samples: tuple[dict[str, object], ...],
    endpoint_events: tuple[dict[str, object], ...],
) -> None:
    worker_event_types = {
        str(row.get("event_type", "")) for row in datasets.events
    }
    endpoint_event_types = {
        str(row.get("event_type", "")) for row in endpoint_events
    }
    required_datasets = {
        "pipeline stage events": datasets.stage_events,
        "pipeline timings": datasets.pipeline_timings,
        "queue samples": datasets.queue_samples,
        "worker process samples": datasets.worker_process_samples,
        "embedding VM samples": embedding_vm_samples,
        "PGMQ dispatch events": "queue.message.started" in worker_event_types,
        "Worker embedding events": any(
            event_type.startswith("embedding.request.")
            for event_type in worker_event_types
        ),
        "STT request events": any(
            event_type.startswith("stt.request.") for event_type in worker_event_types
        ),
        "enrichment step events": "enrichment.step" in worker_event_types,
        "embedding admission events": "embedding.admission" in endpoint_event_types,
    }
    missing = [name for name, rows in required_datasets.items() if not rows]
    if missing:
        raise LoadTestError(
            "Video pipeline observability is incomplete: " + ", ".join(missing)
        )


class _GCloudIdentityTokenProvider:
    def __init__(self, commands: CommandRunner) -> None:
        self._commands = commands

    def identity_token(self, _audience: str) -> str:
        return self._commands.output(user_identity_token_command())


class _ApplicationJwtProvider:
    def __init__(self, requester_user_id: str, secret: str) -> None:
        self._requester_user_id = requester_user_id
        self._secret = secret

    def application_token(self) -> str:
        return make_jwt(
            requester_user_id=self._requester_user_id,
            secret=self._secret,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        dispatch(arguments)
    except LoadTestError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
