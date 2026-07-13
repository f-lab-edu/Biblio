import secrets
from typing import Protocol
from uuid import UUID

import jwt
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from biblio_auth.context import AuthContext
from biblio_auth.errors import AuthenticationFailed, CsrfValidationFailed

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_EXEMPT_PATH_SUFFIXES = ("/auth/login", "/auth/signup")


class AuthSettings(Protocol):
    jwt_secret_key: str
    auth_cookie_name: str
    csrf_cookie_name: str


def authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    settings: AuthSettings,
) -> AuthContext:
    token = _bearer_token(credentials)
    if token is None:
        token = _cookie_token(request, settings)
        if token is not None:
            validate_csrf_request(request, settings)
    if token is None:
        raise AuthenticationFailed("Bearer token or auth cookie is required.")

    payload = _decode_jwt(token, settings)
    return AuthContext(
        user_id=_parse_requester_user_id(payload),
        is_admin=_has_admin_role(payload),
    )


def validate_csrf_request(request: Request, settings: AuthSettings) -> None:
    if not _requires_csrf_check(request):
        return

    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    csrf_header = request.headers.get("X-CSRF-Token")
    if csrf_cookie is None or csrf_header is None or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise CsrfValidationFailed()


def _bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials


def _cookie_token(request: Request, settings: AuthSettings) -> str | None:
    return request.cookies.get(settings.auth_cookie_name)


def _requires_csrf_check(request: Request) -> bool:
    if request.method.upper() in SAFE_METHODS:
        return False
    return not request.url.path.endswith(CSRF_EXEMPT_PATH_SUFFIXES)


def _decode_jwt(token: str, settings: AuthSettings) -> dict[str, object]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationFailed("JWT has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationFailed("JWT validation failed.") from exc
    if not isinstance(payload, dict):
        raise AuthenticationFailed("JWT payload is invalid.")
    return payload


def _parse_requester_user_id(payload: dict[str, object]) -> UUID:
    raw_user_id = payload.get("requester_user_id")
    if raw_user_id is None:
        raise AuthenticationFailed("JWT payload must include requester_user_id.")

    try:
        return UUID(str(raw_user_id))
    except (TypeError, ValueError) as exc:
        raise AuthenticationFailed("JWT payload contains an invalid requester_user_id.") from exc


def _has_admin_role(payload: dict[str, object]) -> bool:
    role = payload.get("role")
    return isinstance(role, str) and role.upper() == "ADMIN"
