from uuid import uuid4

import jwt
import pytest
from argon2.exceptions import VerificationError

from src.core.config import Settings
from src.middlewares.error_handler import AuthenticationError
from src.models.user import AppUser
from src.services import auth_service as auth_service_module
from src.services.auth_service import DUMMY_PASSWORD_HASH, AuthService
from tests.support import TEST_JWT_SIGNING_KEY


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeSessionFactory:
    def __call__(self) -> FakeSession:
        return FakeSession()


class MissingUserRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> None:
        return None


class VerificationErrorPasswordHasher:
    def verify(self, password_hash: str, password: str) -> bool:
        del password_hash, password
        raise VerificationError("invalid hash")


def create_settings() -> Settings:
    return Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key=TEST_JWT_SIGNING_KEY,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/app",
        broker_type="inmemory",
    )


@pytest.mark.asyncio
async def test_login_verifies_dummy_hash_when_user_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_password_matches(self: AuthService, password_hash: str, password: str) -> bool:
        del self
        calls.append((password_hash, password))
        return False

    monkeypatch.setattr(auth_service_module, "UserRepository", MissingUserRepository)
    monkeypatch.setattr(AuthService, "_password_matches", fake_password_matches)

    service = AuthService(FakeSessionFactory(), create_settings())

    with pytest.raises(AuthenticationError):
        await service.login("missing@example.com", "wrong-password")

    assert calls == [(DUMMY_PASSWORD_HASH, "wrong-password")]


def test_password_matches_returns_false_for_verification_error() -> None:
    service = AuthService(FakeSessionFactory(), create_settings())
    service.password_hasher = VerificationErrorPasswordHasher()

    assert service._password_matches("broken-hash", "password") is False


def test_password_matches_returns_false_for_invalid_hash_string() -> None:
    service = AuthService(FakeSessionFactory(), create_settings())

    assert service._password_matches("broken-hash", "password") is False


def test_access_token_role_claim_uses_database_role_value() -> None:
    service = AuthService(FakeSessionFactory(), create_settings())
    user_id = uuid4()
    user = AppUser(
        id=user_id,
        email="admin@example.com",
        password_hash=DUMMY_PASSWORD_HASH,
        role="ADMIN",
    )

    result = service._result_for_user(user)
    decoded = jwt.decode(result.access_token, TEST_JWT_SIGNING_KEY, algorithms=["HS256"])

    assert decoded["requester_user_id"] == str(user_id)
    assert decoded["role"] == "ADMIN"
