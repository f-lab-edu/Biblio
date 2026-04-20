"""Unit tests for RRF (Reciprocal Rank Fusion) merger.

Verifies: score calculation, dedup, FINAL_TOP_K limit, ordering.
"""

from uuid import uuid4

from src.infra.db.search_repository import ANNCandidate, FTSCandidate
from src.services.rrf import rrf_merge


def _fts(chunk_id, rank):
    return FTSCandidate(chunk_id=chunk_id, rank=rank)


def _ann(chunk_id, rank):
    return ANNCandidate(chunk_id=chunk_id, rank=rank)


class TestRRFMerge:
    def test_single_source_fts_only(self) -> None:
        cid = uuid4()
        result = rrf_merge([_fts(cid, 1)], [], k=60, top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == cid

    def test_single_source_ann_only(self) -> None:
        cid = uuid4()
        result = rrf_merge([], [_ann(cid, 1)], k=60, top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == cid

    def test_both_empty_returns_empty(self) -> None:
        result = rrf_merge([], [], k=60, top_k=5)
        assert result == []

    def test_dedup_same_chunk_in_both(self) -> None:
        """Same chunk_id in FTS and ANN should appear once with combined score."""
        cid = uuid4()
        result = rrf_merge([_fts(cid, 1)], [_ann(cid, 1)], k=60, top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == cid
        # Score should be 2 * 1/(60+1) since rank=1 in both
        expected_score = 2 * (1.0 / (60 + 1))
        assert abs(result[0].score - expected_score) < 1e-9

    def test_score_formula(self) -> None:
        """RRF_score(d) = Σ 1/(k + rank(d)), k=60."""
        cid_a = uuid4()
        cid_b = uuid4()
        # cid_a: FTS rank=1, ANN rank=3
        # cid_b: FTS rank=2, ANN rank=1
        result = rrf_merge(
            [_fts(cid_a, 1), _fts(cid_b, 2)],
            [_ann(cid_b, 1), _ann(cid_a, 3)],
            k=60,
            top_k=10,
        )
        scores = {r.chunk_id: r.score for r in result}
        # cid_a: 1/(60+1) + 1/(60+3) = 1/61 + 1/63
        assert abs(scores[cid_a] - (1 / 61 + 1 / 63)) < 1e-9
        # cid_b: 1/(60+2) + 1/(60+1) = 1/62 + 1/61
        assert abs(scores[cid_b] - (1 / 62 + 1 / 61)) < 1e-9

    def test_ordering_by_score_desc(self) -> None:
        """Higher RRF score should come first."""
        cid_high = uuid4()
        cid_low = uuid4()
        # cid_high appears in both (higher combined score)
        # cid_low appears only in ANN
        result = rrf_merge(
            [_fts(cid_high, 1)],
            [_ann(cid_high, 1), _ann(cid_low, 2)],
            k=60,
            top_k=10,
        )
        assert result[0].chunk_id == cid_high
        assert result[1].chunk_id == cid_low

    def test_top_k_limits_output(self) -> None:
        fts = [_fts(uuid4(), i + 1) for i in range(10)]
        ann = [_ann(uuid4(), i + 1) for i in range(10)]
        result = rrf_merge(fts, ann, k=60, top_k=3)
        assert len(result) == 3

    def test_top_k_with_fewer_candidates(self) -> None:
        """If fewer candidates than top_k, return all."""
        cid = uuid4()
        result = rrf_merge([_fts(cid, 1)], [], k=60, top_k=10)
        assert len(result) == 1
