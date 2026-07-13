import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from src.core.config import Settings
from src.core.dependencies import get_db_session_factory, get_settings_dependency
from src.middlewares.auth import AuthenticatedUser, get_current_user, validate_csrf_request
from src.schemas.auth_dto import AuthResponse, CurrentUserResponse, LoginRequest, SignupRequest
from src.schemas.video_dto import ErrorResponse
from src.services.auth_service import AuthResult, AuthService

DbSessionFactoryDependency = Annotated[Any, Depends(get_db_session_factory)]
SettingsDependency = Annotated[Settings, Depends(get_settings_dependency)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
'''
다음 settings 값들 사용

  settings.jwt_expiration_days
  settings.auth_cookie_name
  settings.csrf_cookie_name
  settings.auth_cookie_secure
  settings.auth_cookie_samesite => 쿠키를 HTTPS에서만 보낼지 정함

  settings.jwt_secret_key
  settings.jwt_expiration_days

'''
auth_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)


@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    response: Response,
    db_session_factory: DbSessionFactoryDependency,
    settings: SettingsDependency,
) -> AuthResponse:
    result = await AuthService(db_session_factory, settings).signup(
        email=payload.email,
        password=payload.password,
    )
    set_auth_cookies(response, result, settings) # 브라우저에 로그인 쿠키를 심음
    return AuthResponse(user_id=result.user_id, email=result.email)


@auth_router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    db_session_factory: DbSessionFactoryDependency,
    settings: SettingsDependency,
) -> AuthResponse:
    result = await AuthService(db_session_factory, settings).login(
        email=payload.email,
        password=payload.password,
    )
    set_auth_cookies(response, result, settings)
    return AuthResponse(user_id=result.user_id, email=result.email)


@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_csrf_request)],
)
async def logout(
    response: Response,
    settings: SettingsDependency,
) -> None:
    clear_auth_cookies(response, settings)


@auth_router.get("/me")
async def me(user: CurrentUser) -> CurrentUserResponse:
    return CurrentUserResponse(user_id=user.requester_user_id)


def set_auth_cookies(response: Response, result: AuthResult, settings: Settings) -> None:
    max_age = settings.jwt_expiration_days * 24 * 60 * 60
    response.set_cookie(
        settings.auth_cookie_name,
        result.access_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
