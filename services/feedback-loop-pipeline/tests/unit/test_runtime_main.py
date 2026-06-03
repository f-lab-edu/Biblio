import pytest

from src.config.settings import Settings
from src.main import build_application

from tests.unit.test_settings import _required_env


async def test_application_dispatches_configured_role(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ROLE", "dataset-worker")

    called_roles: list[str] = []

    def bootstrap(settings: Settings, *, run_once: bool) -> None:
        called_roles.append(f"{settings.app_role}:{run_once}")

    app = build_application(settings=Settings(), role_bootstraps={"dataset-worker": bootstrap})

    await app.run(run_once=True)

    assert called_roles == ["dataset-worker:True"]


async def test_application_rejects_unknown_role(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ROLE", "rollback-worker")

    app = build_application(settings=Settings(), role_bootstraps={})

    with pytest.raises(ValueError, match="Unsupported APP_ROLE"):
        await app.run(run_once=True)


def test_reembedding_worker_role_registered():
    from src.bootstrap import create_role_bootstraps
    assert "reembedding-worker" in create_role_bootstraps()
