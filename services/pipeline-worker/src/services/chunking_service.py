import re
from dataclasses import dataclass
from typing import Protocol


class VideoSegment(Protocol):
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class SentenceFragment:
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class ChunkDraft:
    chunk_index: int
    text: str
    start_ms: int
    end_ms: int
    chunking_version: str


@dataclass(slots=True)
class ChunkingProgress:
    chunks: list[ChunkDraft]
    buffer: list[SentenceFragment]
    next_chunk_index: int


class ChunkingService:
    def __init__(
        self,
        *,
        max_tokens: int,
        overlap_sentences: int,
        chunking_version: str = "v1",
    ) -> None:
        self._max_tokens = max_tokens
        self._overlap_sentences = overlap_sentences
        self._chunking_version = chunking_version

    def chunk_segments(self, segments: list[VideoSegment]) -> list[ChunkDraft]:
        return self.append_segments(
            segments,
            buffer=[],
            next_chunk_index=0,
            flush=True,
        ).chunks

    def append_segments(
        self,
        segments: list[VideoSegment],
        *,
        buffer: list[SentenceFragment],
        next_chunk_index: int,
        flush: bool = False,
    ) -> ChunkingProgress:
        fragments: list[SentenceFragment] = []
        for segment in segments:
            fragments.extend(self._split_segment(segment))

        chunks: list[ChunkDraft] = []
        current_buffer = list(buffer)
        for fragment in fragments:
            candidate = current_buffer + [fragment]
            if current_buffer and self._token_count(candidate) > self._max_tokens:
                chunks.append(self._build_chunk(next_chunk_index, current_buffer))
                next_chunk_index += 1
                overlap = (
                    current_buffer[-self._overlap_sentences :]
                    if self._overlap_sentences
                    else []
                )
                current_buffer = overlap + [fragment]
            else:
                current_buffer = candidate

        if flush and current_buffer:
            chunks.append(self._build_chunk(next_chunk_index, current_buffer))
            next_chunk_index += 1
            current_buffer = []
        return ChunkingProgress(
            chunks=chunks,
            buffer=current_buffer,
            next_chunk_index=next_chunk_index,
        )

    @property
    def chunking_version(self) -> str:
        return self._chunking_version

    def _split_segment(self, segment: VideoSegment) -> list[SentenceFragment]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", segment.text) if part.strip()]
        if not parts:
            parts = [segment.text.strip()]
        return [
            SentenceFragment(text=part, start_ms=segment.start_ms, end_ms=segment.end_ms)
            for part in parts
        ]

    def _build_chunk(self, chunk_index: int, fragments: list[SentenceFragment]) -> ChunkDraft:
        return ChunkDraft(
            chunk_index=chunk_index,
            text=" ".join(fragment.text for fragment in fragments).strip(),
            start_ms=fragments[0].start_ms,
            end_ms=fragments[-1].end_ms,
            chunking_version=self._chunking_version,
        )

    @staticmethod
    def _token_count(fragments: list[SentenceFragment]) -> int:
        return sum(len(fragment.text.split()) for fragment in fragments)
