from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock


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


def test_upload_file_uses_curl_upload_file_contract() -> None:
    smoke = _load_module()
    run = Mock()
    run.return_value = type("Result", (), {"stdout": "200", "stderr": "", "returncode": 0})()
    smoke._run = run

    status = smoke._upload_file("https://example.com/upload", Path("/tmp/demo.mp4"), timeout_sec=321)

    command = run.call_args.args[0]
    assert command[:4] == ["curl", "-sS", "-o", "/dev/null"]
    assert "--upload-file" in command
    assert "/tmp/demo.mp4" in command
    assert "https://example.com/upload" in command
    assert "-m" in command
    assert "321" in command
    assert status == 200
