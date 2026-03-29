import pytest
from pydantic import ValidationError

from src.schemas.search_dto import (
    SearchRequest,
    normalize_query,
)


class TestNormalizeQuery:
    def test_preserves_korean(self) -> None:
        assert normalize_query("안녕하세요 ABC") == "안녕하세요 abc"

    def test_mixed_normalization(self) -> None:
        assert normalize_query("  Hello\x00  World  ") == "hello world"


class TestSearchRequest:
    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="")

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest.model_validate({"query": "hello", "category": "IT"})
