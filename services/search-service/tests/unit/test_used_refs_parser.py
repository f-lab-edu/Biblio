"""Tests for used_refs parsing and answer extraction (Task 5)."""

import importlib

import pytest


def _load_module():
    try:
        return importlib.import_module("src.services.used_refs_parser")
    except ModuleNotFoundError as exc:
        pytest.fail(f"used_refs_parser module missing: {exc}")


def _get_attr(module, name: str):
    attr = getattr(module, name, None)
    if attr is None:
        pytest.fail(f"used_refs_parser is missing `{name}`")
    return attr


class TestExtractAnswer:
    def test_returns_only_answer_block(self) -> None:
        module = _load_module()
        extract_answer = _get_attr(module, "extract_answer")

        llm_text = """
        <ANSWER>
        근거에 기반한 답변 [1]
        </ANSWER>
        <USED_REFS_JSON>
        {"used_refs":[1]}
        </USED_REFS_JSON>
        """

        assert extract_answer(llm_text) == "근거에 기반한 답변 [1]"

    def test_raises_when_answer_block_is_missing_or_blank(self) -> None:
        module = _load_module()
        extract_answer = _get_attr(module, "extract_answer")

        with pytest.raises(ValueError):
            extract_answer("<USED_REFS_JSON>{\"used_refs\":[1]}</USED_REFS_JSON>")

        with pytest.raises(ValueError):
            extract_answer("<ANSWER>   </ANSWER>")


class TestParseUsedRefs:
    def test_filters_duplicates_out_of_range_and_non_integer_values(self) -> None:
        module = _load_module()
        parse_used_refs = _get_attr(module, "parse_used_refs")

        llm_text = """
        <ANSWER>답변 [2] [1]</ANSWER>
        <USED_REFS_JSON>
        {"used_refs":[2,2,99,"x",true,1]}
        </USED_REFS_JSON>
        """

        assert parse_used_refs(llm_text, max_ref=3) == [2, 1]

    def test_only_reads_metadata_block_not_json_like_answer_body(self) -> None:
        module = _load_module()
        parse_used_refs = _get_attr(module, "parse_used_refs")

        llm_text = """
        <ANSWER>
        본문 예시 {"used_refs":[3]} [2]
        </ANSWER>
        <USED_REFS_JSON>
        {"used_refs":[2]}
        </USED_REFS_JSON>
        """

        assert parse_used_refs(llm_text, max_ref=3) == [2]

    def test_returns_empty_list_when_metadata_is_malformed(self) -> None:
        module = _load_module()
        parse_used_refs = _get_attr(module, "parse_used_refs")

        llm_text = """
        <ANSWER>답변 [1]</ANSWER>
        <USED_REFS_JSON>
        {"used_refs":
        </USED_REFS_JSON>
        """

        assert parse_used_refs(llm_text, max_ref=3) == []
