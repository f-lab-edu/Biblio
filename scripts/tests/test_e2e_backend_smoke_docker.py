from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "e2e_backend_smoke_docker.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_backend_smoke_docker", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_args_defaults_to_docker_local_endpoints() -> None:
    smoke = _load_module()

    args = smoke.parse_args([])

    assert args.core_api_base_url == "http://localhost:8080"
    assert args.embedding_base_url == "http://localhost:8081"
    assert args.search_base_url == "http://localhost:8082"
    assert args.upload_timeout_sec == 600
    assert args.video_paths == smoke.SHARED.DEFAULT_VIDEO_PATHS
    assert args.queries == smoke.SHARED.DEFAULT_QUERIES


def test_preflight_existing_services_checks_required_health_endpoints() -> None:
    smoke = _load_module()
    wait_for_health = Mock()
    smoke.SHARED._wait_for_health = wait_for_health

    smoke._preflight_existing_services(
        core_api_base_url="http://localhost:8080",
        embedding_base_url="http://localhost:8081",
        search_base_url="http://localhost:8082",
    )

    assert wait_for_health.call_count == 3
    assert wait_for_health.call_args_list[0].args == (
        "core-api",
        "http://localhost:8080/health",
    )
    assert wait_for_health.call_args_list[1].args == (
        "managed-embedding-endpoint",
        "http://localhost:8081/health",
    )
    assert wait_for_health.call_args_list[2].args == (
        "search-service",
        "http://localhost:8082/health",
    )


def test_cleanup_test_user_uses_docker_compose_db_exec() -> None:
    smoke = _load_module()
    run = Mock()
    smoke.SHARED._run = run

    smoke._cleanup_test_user_via_compose(user_id="11111111-1111-1111-1111-111111111111")

    command = run.call_args.args[0]
    assert command[:5] == ["docker", "compose", "exec", "-T", "db"]
    assert "DELETE FROM video" in command[-1]


def test_parse_args_loads_values_from_scenario_file(tmp_path: Path) -> None:
    smoke = _load_module()
    scenario_path = tmp_path / "docker-basic.json"
    video_a = tmp_path / "video-a.mp4"
    video_b = tmp_path / "video-b.mp4"
    scenario_path.write_text(
        json.dumps(
            {
                "video_paths": [str(video_a), str(video_b)],
                "queries": ["첫 질문", "둘째 질문"],
                "user_id": "22222222-2222-2222-2222-222222222222",
                "ready_timeout_sec": 900,
                "upload_timeout_sec": 480,
            }
        ),
        encoding="utf-8",
    )

    args = smoke.parse_args(["--scenario", str(scenario_path)])

    assert args.video_paths == [video_a, video_b]
    assert args.queries == ["첫 질문", "둘째 질문"]
    assert args.user_id == "22222222-2222-2222-2222-222222222222"
    assert args.ready_timeout_sec == 900
    assert args.upload_timeout_sec == 480


def test_parse_args_prefers_cli_values_over_scenario(tmp_path: Path) -> None:
    smoke = _load_module()
    scenario_path = tmp_path / "docker-basic.json"
    scenario_video = tmp_path / "video-a.mp4"
    override_video = tmp_path / "override.mp4"
    scenario_path.write_text(
        json.dumps(
            {
                "video_paths": [str(scenario_video)],
                "queries": ["시나리오 질문"],
                "user_id": "22222222-2222-2222-2222-222222222222",
                "ready_timeout_sec": 900,
            }
        ),
        encoding="utf-8",
    )

    args = smoke.parse_args(
        [
            "--scenario",
            str(scenario_path),
            "--video-path",
            str(override_video),
            "--query",
            "직접 질문",
            "--user-id",
            "33333333-3333-3333-3333-333333333333",
            "--ready-timeout-sec",
            "1200",
        ]
    )

    assert args.video_paths == [override_video]
    assert args.queries == ["직접 질문"]
    assert args.user_id == "33333333-3333-3333-3333-333333333333"
    assert args.ready_timeout_sec == 1200
