from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from sqlalchemy.exc import IntegrityError

from src.core.config import Settings
from src.infra.db.user_repository import UserRepository
from src.middlewares.error_handler import AuthenticationError, ConflictError
from src.models.user import AppUser

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$u0qh/TSp0T9rYYSkEdEf2A"
    "$onQFcQK2qRRw0iE1tto+XRYdfmCrUZlhoC+rDkpb1z8"
)


@dataclass(frozen=True, slots=True)
class AuthResult:
    user_id: UUID
    email: str
    role: str
    access_token: str


class AuthService:
    def __init__(self, db_session_factory, settings: Settings) -> None:
        self.db_session_factory = db_session_factory
        self.settings = settings
        self.password_hasher = PasswordHasher(type=Type.ID)

    async def signup(self, email: str, password: str) -> AuthResult:
        normalized_email = normalize_email(email)

        async with self.db_session_factory() as session:
            repository = UserRepository(session)
            existing_user = await repository.get_by_email(normalized_email)
            if existing_user is not None:
                raise ConflictError("이미 가입된 이메일입니다.")

            password_hash = self.password_hasher.hash(password)
            user = AppUser(email=normalized_email, password_hash=password_hash)
            try:
                await repository.add(user)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("이미 가입된 이메일입니다.") from exc

            return self._result_for_user(user)

    async def login(self, email: str, password: str) -> AuthResult:
        normalized_email = normalize_email(email)

        async with self.db_session_factory() as session:
            repository = UserRepository(session)
            user = await repository.get_by_email(normalized_email)
            password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            password_matches = self._password_matches(password_hash, password)
            if user is None or not password_matches:
                raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않습니다.")

            await repository.update_last_login(user, datetime.now(tz=timezone.utc))
            await session.commit()
            return self._result_for_user(user)

    def _result_for_user(self, user: AppUser) -> AuthResult:
        return AuthResult(
            user_id=user.id,
            email=user.email,
            role=user.role,
            access_token=self._create_access_token(user.id, user.role),
        )

    def _create_access_token(self, user_id: UUID, role: str) -> str:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            days=self.settings.jwt_expiration_days,
        )
        payload = {"requester_user_id": str(user_id), "role": role, "exp": expires_at}
        return jwt.encode(payload, self.settings.jwt_secret_key, algorithm="HS256")

    def _password_matches(self, password_hash: str, password: str) -> bool:
        try:
            return self.password_hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return False


def normalize_email(email: str) -> str:
    return email.strip().lower()
