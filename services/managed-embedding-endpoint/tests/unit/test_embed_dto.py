import pytest
from pydantic import ValidationError

from src.schemas.embed_dto import EmbedRequest


class TestEmbedRequestValid:
    def test_single_text(self):
        req = EmbedRequest(texts=["hello"], model_version="test-model")
        assert req.texts == ["hello"]
        assert req.model_version == "test-model"

    def test_multiple_texts(self):
        req = EmbedRequest(texts=["a", "b", "c"], model_version="test-model")
        assert len(req.texts) == 3


class TestEmbedRequestInvalid:
    def test_empty_texts_list(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts=[], model_version="test-model")

    def test_missing_texts_field(self):
        with pytest.raises(ValidationError):
            EmbedRequest()

    def test_missing_model_version_field(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts=["hello"])

    def test_texts_wrong_type(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts="not a list", model_version="test-model")

    def test_empty_string_in_texts(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts=[""], model_version="test-model")

    def test_empty_string_among_valid_texts(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts=["hello", "", "world"], model_version="test-model")

    def test_empty_model_version(self):
        with pytest.raises(ValidationError):
            EmbedRequest(texts=["hello"], model_version="")
