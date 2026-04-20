#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import e2e_backend_smoke_shared as SHARED


DEFAULT_CORE_API_BASE_URL = SHARED.DEFAULT_CORE_API_BASE_URL
DEFAULT_EMBEDDING_BASE_URL = SHARED.DEFAULT_EMBEDDING_BASE_URL
DEFAULT_SEARCH_BASE_URL = SHARED.DEFAULT_SEARCH_BASE_URL

StepError = SHARED.StepError
StepTimer = SHARED.StepTimer
_print_step = SHARED._print_step
_run_timed = SHARED._run_timed
_process_video = SHARED._process_video
_run_search_queries = SHARED._run_search_queries
_validate_video_paths = SHARED._validate_video_paths
_load_scenario = SHARED._load_scenario
_prepare_compose_smoke = SHARED._prepare_compose_smoke
_preflight_existing_services = SHARED._preflight_existing_services
_cleanup_test_user_via_compose = SHARED._cleanup_test_user_via_compose


def run_smoke(args: argparse.Namespace) -> int:
    _validate_video_paths(args.video_paths)
    timer = StepTimer()
    log_dir, token = _prepare_compose_smoke(
        timer=timer,
        log_prefix="biblio-e2e-docker-logs-",
        core_api_base_url=args.core_api_base_url,
        embedding_base_url=args.embedding_base_url,
        search_base_url=args.search_base_url,
        user_id=args.user_id,
    )

    for index, video_path in enumerate(args.video_paths, start=1):
        _process_video(
            timer=timer,
            token=token,
            video_path=video_path,
            index=index,
            ready_timeout_sec=args.ready_timeout_sec,
            upload_timeout_sec=args.upload_timeout_sec,
        )

    _run_search_queries(timer=timer, token=token, queries=args.queries, log_dir=log_dir)
    print("\nDocker E2E smoke succeeded.", flush=True)
    print(timer.summary(), flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend E2E smoke against an already-running docker compose stack.")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--video-path", dest="video_paths", type=Path, action="append")
    parser.add_argument("--query", dest="queries", action="append")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--ready-timeout-sec", type=int, default=None)
    parser.add_argument("--upload-timeout-sec", type=int, default=None)
    parser.add_argument("--core-api-base-url", default=DEFAULT_CORE_API_BASE_URL)
    parser.add_argument("--embedding-base-url", default=DEFAULT_EMBEDDING_BASE_URL)
    parser.add_argument("--search-base-url", default=DEFAULT_SEARCH_BASE_URL)
    args = parser.parse_args(argv)

    scenario: dict[str, object] = {}
    if args.scenario is not None:
        scenario = _load_scenario(args.scenario)

    if not args.video_paths:
        args.video_paths = [Path(path) for path in scenario.get("video_paths", SHARED.DEFAULT_VIDEO_PATHS)]
    if not args.queries:
        args.queries = list(scenario.get("queries", SHARED.DEFAULT_QUERIES))
    if args.user_id is None:
        args.user_id = str(scenario.get("user_id", SHARED.DEFAULT_USER_ID))
    if args.ready_timeout_sec is None:
        args.ready_timeout_sec = int(scenario.get("ready_timeout_sec", 1800))
    if args.upload_timeout_sec is None:
        args.upload_timeout_sec = int(scenario.get("upload_timeout_sec", SHARED.DEFAULT_UPLOAD_TIMEOUT_SEC))
    return args


def main() -> int:
    try:
        return run_smoke(parse_args())
    except StepError as exc:
        print(f"\nDocker E2E smoke failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
