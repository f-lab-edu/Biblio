from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AuthResponse(BaseModel):
    # populate_by_name=True : alias가 있어도 원래 필드 이름(user_id)으로 값을 넣을 수 있게 해주는 옵션
    model_config = ConfigDict(populate_by_name=True)

    user_id: UUID = Field(serialization_alias="userId") # 파이썬 안에서는 user_id로 쓰고, JSON 응답으로 나갈 때는 userId
    email: str


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: UUID = Field(serialization_alias="userId")
