from typing import Annotated

from pydantic import BaseModel, Field


class EmbedRequest(BaseModel):
    texts: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


class HealthResponse(BaseModel):
    status: str
    model_version: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    trace_id: str
