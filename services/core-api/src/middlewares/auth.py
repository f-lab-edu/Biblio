from dataclasses import dataclass
import secrets
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import Settings
from src.core.dependencies import get_settings_dependency
from src.middlewares.error_handler import AuthenticationError, ForbiddenError

bearer_scheme = HTTPBearer(auto_error=False)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_EXEMPT_PATH_SUFFIXES = ("/auth/login", "/auth/signup")


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    requester_user_id: UUID
    is_admin: bool = False


def _parse_requester_user_id(payload: dict[str, object]) -> UUID:
    raw_user_id = payload.get("requester_user_id")
    if raw_user_id is None:
        raise AuthenticationError("JWT payload must include requester_user_id.")

    try:
        return UUID(str(raw_user_id))
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("JWT payload contains an invalid requester_user_id.") from exc


def _has_admin_role(payload: dict[str, object]) -> bool:
    role = payload.get("role")
    return isinstance(role, str) and role.upper() == "ADMIN"


def _bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials


def _cookie_token(request: Request, settings: Settings) -> str | None:
    return request.cookies.get(settings.auth_cookie_name)

# CSRF 검사가 필요한지 판단
def _requires_csrf_check(request: Request) -> bool:
    if request.method.upper() in SAFE_METHODS:
        return False
    return not request.url.path.endswith(CSRF_EXEMPT_PATH_SUFFIXES)


def _validate_csrf(request: Request, settings: Settings) -> None:
    if not _requires_csrf_check(request):
        return

    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    csrf_header = request.headers.get("X-CSRF-Token")
    if csrf_cookie is None or csrf_header is None or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise ForbiddenError("CSRF token is missing or invalid.")


def validate_csrf_request(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dependency)],
) -> None:
    _validate_csrf(request, settings)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthenticatedUser:
    # 테스트와 내부 호출 호환을 위해 Bearer 인증을 유지하고, 브라우저는 쿠키 인증을 사용한다.
    token = _bearer_token(credentials)
    if token is None:
        token = _cookie_token(request, settings)
        if token is not None:
            _validate_csrf(request, settings)
    if token is None:
        raise AuthenticationError("Bearer token or auth cookie is required.")

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("JWT has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("JWT validation failed.") from exc

    requester_user_id = _parse_requester_user_id(payload)
    request.state.user_id = str(requester_user_id)
    return AuthenticatedUser(
        requester_user_id=requester_user_id,
        is_admin=_has_admin_role(payload),
    )


def require_admin_user(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if not user.is_admin:
        raise ForbiddenError("Admin role is required.")
    return user
