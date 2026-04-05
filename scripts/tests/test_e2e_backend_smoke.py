from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "e2e_backend_smoke.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_backend_smoke", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_signal_service_process_group_signals_owned_group(monkeypatch) -> None:
    smoke = _load_module()
    process = Mock()
    process.pid = 4242

    killpg = Mock()
    monkeypatch.setattr(smoke.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(smoke.os, "killpg", killpg)

    signaled = smoke._signal_service_process_group(process, smoke.signal.SIGTERM)

    assert signaled is True
    killpg.assert_called_once_with(4242, smoke.signal.SIGTERM)


def test_signal_service_process_group_skips_unowned_group(monkeypatch) -> None:
    smoke = _load_module()
    process = Mock()
    process.pid = 4242

    killpg = Mock()
    monkeypatch.setattr(smoke.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(smoke.os, "killpg", killpg)

    signaled = smoke._signal_service_process_group(process, smoke.signal.SIGTERM)

    assert signaled is False
    killpg.assert_not_called()
