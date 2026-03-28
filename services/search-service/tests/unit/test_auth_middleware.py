from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.core.config import Settings
from src.core.dependencies import build_dependency_container
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.middlewares.error_handler import register_exception_handlers

TEST_SECRET = "test-secret-key-for-search-service-32b"
TEST_USER_ID = uuid4()


def _make_settings() -> Settings:
    return Settings(
        JWT_SECRET_KEY=TEST_SECRET,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        EMBEDDING_API_URL="http://localhost:8081/embed",
    )


def _make_token(
    user_id: UUID | str | None = None,
    *,
    expired: bool = False,
    secret: str = TEST_SECRET,
) -> str:
    payload: dict = {}
    if user_id is not None:
        payload["requester_user_id"] = str(user_id)
    if expired:
        payload["exp"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(payload, secret, algorithm="HS256")


def _create_app() -> FastAPI:
    app = FastAPI()
    settings = _make_settings()
    app.state.container = build_dependency_container(settings)
    register_exception_handlers(app)

    @app.get("/test-auth")
    def protected(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
        return {"user_id": str(user.requester_user_id)}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_create_app())


class TestAuthMiddleware:
    def test_valid_token(self, client: TestClient) -> None:
        token = _make_token(TEST_USER_ID)
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == str(TEST_USER_ID)

    def test_no_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/test-auth")
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"

    def test_expired_token_returns_401(self, client: TestClient) -> None:
        token = _make_token(TEST_USER_ID, expired=True)
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_wrong_secret_returns_401(self, client: TestClient) -> None:
        token = _make_token(TEST_USER_ID, secret="wrong-secret-key-that-is-32-bytes!!")
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_missing_user_id_returns_401(self, client: TestClient) -> None:
        token = _make_token(user_id=None)
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "requester_user_id" in resp.json()["message"]

    def test_invalid_user_id_returns_401(self, client: TestClient) -> None:
        token = _make_token(user_id="not-a-uuid")
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
