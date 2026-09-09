import pytest

from enterprise_agent.runtime.retrieval import RetrievalCandidate, hybrid_rrf_rerank


def candidate(identifier: str) -> RetrievalCandidate:
    return RetrievalCandidate(identifier, f"content {identifier}", f"page:{identifier}", "doc")


def test_hybrid_retrieval_fuses_fts_and_vector_then_applies_reranker() -> None:
    a, b, c = candidate("a"), candidate("b"), candidate("c")
    observed = []

    def bge(query, values):
        observed.append((query, tuple(item.chunk_id for item in values)))
        return [0.1 if item.chunk_id == "b" else 0.9 if item.chunk_id == "c" else 0.5 for item in values]

    hits = hybrid_rrf_rerank(
        "supplier risk",
        (a, b),
        (b, c),
        reranker=bge,
        result_limit=3,
    )
    assert observed == [("supplier risk", ("b", "a", "c"))]
    assert [hit.candidate.chunk_id for hit in hits] == ["c", "a", "b"]
    assert hits[2].rrf_score > hits[0].rrf_score


def test_hybrid_retrieval_rejects_identity_drift_and_bad_reranker() -> None:
    original = candidate("a")
    changed = RetrievalCandidate("a", "changed", "page:1", "doc")
    with pytest.raises(ValueError, match="changed content"):
        hybrid_rrf_rerank("q", (original,), (changed,), reranker=lambda _q, _v: [1])
    with pytest.raises(ValueError, match="score count"):
        hybrid_rrf_rerank("q", (original,), (), reranker=lambda _q, _v: [])
