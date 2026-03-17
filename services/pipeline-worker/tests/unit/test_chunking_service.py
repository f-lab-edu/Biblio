from dataclasses import dataclass

from services.chunking_service import ChunkingService


@dataclass
class _Segment:
    text: str
    start_ms: int
    end_ms: int


def test_chunking_service_applies_overlap() -> None:
    service = ChunkingService(max_tokens=4, overlap_sentences=1)
    segments = [
        _Segment(text="Alpha beta. Gamma delta.", start_ms=0, end_ms=100),
        _Segment(text="Epsilon zeta.", start_ms=100, end_ms=200),
    ]

    chunks = service.chunk_segments(segments)

    assert len(chunks) >= 2
    assert chunks[1].text.startswith("Gamma delta.")


def test_chunking_service_splits_oversized_sentence() -> None:
    service = ChunkingService(max_tokens=3, overlap_sentences=0)
    segments = [_Segment(text="one two three four five six", start_ms=0, end_ms=100)]

    chunks = service.chunk_segments(segments)

    assert len(chunks) == 2
