#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from batch_embedding import BatchEmbeddingSession, BatchRunConfig
from infrastructure import CommandRunner, Infrastructure, LoadTestError, Settings
from k6_runner import ArtifactManager, K6Runner, compact_utc_timestamp, utc_timestamp
from search_embedding import SearchEmbeddingSession, SearchRunConfig
from scripts.test_support.http import JsonHttpClient, make_jwt
from scripts.test_support.video_api import VideoApiClient
from video_pipeline.artifacts import write_video_pipeline_artifacts
from video_pipeline.fixtures import fixture_workload, load_fixture_manifest
from video_pipeline.models import ScenarioOverrides
from video_pipeline.scenarios import build_scenario_plan
from video_pipeline.session import execute_scenario


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

    settings = Settings.from_environment()
    commands = CommandRunner()
    app_jwt_secret = _required_environment("APP_JWT_SECRET")
    requester_user_id = _required_environment("REQUESTER_USER_ID")
    core_api_url = _required_environment("CORE_API_URL")
    identity_provider = _GCloudIdentityTokenProvider(commands)
    client = VideoApiClient(
        core_api_url,
        JsonHttpClient(
            application_token_provider=_ApplicationJwtProvider(
                requester_user_id,
                app_jwt_secret,
            ),
            identity_token_provider=identity_provider,
            use_cloud_run_identity_token=arguments.cloud_run_auth,
        ),
    )
    run_id = arguments.run_id or f"{compact_utc_timestamp()}-video-{plan.preset.lower()}"
    started_at = utc_timestamp()
    requests, terminal_statuses = execute_scenario(
        client,
        plan,
        fixtures,
        project_id=arguments.biblio_project_id,
        run_label=run_id,
        terminal_timeout_seconds=arguments.terminal_timeout,
        poll_interval_seconds=arguments.poll_interval,
    )
    result_directory = settings.artifact_root / run_id / "video-pipeline"
    write_video_pipeline_artifacts(
        result_directory,
        run_metadata={
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": utc_timestamp(),
            "plan": asdict(plan),
            "workload": workload,
            "git_sha": commands.output(["git", "rev-parse", "HEAD"]),
        },
        fixture_manifest={kind: asdict(fixture) for kind, fixture in fixtures.items()},
        requests=requests,
        terminal_statuses=terminal_statuses,
    )
    print(f"Video pipeline results: {result_directory}")


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LoadTestError(f"{name} is required for video-pipeline-run.")
    return value


class _GCloudIdentityTokenProvider:
    def __init__(self, commands: CommandRunner) -> None:
        self._commands = commands

    def identity_token(self, audience: str) -> str:
        return self._commands.output(
            ["gcloud", "auth", "print-identity-token", "--audiences", audience]
        )


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
