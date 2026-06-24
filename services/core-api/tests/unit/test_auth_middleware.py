import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from src.core.config import Settings
from src.middlewares.auth import AuthenticatedUser, get_current_user, require_admin_user
from src.middlewares.error_handler import AuthenticationError, ForbiddenError, api_error_handler
from tests.support import TEST_JWT_SIGNING_KEY


def create_settings() -> Settings:
    return Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key=TEST_JWT_SIGNING_KEY,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/app",
        broker_type="inmemory",
    )


def create_token(secret: str, **claims: object) -> str:
    payload = {"exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5), **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


def build_request() -> Request:
    return Request({"type": "http", "headers": []})


def render_error_response(exc: Exception) -> dict[str, object]:
    request = build_request()
    response = asyncio.run(api_error_handler(request, exc))
    return {
        "status_code": response.status_code,
        "body": response.body.decode("utf-8"),
    }


def test_missing_jwt_returns_401_error_contract() -> None:
    settings = create_settings()
    request = build_request()

    with pytest.raises(AuthenticationError) as exc_info:
        get_current_user(request=request, credentials=None, settings=settings)

    rendered = render_error_response(exc_info.value)

    assert rendered["status_code"] == 401
    assert '"code":"UNAUTHENTICATED"' in rendered["body"]
    assert '"trace_id":"' in rendered["body"]


def test_expired_jwt_returns_401_error_contract() -> None:
    settings = create_settings()
    token = jwt.encode(
        {
            "requester_user_id": str(uuid4()),
            "exp": datetime.now(tz=timezone.utc) - timedelta(seconds=1),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    request = build_request()

    with pytest.raises(AuthenticationError) as exc_info:
        get_current_user(request=request, credentials=credentials, settings=settings)

    rendered = render_error_response(exc_info.value)

    assert rendered["status_code"] == 401
    assert '"code":"UNAUTHENTICATED"' in rendered["body"]


def test_invalid_payload_returns_401_error_contract() -> None:
    settings = create_settings()
    token = create_token(settings.jwt_secret_key, requester_user_id="not-a-uuid")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    request = build_request()

    with pytest.raises(AuthenticationError) as exc_info:
        get_current_user(request=request, credentials=credentials, settings=settings)

    rendered = render_error_response(exc_info.value)

    assert rendered["status_code"] == 401
    assert '"code":"UNAUTHENTICATED"' in rendered["body"]
    assert "requester_user_id" in rendered["body"]


def _decode_user(settings: Settings, token: str) -> AuthenticatedUser:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    request = build_request()
    return get_current_user(request=request, credentials=credentials, settings=settings)


def test_user_without_role_claim_is_not_admin() -> None:
    settings = create_settings()
    token = create_token(settings.jwt_secret_key, requester_user_id=str(uuid4()))

    user = _decode_user(settings, token)

    assert user.is_admin is False


def test_role_claim_admin_marks_user_as_admin() -> None:
    settings = create_settings()
    token = create_token(settings.jwt_secret_key, requester_user_id=str(uuid4()), role="admin")

    user = _decode_user(settings, token)

    assert user.is_admin is True


def test_roles_array_with_admin_marks_user_as_admin() -> None:
    settings = create_settings()
    token = create_token(
        settings.jwt_secret_key,
        requester_user_id=str(uuid4()),
        roles=["viewer", "admin"],
    )

    user = _decode_user(settings, token)

    assert user.is_admin is True


def test_require_admin_user_returns_user_when_admin() -> None:
    user = AuthenticatedUser(requester_user_id=uuid4(), is_admin=True)

    assert require_admin_user(user=user) is user


def test_require_admin_user_rejects_non_admin() -> None:
    user = AuthenticatedUser(requester_user_id=uuid4(), is_admin=False)

    with pytest.raises(ForbiddenError):
        require_admin_user(user=user)


def test_forbidden_error_returns_403_error_contract() -> None:
    rendered = render_error_response(ForbiddenError("Forbidden resource."))

    assert rendered["status_code"] == 403
    assert '"code":"FORBIDDEN"' in rendered["body"]
    assert '"message":"Forbidden resource."' in rendered["body"]
    assert '"trace_id":"' in rendered["body"]
