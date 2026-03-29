"""Tests for Search Service prompt builder (Task 5)."""

import importlib
from uuid import uuid4

import pytest

from src.infra.db.search_repository import ChunkRecord


def _load_module():
    try:
        return importlib.import_module("src.services.prompt_builder")
    except ModuleNotFoundError as exc:
        pytest.fail(f"prompt_builder module missing: {exc}")


def _get_attr(module, name: str):
    attr = getattr(module, name, None)
    if attr is None:
        pytest.fail(f"prompt_builder is missing `{name}`")
    return attr


class TestBuildContextBlocks:
    def test_prefers_enriched_text_and_falls_back_to_raw_text(self) -> None:
        module = _load_module()
        build_context_blocks = _get_attr(module, "build_context_blocks")

        records = [
            ChunkRecord(
                chunk_id=uuid4(),
                video_id=uuid4(),
                title="Video A",
                text="raw text",
                enriched_text="enriched text",
                start_ms=1000,
                end_ms=2000,
            ),
            ChunkRecord(
                chunk_id=uuid4(),
                video_id=uuid4(),
                title="Video B",
                text="fallback raw text",
                enriched_text="",
                start_ms=3000,
                end_ms=4500,
            ),
        ]

        blocks = build_context_blocks(records)

        assert [block.ref for block in blocks] == [1, 2]
        assert [block.text for block in blocks] == [
            "enriched text",
            "fallback raw text",
        ]


class TestBuildUserPrompt:
    def test_serializes_multi_video_context_with_titles_and_timestamps(self) -> None:
        module = _load_module()
        ContextBlock = _get_attr(module, "ContextBlock")
        build_user_prompt = _get_attr(module, "build_user_prompt")

        first_chunk_id = uuid4()
        second_chunk_id = uuid4()
        prompt = build_user_prompt(
            query="How does the pipeline recover?",
            contexts=[
                ContextBlock(
                    ref=1,
                    chunk_id=first_chunk_id,
                    title="Pipeline 101",
                    text="Recovery uses failed_stage checkpoints.",
                    start_ms=12000,
                    end_ms=18000,
                ),
                ContextBlock(
                    ref=2,
                    chunk_id=second_chunk_id,
                    title="Ops Review",
                    text="Rollbacks keep prior artifacts available.",
                    start_ms=24000,
                    end_ms=30000,
                ),
            ],
        )

        assert "How does the pipeline recover?" in prompt
        assert "Pipeline 101" in prompt
        assert "Ops Review" in prompt
        assert "12000" in prompt
        assert "30000" in prompt
        assert "Recovery uses failed_stage checkpoints." in prompt
        assert "Rollbacks keep prior artifacts available." in prompt
        assert str(first_chunk_id) not in prompt
        assert str(second_chunk_id) not in prompt


class TestBuildSystemPrompt:
    def test_contains_output_format_contract(self) -> None:
        module = _load_module()
        build_system_prompt = _get_attr(module, "build_system_prompt")

        prompt = build_system_prompt()

        assert "<ANSWER>" in prompt
        assert "<USED_REFS_JSON>" in prompt
        assert '"used_refs"' in prompt
        assert "[n]" in prompt
        assert "근거" in prompt
