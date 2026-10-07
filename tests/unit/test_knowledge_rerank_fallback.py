import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from jsonschema import Draft202012Validator

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    CapabilityRequest,
    Claim,
    FailureCode,
    Risk,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.activities import _result_evidence
from enterprise_agent.runtime.retrieval import RetrievalCandidate
from enterprise_agent.runtime.reviewer import review_artifact


def _candidate(identifier: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=identifier,
        content=f"selected document chunk {identifier}",
        locator=f"page:{identifier}",
        source_id="selected-document",
    )


async def test_healthy_reranker_scores_remain_the_primary_ordering() -> None:
    a, b, c = _candidate("a"), _candidate("b"), _candidate("c")
    lexical = (a, b)
    semantic = (b, c)
    ordered = (a, b, c)
    calls = 0

    async def score() -> tuple[float, ...]:
        nonlocal calls
        calls += 1
        return (0.6, 0.1, 0.95)

    scores = await builtins._bounded_knowledge_rerank(
        score,
        expected_count=3,
        timeout_sec=0.5,
    )
    hits = builtins._rank_knowledge_candidates(
        "question",
        lexical,
        semantic,
        ordered,
        scores,
        limit=3,
    )

    assert calls == 1
    assert [(hit.candidate.chunk_id, hit.rank_score) for hit in hits] == [
        ("c", 0.95),
        ("a", 0.6),
    ]
    assert [hit.relevance for hit in hits] == [0.95, 0.6]
    assert all(hit.ranking_mode == "reranker" for hit in hits)


async def test_unavailable_reranker_uses_clearly_logged_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def score() -> tuple[float, ...]:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "reranker offline")

    with caplog.at_level(logging.WARNING, logger=builtins.__name__):
        scores = await builtins._bounded_knowledge_rerank(
            score,
            expected_count=2,
            timeout_sec=0.5,
        )

    assert scores is None
    fallback_record = next(
        record for record in caplog.records if "deterministic RRF fallback" in record.message
    )
    assert fallback_record.ranking_mode == "rrf_fallback"
    assert fallback_record.failure_code == FailureCode.CAPABILITY_UNAVAILABLE.value


async def test_slow_reranker_falls_back_within_total_timeout() -> None:
    async def score() -> tuple[float, ...]:
        await asyncio.sleep(1)
        return (0.9,)

    started = monotonic()
    scores = await builtins._bounded_knowledge_rerank(
        score,
        expected_count=1,
        timeout_sec=0.02,
    )

    assert scores is None
    assert monotonic() - started < 0.25


@pytest.mark.parametrize(
    "scores",
    [
        (0.9,),
        (0.9, float("nan")),
    ],
)
async def test_malformed_reranker_scores_use_fallback(scores: tuple[float, ...]) -> None:
    async def score() -> tuple[float, ...]:
        return scores

    assert (
        await builtins._bounded_knowledge_rerank(
            score,
            expected_count=2,
            timeout_sec=0.5,
        )
        is None
    )


@pytest.mark.parametrize(
    "code",
    [FailureCode.POLICY_DENIED, FailureCode.BUDGET_EXCEEDED],
)
async def test_policy_and_budget_failures_are_never_hidden(code: FailureCode) -> None:
    async def score() -> tuple[float, ...]:
        raise DomainError(code, "must propagate")

    with pytest.raises(DomainError) as raised:
        await builtins._bounded_knowledge_rerank(
            score,
            expected_count=1,
            timeout_sec=0.5,
        )

    assert raised.value.code is code


def test_rrf_fallback_order_is_deterministic_and_bounded() -> None:
    a, b, c, d = (_candidate(identifier) for identifier in ("a", "b", "c", "d"))
    lexical = (b, a, c)
    semantic = (a, b, d)
    ordered = (a, b, c, d)

    first = builtins._rank_knowledge_candidates(
        "question",
        lexical,
        semantic,
        ordered,
        None,
        limit=3,
    )
    second = builtins._rank_knowledge_candidates(
        "question",
        lexical,
        semantic,
        ordered,
        None,
        limit=3,
    )

    assert [hit.candidate.chunk_id for hit in first] == ["a", "b", "c"]
    assert first == second
    assert all(hit.candidate.source_id == "selected-document" for hit in first)
    assert all(hit.rank_score > 0 for hit in first)
    assert all(0.5 <= hit.relevance <= 1 for hit in first)
    assert all(hit.ranking_mode == "rrf_fallback" for hit in first)


async def test_offline_reranker_rrf_evidence_remains_reviewer_eligible() -> None:
    """Regression: raw RRF rank units must not become Evidence.relevance."""

    async def offline() -> tuple[float, ...]:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "reranker offline")

    scores = await builtins._bounded_knowledge_rerank(
        offline,
        expected_count=1,
        timeout_sec=0.5,
    )
    candidate = RetrievalCandidate(
        chunk_id="policy",
        content="Security incidents must be reported within 2 hours.",
        locator="page:7",
        source_id="selected-document",
    )
    ranked = builtins._rank_knowledge_candidates(
        "incident reporting deadline",
        (candidate,),
        (),
        (candidate,),
        scores,
        limit=1,
    )

    assert len(ranked) == 1
    hit = ranked[0]
    assert hit.rank_score == pytest.approx(1 / 61)
    assert hit.relevance == pytest.approx(1)
    assert hit.ranking_mode == "rrf_fallback"

    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="knowledge.hybrid_search",
        capability_version="1.0.0",
        arguments={"query": "incident reporting deadline"},
    )
    descriptor = next(
        item for item in builtins.builtin_descriptors() if item.id == "knowledge.hybrid_search"
    )
    content_hash = hashlib.sha256(candidate.content.encode()).hexdigest()
    hit_payload = builtins._knowledge_hit_payload(hit)
    assert hit_payload["content_hash"] == content_hash
    assert hit_payload["rank_score"] == pytest.approx(1 / 61)
    assert hit_payload["score"] == pytest.approx(1)
    assert hit_payload["relevance"] == pytest.approx(1)
    result_output = {
        "query": "incident reporting deadline",
        "hits": [hit_payload],
    }
    Draft202012Validator(descriptor.output_schema).validate(result_output)
    evidence = _result_evidence(
        request=request,
        descriptor=descriptor,
        result_output=result_output,
        derived_from=(),
    )

    assert len(evidence) == 1
    assert evidence[0].relevance == pytest.approx(1)
    assert hit.rank_score < 0.5
    claim = Claim(
        claim_id=uuid5(NAMESPACE_URL, "rrf-review-claim"),
        workspace_id=request.workspace_id,
        task_id=request.task_id,
        content="Security incidents must be reported within 2 hours.",
        importance="HIGH",
        risk_level=Risk.READ,
        evidence_ids=(evidence[0].evidence_id,),
    )
    manifest = ArtifactManifest(
        artifact_id=uuid4(),
        workspace_id=request.workspace_id,
        source_task=request.task_id,
        storage_uri="s3://artifacts/rrf-review.json",
        sha256="a" * 64,
        media_type="application/json",
        claim_ids=(claim.claim_id,),
        created_at=datetime.now(UTC),
    )

    review = review_artifact(
        manifest,
        {"Summary": claim.content},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (claim,),
        evidence,
    )

    assert review.accepted
