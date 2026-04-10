from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "e2e_backend_smoke_shared.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_backend_smoke_shared", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_shared_module_exposes_default_video_and_query_inputs() -> None:
    smoke = _load_module()

    assert smoke.DEFAULT_VIDEO_PATHS
    assert smoke.DEFAULT_QUERIES
    assert smoke.DEFAULT_USER_ID == "11111111-1111-1111-1111-111111111111"


def test_default_database_url_uses_postgres_password_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-secret")

    smoke = _load_module()

    assert smoke.default_database_url() == "postgresql+asyncpg://postgres:runtime-secret@localhost:55433/app"


def test_load_scenario_reads_json_payload(tmp_path: Path) -> None:
    smoke = _load_module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text('{"user_id":"abc","queries":["q1"]}', encoding="utf-8")

    payload = smoke._load_scenario(scenario_path)

    assert payload == {"user_id": "abc", "queries": ["q1"]}


def test_prepare_compose_smoke_sets_shared_base_urls_and_returns_token(tmp_path: Path) -> None:
    smoke = _load_module()
    timer = smoke.StepTimer()
    preflight = Mock()
    cleanup = Mock()
    make_token = Mock(return_value="jwt-token")
    smoke._preflight_existing_services = preflight
    smoke._cleanup_test_user_via_compose = cleanup
    smoke._make_token = make_token

    log_dir, token = smoke._prepare_compose_smoke(
        timer=timer,
        log_prefix="biblio-test-logs-",
        core_api_base_url="http://localhost:8080/",
        embedding_base_url="http://localhost:8081/",
        search_base_url="http://localhost:8082/",
        user_id="11111111-1111-1111-1111-111111111111",
    )

    assert log_dir.name.startswith("biblio-test-logs-")
    assert token == "jwt-token"
    assert smoke.CORE_API_BASE_URL == "http://localhost:8080"
    assert smoke.EMBEDDING_BASE_URL == "http://localhost:8081"
    assert smoke.SEARCH_BASE_URL == "http://localhost:8082"
    assert preflight.call_count == 1
    assert cleanup.call_count == 1
    assert make_token.call_args.args == ("11111111-1111-1111-1111-111111111111",)


def test_upload_file_uses_curl_upload_file_contract(tmp_path: Path) -> None:
    smoke = _load_module()
    run = Mock()
    run.return_value = type("Result", (), {"stdout": "200", "stderr": "", "returncode": 0})()
    smoke._run = run
    upload_path = tmp_path / "demo.mp4"

    status = smoke._upload_file("https://example.com/upload", upload_path, timeout_sec=321)

    command = run.call_args.args[0]
    assert command[:4] == ["curl", "-sS", "-o", "/dev/null"]
    assert "--upload-file" in command
    assert str(upload_path) in command
    assert "https://example.com/upload" in command
    assert "-m" in command
    assert "321" in command
    assert status == 200
