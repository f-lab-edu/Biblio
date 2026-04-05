#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path("/mnt/c/Users/ASUS/project/Biblio")
CORE_API_DIR = ROOT / "services" / "core-api"
WORKER_DIR = ROOT / "services" / "pipeline-worker"
SEARCH_DIR = ROOT / "services" / "search-service"
EMBEDDING_DIR = ROOT / "services" / "managed-embedding-endpoint"
DB_DIR = ROOT / "infra" / "e2e-db"

DEFAULT_VIDEO_PATHS = [
    Path("/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4"),
    Path("/mnt/c/Users/ASUS/Downloads/가게에서 팔아도 되는 싱크로율 90% 지코바 양념치킨 레시피 대공개! 지코바 사장님은 보지마세요.mp4"),
]
DEFAULT_QUERIES = [
    "도커가 왜 필요한지 설명해줘",
    "지코바 양념치킨 레시피 핵심을 설명해줘",
    "도커 관련 내용만 요약해줘",
]
DEFAULT_USER_ID = "11111111-1111-1111-1111-111111111111"
DEFAULT_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:55433/app"
DEFAULT_DB_CONTAINER = "biblio-e2e-db"
DEFAULT_DB_IMAGE = "biblio-e2e-db"


@dataclass(slots=True)
class ServiceProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path


class StepError(RuntimeError):
    pass


class StepTimer:
    def __init__(self) -> None:
        self._started_at: dict[str, float] = {}
        self.durations: dict[str, float] = {}

    def start(self, name: str) -> None:
        self._started_at[name] = time.perf_counter()

    def stop(self, name: str) -> float:
        started_at = self._started_at.pop(name, None)
        if started_at is None:
            raise StepError(f"Step timer was not started: {name}")
        duration = time.perf_counter() - started_at
        self.durations[name] = duration
        return duration

    def summary(self) -> str:
        if not self.durations:
            return "No timings recorded."
        lines = ["\nStep timing summary:"]
        for name, duration in self.durations.items():
            lines.append(f"- {name}: {duration:.2f}s")
        return "\n".join(lines)


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


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def _print_step(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def _run_timed(timer: StepTimer, step_name: str, fn, *args, **kwargs):
    timer.start(step_name)
    try:
        return fn(*args, **kwargs)
    finally:
        duration = timer.stop(step_name)
        print(f"[timing] {step_name}: {duration:.2f}s", flush=True)


def _docker_build(db_image: str) -> None:
    _print_step("Build E2E DB image")
    _run(["docker", "build", "-t", db_image, str(DB_DIR)])


def _docker_reset(db_container: str, db_image: str) -> None:
    _print_step("Start E2E DB container")
    _run(["docker", "stop", db_container], check=False)
    _run(["docker", "rm", db_container], check=False)
    _run([
        "docker",
        "run",
        "-d",
        "--name",
        db_container,
        "-e",
        "POSTGRES_USER=postgres",
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=app",
        "-p",
        "55433:5432",
        db_image,
    ])
    for _ in range(30):
        result = _run(
            ["docker", "exec", db_container, "pg_isready", "-U", "postgres", "-d", "app"],
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


def _read_core_api_secret() -> str:
    env_path = CORE_API_DIR / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("JWT_SECRET_KEY="):
            return line.split("=", 1)[1].strip()
    raise StepError(f"JWT_SECRET_KEY not found in {env_path}")


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


def _wait_for_health(name: str, url: str, timeout_sec: int) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            response = _http_request("GET", url)
            if response["status"] == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise StepError(f"{name} health check timed out: {url}")


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    data = None
    final_headers = headers.copy() if headers else {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=final_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            return {
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                "body": body,
                "json": json.loads(body) if body else None,
            }
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return {
            "status": exc.code,
            "headers": dict(exc.headers.items()),
            "body": body,
            "json": json.loads(body) if body else None,
        }


def _upload_file(signed_url: str, file_path: Path, timeout_sec: int = 120) -> int:
    with file_path.open("rb") as file_obj:
        data = file_obj.read()
    req = request.Request(
        signed_url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": "application/octet-stream",
            "x-goog-content-length-range": "0,2147483648",
        },
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.status


def _make_token(user_id: str) -> str:
    secret = _read_core_api_secret()
    python_code = f"""
from datetime import datetime, timedelta, timezone
import jwt
payload = {{
    "requester_user_id": "{user_id}",
    "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
}}
print(jwt.encode(payload, "{secret}", algorithm="HS256"))
""".strip()
    result = _run([str(CORE_API_DIR / ".venv" / "bin" / "python"), "-c", python_code], cwd=CORE_API_DIR)
    return result.stdout.strip()


def _poll_video_ready(video_id: str, token: str, timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        response = _http_request(
            "GET",
            f"http://localhost:8080/api/v1/videos/{video_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response["status"] != 200:
            raise StepError(f"Video polling failed: status={response['status']} body={response['body']}")
        payload = response["json"] or {}
        last_payload = payload
        status = payload.get("status")
        if status == "READY":
            return payload
        if status == "FAILED":
            raise StepError(f"Video processing failed: {json.dumps(payload, ensure_ascii=False)}")
        time.sleep(5)
    raise StepError(f"Video did not become READY in time. Last payload={json.dumps(last_payload, ensure_ascii=False)}")


def _assert_search(token: str, query: str) -> dict[str, Any]:
    response = _http_request(
        "POST",
        "http://localhost:8082/api/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        json_body={"query": query},
        timeout_sec=60,
    )
    if response["status"] != 200:
        raise StepError(f"Search failed: status={response['status']} body={response['body']}")
    payload = response["json"] or {}
    if not payload.get("answer"):
        raise StepError(f"Search answer missing: {json.dumps(payload, ensure_ascii=False)}")
    if not payload.get("chunks"):
        raise StepError(f"Search chunks missing: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def _terminate_services(services: list[ServiceProcess]) -> None:
    for service in services:
        if service.process.poll() is not None:
            continue
        try:
            os.killpg(service.process.pid, signal.SIGTERM)
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
                os.killpg(service.process.pid, signal.SIGKILL)
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


def _write_json_artifact(log_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    output_path = log_dir / name
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _start_services(log_dir: Path) -> list[ServiceProcess]:
    _print_step("Start services")
    services = [
        _start_process(
            "embedding",
            ["poetry", "run", "uvicorn", "src.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"],
            EMBEDDING_DIR,
            log_dir,
        ),
        _start_process(
            "core-api",
            ["poetry", "run", "uvicorn", "src.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"],
            CORE_API_DIR,
            log_dir,
        ),
        _start_process(
            "pipeline-worker",
            ["poetry", "run", "python", "-m", "src.main"],
            WORKER_DIR,
            log_dir,
        ),
        _start_process(
            "search-service",
            ["poetry", "run", "uvicorn", "src.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8082"],
            SEARCH_DIR,
            log_dir,
        ),
    ]
    _wait_for_health("embedding", "http://localhost:8000/health", timeout_sec=240)
    _wait_for_health("core-api", "http://localhost:8080/health", timeout_sec=60)
    _wait_for_health("search-service", "http://localhost:8082/health", timeout_sec=60)
    time.sleep(2)
    return services


def run_smoke(args: argparse.Namespace) -> int:
    for video_path in args.video_paths:
        if not video_path.exists():
            raise StepError(f"Test video not found: {video_path}")

    log_dir = Path(tempfile.mkdtemp(prefix="biblio-e2e-logs-"))
    timer = StepTimer()
    print(f"Logs: {log_dir}", flush=True)
    services: list[ServiceProcess] = []
    try:
        if not args.skip_db_build:
            _run_timed(timer, "db_build", _docker_build, args.db_image)
        _run_timed(timer, "db_start", _docker_reset, args.db_container, args.db_image)
        _run_timed(timer, "core_api_migration", _run_migration, args.database_url)
        _run_timed(timer, "stale_video_cleanup", _cleanup_test_user, args.db_container, args.user_id)
        services = _run_timed(timer, "service_startup", _start_services, log_dir)

        _print_step("Create JWT")
        token = _run_timed(timer, "jwt_issue", _make_token, args.user_id)

        ready_payloads: list[dict[str, Any]] = []
        for index, video_path in enumerate(args.video_paths, start=1):
            _print_step(f"Create video #{index}")
            create_response = _run_timed(
                timer,
                f"video_create_{index}",
                _http_request,
                "POST",
                "http://localhost:8080/api/v1/videos",
                headers={"Authorization": f"Bearer {token}"},
                json_body={
                    "title": video_path.stem[:120],
                    "category": "GENERAL",
                    "input_type": "LOCAL_FILE",
                    "extension": video_path.suffix or ".mp4",
                },
            )
            if create_response["status"] not in (200, 201, 202):
                raise StepError(f"Create video failed: {create_response['status']} {create_response['body']}")
            create_payload = create_response["json"] or {}
            video_id = create_payload["video_id"]
            signed_url = create_payload["signed_url"]
            print(f"VIDEO_ID_{index}={video_id}", flush=True)

            _print_step(f"Upload test video #{index}")
            upload_status = _run_timed(
                timer,
                f"video_upload_{index}",
                _upload_file,
                signed_url,
                video_path,
            )
            if upload_status not in (200, 201):
                raise StepError(f"Upload failed with status {upload_status}")

            _print_step(f"Complete upload #{index}")
            complete_response = _run_timed(
                timer,
                f"video_complete_{index}",
                _http_request,
                "POST",
                f"http://localhost:8080/api/v1/videos/{video_id}/complete",
                headers={"Authorization": f"Bearer {token}"},
                json_body={},
            )
            if complete_response["status"] not in (200, 202):
                raise StepError(f"Complete failed: {complete_response['status']} {complete_response['body']}")

            _print_step(f"Poll READY #{index}")
            ready_payload = _run_timed(
                timer,
                f"video_ready_poll_{index}",
                _poll_video_ready,
                video_id,
                token,
                timeout_sec=args.ready_timeout_sec,
            )
            print(json.dumps(ready_payload, ensure_ascii=False, indent=2), flush=True)
            ready_payloads.append(ready_payload)

        for index, query in enumerate(args.queries, start=1):
            _print_step(f"Search #{index}")
            search_payload = _run_timed(
                timer,
                f"search_request_{index}",
                _assert_search,
                token,
                query,
            )
            output_path = _write_json_artifact(
                log_dir,
                f"search_{index}.json",
                search_payload,
            )
            print(f"QUERY_{index}={query}", flush=True)
            print(json.dumps(search_payload, ensure_ascii=False, indent=2), flush=True)
            print(f"SEARCH_OUTPUT_{index}={output_path}", flush=True)

        print("\nE2E smoke succeeded.", flush=True)
        print(timer.summary(), flush=True)
        print(_read_worker_timing_summary(services), flush=True)
        return 0
    finally:
        if services and not args.keep_services:
            _terminate_services(services)
            print("Services stopped.", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend E2E smoke flow against real infra.")
    parser.add_argument("--video-path", dest="video_paths", type=Path, action="append")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--query", dest="queries", action="append")
    parser.add_argument("--database-url", default=DEFAULT_DB_URL)
    parser.add_argument("--db-container", default=DEFAULT_DB_CONTAINER)
    parser.add_argument("--db-image", default=DEFAULT_DB_IMAGE)
    parser.add_argument("--ready-timeout-sec", type=int, default=1800)
    parser.add_argument("--skip-db-build", action="store_true")
    parser.add_argument("--keep-services", action="store_true")
    args = parser.parse_args()
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
