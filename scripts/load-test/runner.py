#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable

from infrastructure import CommandRunner, Infrastructure, LoadTestError, Settings
from k6_runner import ArtifactManager, K6Runner
from search_embedding import SearchEmbeddingSession, SearchRunConfig


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
    return parser


def search_run_config(arguments: argparse.Namespace) -> SearchRunConfig:
    return SearchRunConfig(
        rate=arguments.rate,
        time_unit=arguments.time_unit,
        duration=arguments.duration,
        client_timeout_seconds=arguments.client_timeout,
        pre_allocated_vus=arguments.pre_allocated_vus,
        max_vus=arguments.max_vus,
    ).validated()


def run_with_signal_cleanup(
    operation: Callable[[], object], session: SearchEmbeddingSession
) -> None:
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}

    def cleanup(signum: int, _frame) -> None:
        print("Signal received; restoring the search embedding session.", file=sys.stderr)
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
    run_config = (
        search_run_config(arguments)
        if arguments.command == "search-embedding-run"
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
    else:
        operations[arguments.command]()


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
