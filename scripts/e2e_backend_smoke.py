#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import parse


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import e2e_backend_smoke_shared as SHARED


ROOT = SHARED.ROOT
CORE_API_DIR = SHARED.CORE_API_DIR
WORKER_DIR = SHARED.WORKER_DIR
SEARCH_DIR = SHARED.SEARCH_DIR
EMBEDDING_DIR = SHARED.EMBEDDING_DIR
DB_DIR = SHARED.DB_DIR

DEFAULT_VIDEO_PATHS = SHARED.DEFAULT_VIDEO_PATHS
DEFAULT_QUERIES = SHARED.DEFAULT_QUERIES
APP_FACTORY = SHARED.APP_FACTORY
DEFAULT_USER_ID = SHARED.DEFAULT_USER_ID
DEFAULT_DB_USER = SHARED.DEFAULT_DB_USER
DEFAULT_DB_PASSWORD = SHARED.DEFAULT_DB_PASSWORD
DEFAULT_DB_NAME = SHARED.DEFAULT_DB_NAME
DEFAULT_DB_PORT = SHARED.DEFAULT_DB_PORT
DEFAULT_DB_URL = SHARED.DEFAULT_DB_URL
DEFAULT_DB_CONTAINER = SHARED.DEFAULT_DB_CONTAINER
DEFAULT_DB_IMAGE = SHARED.DEFAULT_DB_IMAGE

StepError = SHARED.StepError
StepTimer = SHARED.StepTimer
_run = SHARED._run
_print_step = SHARED._print_step
_run_timed = SHARED._run_timed
_http_request = SHARED._http_request
_wait_for_health = SHARED._wait_for_health
_make_token = SHARED._make_token
_process_video = SHARED._process_video
_run_search_queries = SHARED._run_search_queries
_validate_video_paths = SHARED._validate_video_paths


@dataclass(slots=True)
class ServiceProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path


PIPELINE_TIMING_RE = re.compile(
    r"pipeline\.timing status=(?P<status>\w+) "
    r"download_ms=(?P<download_ms>[0-9.]+) "
    r"audio_ms=(?P<audio_ms>[0-9.]+) "
    r"stt_ms=(?P<stt_ms>[0-9.]+) "
    r"chunk_enrichment_ms=(?P<chunk_enrichment_ms>[0-9.]+) "
    r"embedding_ms=(?P<embedding_ms>[0-9.]+) "
    r"persist_ms=(?P<persist_ms>[0-9.]+) "
    r"total_ms=(?P<total_ms>[0-9.]+)"
)


def _docker_build(db_image: str) -> None:
    _print_step("Build E2E DB image")
    _run(["docker", "build", "-t", db_image, str(DB_DIR)])


def _db_container_settings(database_url: str) -> dict[str, str]:
    parsed = parse.urlsplit(database_url)
    db_name = parsed.path.lstrip("/") or DEFAULT_DB_NAME
    return {
        "POSTGRES_USER": parsed.username or DEFAULT_DB_USER,
        "POSTGRES_PASSWORD": parsed.password or DEFAULT_DB_PASSWORD,
        "POSTGRES_DB": db_name,
    }


def _docker_reset(db_container: str, db_image: str, database_url: str) -> None:
    _print_step("Start E2E DB container")
    db_settings = _db_container_settings(database_url)
    _run(["docker", "stop", db_container], check=False)
    _run(["docker", "rm", db_container], check=False)
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            db_container,
            "-e",
            f"POSTGRES_USER={db_settings['POSTGRES_USER']}",
            "-e",
            f"POSTGRES_PASSWORD={db_settings['POSTGRES_PASSWORD']}",
            "-e",
            f"POSTGRES_DB={db_settings['POSTGRES_DB']}",
            "-p",
            "55433:5432",
            db_image,
        ]
    )
    for _ in range(30):
        result = _run(
            ["docker", "exec", db_container, "pg_isready", "-U", db_settings["POSTGRES_USER"], "-d", db_settings["POSTGRES_DB"]],
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise StepError("DB container did not become ready in time.")


def _run_migration(db_url: str) -> None:
    _print_step("Apply core-api migration")
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    _run([str(CORE_API_DIR / ".venv" / "bin" / "alembic"), "upgrade", "head"], cwd=CORE_API_DIR, env=env)


def _cleanup_test_user(db_container: str, user_id: str) -> None:
    _print_step("Delete stale test-user videos")
    sql = f"""
DELETE FROM vector_index_entry
WHERE video_id IN (SELECT id FROM video WHERE user_id = '{user_id}');
DELETE FROM chunk
WHERE video_id IN (SELECT id FROM video WHERE user_id = '{user_id}');
DELETE FROM transcript_segment
WHERE video_id IN (SELECT id FROM video WHERE user_id = '{user_id}');
DELETE FROM asset
WHERE video_id IN (SELECT id FROM video WHERE user_id = '{user_id}');
DELETE FROM video
WHERE user_id = '{user_id}';
"""
    _run(["docker", "exec", "-i", db_container, "psql", "-U", "postgres", "-d", "app", "-c", sql])


def _start_process(name: str, cmd: list[str], cwd: Path, log_dir: Path) -> ServiceProcess:
    log_path = log_dir / f"{name}.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return ServiceProcess(name=name, process=process, log_path=log_path)


def _stop_service_process(process: subprocess.Popen[str], *, force: bool = False) -> bool:
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return False
    return True


def _terminate_services(services: list[ServiceProcess]) -> None:
    for service in services:
        if service.process.poll() is not None:
            continue
        try:
            _stop_service_process(service.process)
        except ProcessLookupError:
            continue
    deadline = time.time() + 10
    while time.time() < deadline:
        if all(service.process.poll() is not None for service in services):
            return
        time.sleep(0.5)
    for service in services:
        if service.process.poll() is None:
            try:
                _stop_service_process(service.process, force=True)
            except ProcessLookupError:
                pass


def _read_worker_timing_summary(services: list[ServiceProcess]) -> str:
    worker_service = next((service for service in services if service.name == "pipeline-worker"), None)
    if worker_service is None or not worker_service.log_path.exists():
        return "\nWorker timing summary:\n- unavailable"

    matches: list[re.Match[str]] = []
    for line in worker_service.log_path.read_text(encoding="utf-8").splitlines():
        match = PIPELINE_TIMING_RE.search(line)
        if match:
            matches.append(match)

    if not matches:
        return "\nWorker timing summary:\n- pipeline.timing log not found"

    latest = matches[-1].groupdict()
    lines = [
        "\nWorker timing summary:",
        f"- status: {latest['status']}",
        f"- download: {float(latest['download_ms']) / 1000:.2f}s",
        f"- audio: {float(latest['audio_ms']) / 1000:.2f}s",
        f"- stt: {float(latest['stt_ms']) / 1000:.2f}s",
        f"- chunk_enrichment: {float(latest['chunk_enrichment_ms']) / 1000:.2f}s",
        f"- embedding: {float(latest['embedding_ms']) / 1000:.2f}s",
        f"- persist: {float(latest['persist_ms']) / 1000:.2f}s",
        f"- total: {float(latest['total_ms']) / 1000:.2f}s",
    ]
    return "\n".join(lines)


def _build_uvicorn_cmd(*, port: int) -> list[str]:
    return [
        "poetry",
        "run",
        "uvicorn",
        APP_FACTORY,
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]


def _start_services(log_dir: Path) -> list[ServiceProcess]:
    _print_step("Start services")
    services = [
        _start_process("embedding", _build_uvicorn_cmd(port=8000), EMBEDDING_DIR, log_dir),
        _start_process("core-api", _build_uvicorn_cmd(port=8080), CORE_API_DIR, log_dir),
        _start_process("pipeline-worker", ["poetry", "run", "python", "-m", "src.main"], WORKER_DIR, log_dir),
        _start_process("search-service", _build_uvicorn_cmd(port=8082), SEARCH_DIR, log_dir),
    ]
    _wait_for_health("embedding", "http://localhost:8000/health", timeout_sec=240)
    _wait_for_health("core-api", "http://localhost:8080/health", timeout_sec=60)
    _wait_for_health("search-service", "http://localhost:8082/health", timeout_sec=60)
    time.sleep(2)
    return services


def _bootstrap_smoke_environment(
    args: argparse.Namespace,
    *,
    timer: StepTimer,
    log_dir: Path,
) -> list[ServiceProcess]:
    if not args.skip_db_build:
        _run_timed(timer, "db_build", _docker_build, args.db_image)
    _run_timed(timer, "db_start", _docker_reset, args.db_container, args.db_image, args.database_url)
    _run_timed(timer, "core_api_migration", _run_migration, args.database_url)
    _run_timed(timer, "stale_video_cleanup", _cleanup_test_user, args.db_container, args.user_id)
    return _run_timed(timer, "service_startup", _start_services, log_dir)


def run_smoke(args: argparse.Namespace) -> int:
    _validate_video_paths(args.video_paths)
    log_dir = Path(tempfile.mkdtemp(prefix="biblio-e2e-logs-"))
    timer = StepTimer()
    print(f"Logs: {log_dir}", flush=True)
    services: list[ServiceProcess] = []
    try:
        services = _bootstrap_smoke_environment(args, timer=timer, log_dir=log_dir)
        _print_step("Create JWT")
        token = _run_timed(timer, "jwt_issue", _make_token, args.user_id)
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
        print("\nE2E smoke succeeded.", flush=True)
        print(timer.summary(), flush=True)
        print(_read_worker_timing_summary(services), flush=True)
        return 0
    finally:
        if services and not args.keep_services:
            _terminate_services(services)
            print("Services stopped.", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend E2E smoke flow against real infra.")
    parser.add_argument("--video-path", dest="video_paths", type=Path, action="append")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--query", dest="queries", action="append")
    parser.add_argument("--database-url", default=DEFAULT_DB_URL)
    parser.add_argument("--db-container", default=DEFAULT_DB_CONTAINER)
    parser.add_argument("--db-image", default=DEFAULT_DB_IMAGE)
    parser.add_argument("--ready-timeout-sec", type=int, default=1800)
    parser.add_argument("--upload-timeout-sec", type=int, default=SHARED.DEFAULT_UPLOAD_TIMEOUT_SEC)
    parser.add_argument("--skip-db-build", action="store_true")
    parser.add_argument("--keep-services", action="store_true")
    args = parser.parse_args(argv)
    if not args.video_paths:
        args.video_paths = list(DEFAULT_VIDEO_PATHS)
    if not args.queries:
        args.queries = list(DEFAULT_QUERIES)
    return args


def main() -> int:
    try:
        return run_smoke(parse_args())
    except StepError as exc:
        print(f"\nE2E smoke failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
