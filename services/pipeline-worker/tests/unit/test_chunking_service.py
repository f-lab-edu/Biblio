from dataclasses import dataclass

from src.services.chunking_service import ChunkingService


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


def test_chunking_service_preserves_oversized_sentence() -> None:
    service = ChunkingService(max_tokens=3, overlap_sentences=0)
    segments = [_Segment(text="one two three four five six", start_ms=0, end_ms=100)]

    chunks = service.chunk_segments(segments)

    assert len(chunks) == 1
    assert chunks[0].text == segments[0].text


def test_chunking_service_timestamps_span_all_segments() -> None:
    # 청크 하나에 여러 세그먼트가 합쳐질 때 start_ms는 첫 세그먼트, end_ms는 마지막 세그먼트 기준
    service = ChunkingService(max_tokens=100, overlap_sentences=0)
    segments = [
        _Segment(text="Alpha.", start_ms=0, end_ms=500),
        _Segment(text="Beta.", start_ms=500, end_ms=1500),
    ]

    chunks = service.chunk_segments(segments)

    assert len(chunks) == 1
    assert chunks[0].start_ms == 0
    assert chunks[0].end_ms == 1500


def test_chunking_service_timestamps_per_chunk_when_split() -> None:
    # 청크가 분리될 때 각 청크의 타임스탬프가 해당 세그먼트 경계를 정확히 따르는지 확인
    service = ChunkingService(max_tokens=1, overlap_sentences=0)
    segments = [
        _Segment(text="Alpha.", start_ms=0, end_ms=500),
        _Segment(text="Beta.", start_ms=500, end_ms=1500),
    ]

    chunks = service.chunk_segments(segments)

    assert chunks[0].start_ms == 0
    assert chunks[0].end_ms == 500
    assert chunks[1].start_ms == 500
    assert chunks[1].end_ms == 1500


def test_chunking_service_records_chunking_version() -> None:
    service = ChunkingService(max_tokens=10, overlap_sentences=0, chunking_version="v2")
    segments = [_Segment(text="Alpha.", start_ms=0, end_ms=100)]

    chunks = service.chunk_segments(segments)

    assert chunks[0].chunking_version == "v2"


def test_chunking_service_returns_empty_for_no_segments() -> None:
    service = ChunkingService(max_tokens=10, overlap_sentences=0)

    assert service.chunk_segments([]) == []


def test_chunking_service_single_segment_produces_one_chunk() -> None:
    service = ChunkingService(max_tokens=10, overlap_sentences=0)
    segments = [_Segment(text="Alpha.", start_ms=100, end_ms=200)]

    chunks = service.chunk_segments(segments)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].start_ms == 100
    assert chunks[0].end_ms == 200


def test_incremental_chunking_keeps_unflushed_buffer() -> None:
    service = ChunkingService(max_tokens=3, overlap_sentences=1)
    first = service.append_segments(
        [_Segment(text="Alpha.", start_ms=0, end_ms=100)],
        buffer=[],
        next_chunk_index=4,
    )

    final = service.append_segments(
        [_Segment(text="Beta gamma delta.", start_ms=100, end_ms=200)],
        buffer=first.buffer,
        next_chunk_index=first.next_chunk_index,
        flush=True,
    )

    assert first.chunks == []
    assert [chunk.chunk_index for chunk in final.chunks] == [4, 5]
    assert final.chunks[1].text.startswith("Alpha.")
