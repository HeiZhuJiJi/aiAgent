from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    content: str
    locator: str
    source_id: str
    source_name: str | None = None


@dataclass(frozen=True)
class RetrievalHit:
    candidate: RetrievalCandidate
    rrf_score: float
    rerank_score: float


@dataclass(frozen=True)
class ReciprocalRankHit:
    candidate: RetrievalCandidate
    rrf_score: float


Reranker = Callable[[str, Sequence[RetrievalCandidate]], Sequence[float]]


def reciprocal_rank_fuse(
    lexical: Sequence[RetrievalCandidate],
    semantic: Sequence[RetrievalCandidate],
    *,
    rrf_k: int = 60,
    candidate_limit: int = 50,
    result_limit: int = 10,
) -> tuple[ReciprocalRankHit, ...]:
    """Return deterministic lexical/semantic RRF results without model scores."""
    if rrf_k <= 0 or candidate_limit <= 0 or result_limit <= 0:
        raise ValueError("retrieval limits must be positive")
    scores: dict[str, float] = {}
    candidates: dict[str, RetrievalCandidate] = {}
    for ranking in (lexical, semantic):
        for rank, candidate in enumerate(ranking[:candidate_limit], start=1):
            existing = candidates.get(candidate.chunk_id)
            if existing is not None and existing != candidate:
                raise ValueError("same chunk id returned with changed content")
            candidates[candidate.chunk_id] = candidate
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0) + 1 / (rrf_k + rank)
    hits = [
        ReciprocalRankHit(candidate=item, rrf_score=scores[item.chunk_id])
        for item in candidates.values()
    ]
    hits.sort(key=lambda item: (-item.rrf_score, item.candidate.chunk_id))
    return tuple(hits[:result_limit])


def hybrid_rrf_rerank(
    query: str,
    lexical: Sequence[RetrievalCandidate],
    semantic: Sequence[RetrievalCandidate],
    *,
    reranker: Reranker,
    rrf_k: int = 60,
    candidate_limit: int = 50,
    result_limit: int = 10,
) -> tuple[RetrievalHit, ...]:
    if not query.strip():
        raise ValueError("query cannot be empty")
    if rrf_k <= 0 or candidate_limit <= 0 or result_limit <= 0:
        raise ValueError("retrieval limits must be positive")
    fused_hits = reciprocal_rank_fuse(
        lexical,
        semantic,
        rrf_k=rrf_k,
        candidate_limit=candidate_limit,
        result_limit=candidate_limit * 2,
    )
    fused = tuple(hit.candidate for hit in fused_hits)
    rerank_scores = tuple(float(value) for value in reranker(query, fused))
    if len(rerank_scores) != len(fused):
        raise ValueError("reranker score count mismatch")
    hits = [
        RetrievalHit(
            candidate=fused_hit.candidate,
            rrf_score=fused_hit.rrf_score,
            rerank_score=score,
        )
        for fused_hit, score in zip(fused_hits, rerank_scores, strict=True)
    ]
    hits.sort(key=lambda item: (-item.rerank_score, -item.rrf_score, item.candidate.chunk_id))
    return tuple(hits[:result_limit])
