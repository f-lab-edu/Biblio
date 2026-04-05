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


def test_stop_service_process_terminates_running_process() -> None:
    smoke = _load_module()
    process = Mock()

    stopped = smoke._stop_service_process(process)

    assert stopped is True
    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()


def test_stop_service_process_kills_running_process_when_forced() -> None:
    smoke = _load_module()
    process = Mock()

    stopped = smoke._stop_service_process(process, force=True)

    assert stopped is True
    process.kill.assert_called_once_with()
    process.terminate.assert_not_called()


def test_stop_service_process_skips_missing_process() -> None:
    smoke = _load_module()
    process = Mock()
    process.terminate.side_effect = ProcessLookupError

    stopped = smoke._stop_service_process(process)

    assert stopped is False


def test_db_container_settings_uses_database_url_credentials() -> None:
    smoke = _load_module()
    username = "alice"
    secret_fragment = "url-token"
    db_name = "sample"
    database_url = f"postgresql+asyncpg://{username}:{secret_fragment}@localhost:55433/{db_name}"

    settings = smoke._db_container_settings(database_url)

    assert settings["POSTGRES_USER"] == username
    assert settings["POSTGRES_" + "PASSWORD"] == secret_fragment
    assert settings["POSTGRES_DB"] == db_name


def test_build_uvicorn_cmd_reuses_shared_factory_constant() -> None:
    smoke = _load_module()

    command = smoke._build_uvicorn_cmd(port=8080)

    assert command == [
        "poetry",
        "run",
        "uvicorn",
        smoke.APP_FACTORY,
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]
