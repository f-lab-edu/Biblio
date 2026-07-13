import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.support import SessionFactory, TEST_JWT_SIGNING_KEY


@pytest.mark.asyncio
async def test_signup_sets_auth_cookies_and_stores_hashed_password(
    api_client: AsyncClient,
    session_factory: SessionFactory,
) -> None:
    response = await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "USER@Example.COM", "password": "strong-password"},
    )

    assert response.status_code == 201
    token = response.cookies.get("biblio_access_token")
    assert token
    assert response.cookies.get("biblio_csrf_token")
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["userId"]
    assert "token" not in body
    decoded = jwt.decode(token, TEST_JWT_SIGNING_KEY, algorithms=["HS256"])
    assert decoded["role"] == "USER"

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT email, password_hash, role, status FROM app_user WHERE email = :email"),
            {"email": "user@example.com"},
        )
        row = result.one()

    assert row.email == "user@example.com"
    assert row.role == "USER"
    assert row.status == "ACTIVE"
    assert row.password_hash != "strong-password"
    assert row.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_normalized_email(api_client: AsyncClient) -> None:
    first_response = await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "duplicate@example.com", "password": "strong-password"},
    )
    second_response = await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "DUPLICATE@EXAMPLE.COM", "password": "strong-password"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


@pytest.mark.asyncio
async def test_signup_rejects_invalid_email_format(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "abc", "password": "strong-password"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_login_rejects_invalid_email_format(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "abc", "password": "strong-password"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_login_cookie_auth_allows_protected_get_without_bearer(
    api_client: AsyncClient,
) -> None:
    await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "login@example.com", "password": "strong-password"},
    )
    login_response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "LOGIN@example.com", "password": "strong-password"},
    )

    assert login_response.status_code == 200
    token = login_response.cookies.get("biblio_access_token")
    assert token is not None
    decoded = jwt.decode(token, TEST_JWT_SIGNING_KEY, algorithms=["HS256"])
    assert decoded["requester_user_id"] == login_response.json()["userId"]
    assert decoded["role"] == "USER"

    videos_response = await api_client.get("/api/v1/videos")

    assert videos_response.status_code == 200
    assert videos_response.json() == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_cookie_auth_state_change_requires_matching_csrf_header(
    api_client: AsyncClient,
) -> None:
    await api_client.post(
        "/api/v1/auth/signup",
        json={"email": "csrf@example.com", "password": "strong-password"},
    )
    login_response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "csrf@example.com", "password": "strong-password"},
    )

    missing_header_response = await api_client.post(
        "/api/v1/videos",
        json={
            "input_type": "EXTERNAL_URL",
            "title": "csrf check",
            "category": "GENERAL",
            "source_url": "https://www.youtube.com/watch?v=csrf",
        },
    )
    invalid_header_response = await api_client.post(
        "/api/v1/videos",
        headers={"X-CSRF-Token": "wrong-csrf-token"},
        json={
            "input_type": "EXTERNAL_URL",
            "title": "csrf check",
            "category": "GENERAL",
            "source_url": "https://www.youtube.com/watch?v=csrf",
        },
    )
    valid_header_response = await api_client.post(
        "/api/v1/videos",
        headers={"X-CSRF-Token": login_response.cookies["biblio_csrf_token"]},
        json={
            "input_type": "EXTERNAL_URL",
            "title": "csrf check",
            "category": "GENERAL",
            "source_url": "https://www.youtube.com/watch?v=csrf",
        },
    )

    assert missing_header_response.status_code == 403
    assert invalid_header_response.status_code == 403
    assert valid_header_response.status_code == 202
