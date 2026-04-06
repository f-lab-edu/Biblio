from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "e2e_backend_smoke_transcript_fixture.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_backend_smoke_transcript_fixture", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_args_loads_transcript_fixture_scenario(tmp_path: Path) -> None:
    smoke = _load_module()
    scenario_path = tmp_path / "transcript-basic.json"
    scenario_path.write_text(
        json.dumps(
            {
                "video_path": "/tmp/input.mp4",
                "transcript_fixture_path": "/tmp/transcript.json",
                "queries": ["질문 하나"],
                "user_id": "22222222-2222-2222-2222-222222222222",
                "ready_timeout_sec": 900,
                "upload_timeout_sec": 480,
            }
        ),
        encoding="utf-8",
    )

    args = smoke.parse_args(["--scenario", str(scenario_path)])

    assert args.video_path == Path("/tmp/input.mp4")
    assert args.transcript_fixture_path == Path("/tmp/transcript.json")
    assert args.queries == ["질문 하나"]
    assert args.user_id == "22222222-2222-2222-2222-222222222222"
    assert args.ready_timeout_sec == 900
    assert args.upload_timeout_sec == 480


def test_seed_transcript_fixture_uses_docker_compose_psql_stdin(tmp_path: Path) -> None:
    smoke = _load_module()
    fixture_path = tmp_path / "transcript.json"
    fixture_path.write_text(
        json.dumps(
            {
                "stt_model_version": "chirp_2",
                "segments": [
                    {"segment_index": 0, "text": "첫 번째 문장", "start_ms": 0, "end_ms": 1200},
                    {"segment_index": 1, "text": "두 번째 문장", "start_ms": 1200, "end_ms": 2500},
                ],
            }
        ),
        encoding="utf-8",
    )
    run = Mock()
    smoke.SHARED._run = run

    smoke._seed_transcript_fixture_via_compose(
        video_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        fixture_path=fixture_path,
    )

    command = run.call_args.args[0]
    assert command[:5] == ["docker", "compose", "exec", "-T", "db"]
    assert run.call_args.kwargs["input_text"].count("INSERT INTO transcript_segment") == 2
    assert "chirp_2" in run.call_args.kwargs["input_text"]
