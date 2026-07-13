from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import AppUser


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, user: AppUser) -> AppUser:
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_email(self, email: str) -> AppUser | None:
        result = await self.session.execute(select(AppUser).where(AppUser.email == email))
        return result.scalar_one_or_none()

    async def update_last_login(self, user: AppUser, logged_in_at: datetime) -> AppUser:
        user.last_login_at = logged_in_at
        await self.session.flush()
        return user
