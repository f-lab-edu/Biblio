#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import jwt


ROOT = Path(__file__).resolve().parents[1]
CORE_API_DIR = ROOT / "services" / "core-api"
WORKER_DIR = ROOT / "services" / "pipeline-worker"
SEARCH_DIR = ROOT / "services" / "search-service"
EMBEDDING_DIR = ROOT / "services" / "managed-embedding-endpoint"
DB_DIR = ROOT / "infra" / "e2e-db"

APP_FACTORY = "src.main:create_app"

DEFAULT_VIDEO_PATHS = [
    Path("/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4"),
    Path("/mnt/c/Users/ASUS/Downloads/가게에서 팔아도 되는 싱크로율 90% 지코바 양념치킨 레시피 대공개! 지코바 사장님은 보지마세요.mp4"),
]
DEFAULT_QUERIES = [
    "도커가 왜 필요한지 설명해줘",
    "이 영상 핵심 내용을 요약해줘",
]
DEFAULT_USER_ID = "11111111-1111-1111-1111-111111111111"

DEFAULT_DB_USER = "postgres"
DEFAULT_DB_NAME = "app"
DEFAULT_DB_PORT = 55433
DEFAULT_DB_CONTAINER = "biblio-e2e-db"
DEFAULT_DB_IMAGE = "biblio-e2e-db"

DEFAULT_CORE_API_BASE_URL = "http://localhost:8080"
DEFAULT_EMBEDDING_BASE_URL = "http://localhost:8081"
DEFAULT_SEARCH_BASE_URL = "http://localhost:8082"
DEFAULT_UPLOAD_TIMEOUT_SEC = 600

CORE_API_BASE_URL = DEFAULT_CORE_API_BASE_URL
EMBEDDING_BASE_URL = DEFAULT_EMBEDDING_BASE_URL
SEARCH_BASE_URL = DEFAULT_SEARCH_BASE_URL


class StepError(RuntimeError):
    pass


@dataclass(slots=True)
class StepTimer:
    timings: list[tuple[str, float]] = field(default_factory=list)

    def record(self, label: str, elapsed_sec: float) -> None:
        self.timings.append((label, elapsed_sec))

    def summary(self) -> str:
        if not self.timings:
            return "No timings recorded."
        lines = ["Timing summary:"]
        for label, elapsed_sec in self.timings:
            lines.append(f"- {label}: {elapsed_sec:.2f}s")
        return "\n".join(lines)


def _print_step(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=capture_output,
        check=check,
        input=input_text,
    )


def _run_timed(timer: StepTimer, label: str, fn, *args, **kwargs):
    started_at = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_sec = time.perf_counter() - started_at
    timer.record(label, elapsed_sec)
    print(f"[timing] {label}: {elapsed_sec:.2f}s", flush=True)
    return result


def _http_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_sec: int = 30,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"

    req = request.Request(url, data=data, method=method.upper(), headers=request_headers)
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw_body = resp.read().decode("utf-8")
            if not raw_body:
                return resp.status, None
            try:
                return resp.status, json.loads(raw_body)
            except json.JSONDecodeError:
                return resp.status, raw_body
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        payload: Any = raw_body
        if raw_body:
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                pass
        return exc.code, payload


def _wait_for_health(service_name: str, url: str, *, timeout_sec: int = 60) -> None:
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status_code, _ = _http_request("GET", url, timeout_sec=5)
            if status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - defensive polling path
            last_error = exc
        time.sleep(2)
    if last_error is not None:
        raise StepError(f"{service_name} health check failed: {last_error}")
    raise StepError(f"{service_name} did not become healthy in time: {url}")


def _health_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/health"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _read_env_value(key: str, *, env_files: list[Path]) -> str | None:
    value = os.getenv(key)
    if value:
        return value

    for env_file in env_files:
        if not env_file.exists():
            continue
        file_value = _read_env_file(env_file).get(key)
        if file_value:
            return file_value
    return None


def _load_scenario(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_db_password() -> str:
    password = _read_env_value("POSTGRES_PASSWORD", env_files=[ROOT / ".env"])
    if password:
        return password

    database_url = _read_env_value("DATABASE_URL", env_files=[CORE_API_DIR / ".env"])
    if database_url:
        parsed = parse.urlsplit(database_url)
        if parsed.password:
            return parsed.password

    raise StepError("POSTGRES_PASSWORD must be set in the environment or repository .env for E2E smoke.")


def default_database_url() -> str:
    password = default_db_password()
    return f"postgresql+asyncpg://{DEFAULT_DB_USER}:{password}@localhost:{DEFAULT_DB_PORT}/{DEFAULT_DB_NAME}"


def _make_token(user_id: str) -> str:
    jwt_secret_key = _read_env_file(CORE_API_DIR / ".env")["JWT_SECRET_KEY"]
    payload = {
        "requester_user_id": user_id,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, jwt_secret_key, algorithm="HS256")


def _validate_video_paths(video_paths: list[Path]) -> None:
    if not video_paths:
        raise StepError("At least one video path is required.")
    missing_paths = [str(path) for path in video_paths if not path.exists()]
    if missing_paths:
        raise StepError(f"Video not found: {', '.join(missing_paths)}")


def _create_local_file_video(token: str, video_path: Path, index: int) -> tuple[str, str]:
    extension = video_path.suffix.lower()
    if not extension:
        raise StepError(f"Video extension is required: {video_path}")

    payload = {
        "title": f"docker-e2e-{index}-{video_path.stem}",
        "category": "IT",
        "input_type": "LOCAL_FILE",
        "extension": extension,
    }
    status_code, body = _http_request(
        "POST",
        f"{CORE_API_BASE_URL}/api/v1/videos",
        token=token,
        json_body=payload,
    )
    if status_code != 201 or not isinstance(body, dict):
        raise StepError(f"Video create failed: status={status_code}, body={body}")
    return str(body["video_id"]), str(body["signed_url"])


def _upload_file(signed_url: str, file_path: Path, timeout_sec: int = DEFAULT_UPLOAD_TIMEOUT_SEC) -> int:
    result = _run(
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-X",
            "PUT",
            "-H",
            "Content-Type: application/octet-stream",
            "-H",
            "x-goog-content-length-range: 0,2147483648",
            "--upload-file",
            str(file_path),
            "-m",
            str(timeout_sec),
            signed_url,
        ],
        capture_output=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError as exc:  # pragma: no cover - defensive path
        raise StepError(f"Unexpected upload response: {result.stdout!r}") from exc


def _complete_local_file_video(token: str, video_id: str) -> dict[str, Any]:
    status_code, body = _http_request(
        "POST",
        f"{CORE_API_BASE_URL}/api/v1/videos/{video_id}/complete",
        token=token,
        json_body={},
    )
    if status_code not in {200, 202} or not isinstance(body, dict):
        raise StepError(f"Video complete failed: status={status_code}, body={body}")
    return body


def _fetch_video(token: str, video_id: str) -> dict[str, Any]:
    status_code, body = _http_request(
        "GET",
        f"{CORE_API_BASE_URL}/api/v1/videos/{video_id}",
        token=token,
    )
    if status_code != 200 or not isinstance(body, dict):
        raise StepError(f"Video fetch failed: status={status_code}, body={body}")
    return body


def _poll_video_ready(token: str, video_id: str, *, timeout_sec: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        body = _fetch_video(token, video_id)
        status = body.get("status")
        if status == "READY":
            return body
        if status == "FAILED":
            failed_stage = body.get("failed_stage")
            raise StepError(f"Video processing failed: video_id={video_id}, failed_stage={failed_stage}")
        time.sleep(5)
    raise StepError(f"Video did not become READY in time: video_id={video_id}")


def _run_search_queries(*, timer: StepTimer, token: str, queries: list[str], log_dir: Path) -> None:
    for index, query in enumerate(queries, start=1):
        _print_step(f"Search #{index}")

        def _search(current_query: str = query) -> dict[str, Any]:
            status_code, body = _http_request(
                "POST",
                f"{SEARCH_BASE_URL}/api/v1/search",
                token=token,
                json_body={"query": current_query},
                timeout_sec=60,
            )
            if status_code != 200 or not isinstance(body, dict):
                raise StepError(f"Search failed: status={status_code}, body={body}")
            return body

        response = _run_timed(timer, f"search_request_{index}", _search)
        print(f"QUERY_{index}={query}", flush=True)
        rendered = json.dumps(response, ensure_ascii=False, indent=2)
        print(rendered, flush=True)
        (log_dir / f"search_{index}.json").write_text(rendered, encoding="utf-8")


def _prepare_compose_smoke(
    *,
    timer: StepTimer,
    log_prefix: str,
    core_api_base_url: str,
    embedding_base_url: str,
    search_base_url: str,
    user_id: str,
) -> tuple[Path, str]:
    log_dir = Path(tempfile.mkdtemp(prefix=log_prefix))
    print(f"Logs: {log_dir}", flush=True)

    global CORE_API_BASE_URL, EMBEDDING_BASE_URL, SEARCH_BASE_URL
    CORE_API_BASE_URL = core_api_base_url.rstrip("/")
    EMBEDDING_BASE_URL = embedding_base_url.rstrip("/")
    SEARCH_BASE_URL = search_base_url.rstrip("/")

    _print_step("Preflight existing services")
    _run_timed(
        timer,
        "service_preflight",
        _preflight_existing_services,
        core_api_base_url=core_api_base_url,
        embedding_base_url=embedding_base_url,
        search_base_url=search_base_url,
    )

    _print_step("Delete stale test-user videos")
    _run_timed(timer, "stale_video_cleanup", _cleanup_test_user_via_compose, user_id=user_id)

    _print_step("Create JWT")
    token = _run_timed(timer, "jwt_issue", _make_token, user_id)
    return log_dir, token


def _process_video(
    *,
    timer: StepTimer,
    token: str,
    video_path: Path,
    index: int,
    ready_timeout_sec: int,
    upload_timeout_sec: int = DEFAULT_UPLOAD_TIMEOUT_SEC,
) -> None:
    _print_step("Create source video")
    video_id, signed_url = _run_timed(timer, f"video_create_{index}", _create_local_file_video, token, video_path, index)

    _print_step("Upload source video")
    upload_status = _run_timed(timer, f"video_upload_{index}", _upload_file, signed_url, video_path, upload_timeout_sec)
    if upload_status != 200:
        raise StepError(f"Video upload failed: status={upload_status}, video_id={video_id}")

    _print_step("Complete upload")
    _run_timed(timer, f"video_complete_{index}", _complete_local_file_video, token, video_id)

    _print_step("Wait for READY")
    _run_timed(timer, f"video_ready_poll_{index}", _poll_video_ready, token, video_id, timeout_sec=ready_timeout_sec)


def _preflight_existing_services(*, core_api_base_url: str, embedding_base_url: str, search_base_url: str) -> None:
    _wait_for_health("core-api", _health_url(core_api_base_url), timeout_sec=60)
    _wait_for_health("managed-embedding-endpoint", _health_url(embedding_base_url), timeout_sec=300)
    _wait_for_health("search-service", _health_url(search_base_url), timeout_sec=60)


def _cleanup_test_user_via_compose(*, user_id: str) -> None:
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
    _run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "app", "-c", sql]
    )


def _sql_literal(raw: str) -> str:
    return raw.replace("'", "''")


def _seed_transcript_fixture_via_compose(*, video_id: str, fixture_path: Path) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    stt_model_version = _sql_literal(str(payload["stt_model_version"]))
    statements = []
    for segment in payload["segments"]:
        text = _sql_literal(str(segment["text"]))
        statements.append(
            "INSERT INTO transcript_segment "
            "(id, video_id, segment_index, text, start_ms, end_ms, stt_model_version) "
            f"VALUES (gen_random_uuid(), '{video_id}', {int(segment['segment_index'])}, '{text}', "
            f"{int(segment['start_ms'])}, {int(segment['end_ms'])}, '{stt_model_version}');"
        )
    _run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "app"],
        input_text="\n".join(statements) + "\n",
    )
