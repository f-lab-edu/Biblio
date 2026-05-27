from fastapi import FastAPI

from src.main import reload_models_on_startup


class _FakeReloader:
    def __init__(self) -> None:
        self.trace_ids: list[str] = []

    async def reload(self, trace_id: str):
        self.trace_ids.append(trace_id)


class TestStartupReload:
    async def test_calls_model_reloader_with_startup_trace_id(self):
        app = FastAPI()
        reloader = _FakeReloader()
        app.state.model_reloader = reloader

        await reload_models_on_startup(app)

        assert reloader.trace_ids == ["startup"]

    async def test_noops_when_reloader_missing(self):
        app = FastAPI()

        await reload_models_on_startup(app)
