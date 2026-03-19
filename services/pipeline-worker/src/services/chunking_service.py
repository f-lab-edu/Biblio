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


class ChunkingService:
    def __init__(self, *, max_tokens: int, overlap_sentences: int, chunking_version: str = "v1") -> None:
        self._max_tokens = max_tokens
        self._overlap_sentences = overlap_sentences
        self._chunking_version = chunking_version

    def chunk_segments(self, segments: list[VideoSegment]) -> list[ChunkDraft]:
        fragments: list[SentenceFragment] = []
        for segment in segments:
            fragments.extend(self._split_segment(segment))

        chunks: list[ChunkDraft] = []
        buffer: list[SentenceFragment] = []
        for fragment in fragments:
            candidate = buffer + [fragment]
            if buffer and self._token_count(candidate) > self._max_tokens:
                chunks.append(self._build_chunk(len(chunks), buffer))
                overlap = buffer[-self._overlap_sentences :] if self._overlap_sentences else []
                buffer = overlap + [fragment]
            else:
                buffer = candidate

        if buffer:
            chunks.append(self._build_chunk(len(chunks), buffer))
        return chunks

    def _split_segment(self, segment: VideoSegment) -> list[SentenceFragment]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", segment.text) if part.strip()]
        if not parts:
            parts = [segment.text.strip()]
        fragments: list[SentenceFragment] = []
        for part in parts:
            tokens = part.split()
            if len(tokens) <= self._max_tokens:
                fragments.append(SentenceFragment(text=part, start_ms=segment.start_ms, end_ms=segment.end_ms))
                continue
            for offset in range(0, len(tokens), self._max_tokens):
                piece = " ".join(tokens[offset : offset + self._max_tokens])
                fragments.append(SentenceFragment(text=piece, start_ms=segment.start_ms, end_ms=segment.end_ms))
        return fragments

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
