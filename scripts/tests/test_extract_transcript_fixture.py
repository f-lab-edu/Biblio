from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "extract_transcript_fixture.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extract_transcript_fixture", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_args_defaults_output_to_transcript_fixture_dir() -> None:
    fixture = _load_module()

    args = fixture.parse_args(["--video-path", "/tmp/demo-video.mp4"])

    assert args.video_path == Path("/tmp/demo-video.mp4")
    assert args.output == fixture.DEFAULT_FIXTURE_DIR / "demo-video.json"


def test_build_fixture_payload_serializes_transcript_segments() -> None:
    fixture = _load_module()
    result = SimpleNamespace(
        stt_model_version="chirp_2",
        segments=[
            SimpleNamespace(text="첫 번째 문장", start_ms=0, end_ms=1200),
            SimpleNamespace(text="두 번째 문장", start_ms=1200, end_ms=2500),
        ],
    )

    payload = fixture._build_fixture_payload(Path("/tmp/demo-video.mp4"), result)

    assert payload["source_video_path"] == "/tmp/demo-video.mp4"
    assert payload["stt_model_version"] == "chirp_2"
    assert payload["segments"] == [
        {
            "segment_index": 0,
            "text": "첫 번째 문장",
            "start_ms": 0,
            "end_ms": 1200,
        },
        {
            "segment_index": 1,
            "text": "두 번째 문장",
            "start_ms": 1200,
            "end_ms": 2500,
        },
    ]
