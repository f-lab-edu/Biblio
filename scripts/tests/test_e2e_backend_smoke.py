from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "e2e_backend_smoke.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_backend_smoke", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_db_container_settings_uses_runtime_default_password_when_url_has_no_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-secret")
    smoke = _load_module()

    settings = smoke._db_container_settings("postgresql+asyncpg://alice@localhost:55433/sample")

    assert settings["POSTGRES_" + "PASSWORD"] == "runtime-secret"
