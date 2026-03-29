import asyncio
from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from src.core.config import Settings
from src.main import create_app


def _make_settings() -> Settings:
    return Settings(
        JWT_SECRET_KEY="test-secret",
        DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        EMBEDDING_API_URL="https://localhost:8081/embed",
        LLM_PROVIDER="mock",
    )


@dataclass
class _FakeContainer:
    settings: Settings
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True
        await asyncio.sleep(0)


class TestAppLifecycle:
    async def test_provided_container_is_closed_on_shutdown(self) -> None:
        container = _FakeContainer(settings=_make_settings())
        app = create_app(settings=container.settings, container=container)

        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="https://testserver",
            ) as client:
                response = await client.get("/health")
                assert response.status_code == 200
                assert container.closed is False

        assert container.closed is True
