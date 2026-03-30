import re
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MULTI_SPACE_RE = re.compile(r"\s+")

QUERY_MIN_LEN = 2
QUERY_MAX_LEN = 1000
EMPTY_ANSWER = "검색 결과가 없습니다"


def normalize_query(raw: str) -> str:
    text = raw.strip()
    text = _CONTROL_CHAR_RE.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = text.lower()
    return text


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class ChunkResponse(BaseModel):
    ref: int
    chunk_id: UUID
    video_id: UUID
    title: str
    start_ms: int
    end_ms: int
    text: str
    used: bool = False


class SearchResponse(BaseModel):
    req_id: UUID
    answer: str
    chunks: list[ChunkResponse]
