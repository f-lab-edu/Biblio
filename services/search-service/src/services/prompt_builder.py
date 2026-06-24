"""Prompt builder for Search Service RAG answers."""

from dataclasses import dataclass
from uuid import UUID

from src.infra.db.search_repository import ChunkRecord


@dataclass(slots=True)
class ContextBlock:
    ref: int
    chunk_id: UUID
    title: str
    text: str
    start_ms: int
    end_ms: int

# 시스템 프롬프트 (고정 지시문), 사용자 질문 및 응답과 무관하게 전달.
def build_system_prompt() -> str:
    """Build the invariant instruction set for grounded Search answers."""
    return "\n".join(
        [
            "You are the Search Service answer composer.",
            "검색으로 회수된 청크에 의해 직접 뒷받침되는 내용만 답변하라.",
            "근거가 불충분하면 추측으로 메우지 말고 근거 부족을 명시하라.",
            "모든 사실 주장에는 하나 이상의 [n] 인라인 citation을 포함하라.",
            "하나의 문장이 여러 청크에 근거하면 관련 citation을 모두 표기하라.",
            "chunk_id를 복사하거나 노출하지 마라.",
            "반드시 아래 출력 형식만 사용하라.",
            "<ANSWER>",
            "사용자에게 보여줄 답변 본문",
            "</ANSWER>",
            "<USED_REFS_JSON>",
            '{"used_refs":[1,2]}',
            "</USED_REFS_JSON>",
        ]
    )


def build_context_blocks(records: list[ChunkRecord]) -> list[ContextBlock]:
    """Build LLM context blocks in canonical ref order."""
    return [
        ContextBlock(
            ref=index + 1,
            chunk_id=record.chunk_id,
            title=record.title,
            text=record.enriched_text or record.text,
            start_ms=record.start_ms,
            end_ms=record.end_ms,
        )
        for index, record in enumerate(records)
    ]


def _serialize_context_block(block: ContextBlock) -> str:
    return "\n".join(
        [
            f"[{block.ref}]",
            f"title: {block.title}",
            f"start_ms: {block.start_ms}",
            f"end_ms: {block.end_ms}",
            f"text: {block.text}",
        ]
    )


def build_user_prompt(*, query: str, contexts: list[ContextBlock]) -> str:
    """Build the request-specific prompt body with query and contexts."""
    serialized_contexts = "\n\n".join(
        _serialize_context_block(block) for block in contexts
    )
    return "\n".join(
        [
            "[USER_QUERY]",
            query,
            "",
            "[CONTEXT_BLOCKS]",
            serialized_contexts,
        ]
    )


