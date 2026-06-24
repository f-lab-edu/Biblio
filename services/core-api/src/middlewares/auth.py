from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import Settings
from src.core.dependencies import get_settings_dependency
from src.middlewares.error_handler import AuthenticationError, ForbiddenError

bearer_scheme = HTTPBearer(auto_error=False)


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
    if payload.get("role") == "admin":
        return True
    roles = payload.get("roles")
    return isinstance(roles, list) and "admin" in roles


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Bearer token is required.")

    try:
        payload = jwt.decode(
            credentials.credentials,
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
