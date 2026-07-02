from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from biblio_auth import (
    AuthenticationFailed,
    CsrfValidationFailed,
    authenticate_request,
    validate_csrf_request as validate_shared_csrf_request,
)

from src.core.config import Settings
from src.core.dependencies import get_settings_dependency
from src.middlewares.error_handler import AuthenticationError, ForbiddenError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    requester_user_id: UUID
    is_admin: bool = False


def validate_csrf_request(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dependency)],
) -> None:
    try:
        validate_shared_csrf_request(request, settings)
    except CsrfValidationFailed as exc:
        raise ForbiddenError(exc.message) from exc


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings_dependency),
) -> AuthenticatedUser:
    try:
        auth_context = authenticate_request(request, credentials, settings)
    except CsrfValidationFailed as exc:
        raise ForbiddenError(exc.message) from exc
    except AuthenticationFailed as exc:
        raise AuthenticationError(exc.message) from exc

    request.state.user_id = str(auth_context.user_id)
    return AuthenticatedUser(
        requester_user_id=auth_context.user_id,
        is_admin=auth_context.is_admin,
    )


def require_admin_user(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if not user.is_admin:
        raise ForbiddenError("Admin role is required.")
    return user
