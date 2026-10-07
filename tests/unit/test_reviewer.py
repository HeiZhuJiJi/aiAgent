import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enterprise_agent.contracts.domain import ArtifactManifest, Claim, Evidence, Risk
from enterprise_agent.runtime.reviewer import requires_reviewer, review_artifact


def review_fixture(*, grounded: bool = True):
    workspace_id, task_id, step_id, attempt_id = uuid4(), uuid4(), uuid4(), uuid4()
    content = "Supplier has an active quality certification."
    evidence = Evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        source_type="DOCUMENT",
        source_id="supplier-profile",
        locator="page:2",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        source_hash="b" * 64,
        source_version="1",
        confidence=0.9,
        relevance=0.8,
        retrieved_at=datetime.now(UTC),
        generated_by=attempt_id,
        derived_from=(),
    )
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        content="The supplier has adequate quality controls.",
        importance="HIGH",
        risk_level=Risk.WRITE,
        evidence_ids=(evidence.evidence_id,) if grounded else (),
    )
    manifest = ArtifactManifest(
        artifact_id=uuid4(),
        workspace_id=workspace_id,
        source_task=task_id,
        storage_uri="s3://artifacts/supplier-report.json",
        sha256="a" * 64,
        media_type="application/json",
        claim_ids=(claim.claim_id,),
        created_at=datetime.now(UTC),
    )
    return manifest, claim, evidence


def test_complex_artifact_review_accepts_grounded_schema_valid_report() -> None:
    manifest, claim, evidence = review_fixture()
    result = review_artifact(
        manifest,
        {"Executive Summary": "Approved", "Risk Rating": "LOW"},
        {
            "type": "object",
            "properties": {
                "Executive Summary": {"type": "string"},
                "Risk Rating": {"enum": ["LOW", "MEDIUM", "HIGH"]},
            },
            "required": ["Executive Summary", "Risk Rating"],
            "additionalProperties": False,
        },
        ("Executive Summary", "Risk Rating"),
        (claim,),
        (evidence,),
    )
    assert result.accepted


def test_review_rejects_missing_deliverable_and_unsupported_risk_claim() -> None:
    manifest, claim, evidence = review_fixture(grounded=False)
    result = review_artifact(
        manifest,
        {"Executive Summary": "Needs evidence"},
        {
            "type": "object",
            "properties": {
                "Executive Summary": {"type": "string"},
                "Risk Rating": {"type": "string"},
            },
            "required": ["Executive Summary", "Risk Rating"],
        },
        ("Executive Summary", "Risk Rating"),
        (claim,),
        (evidence,),
    )
    assert not result.accepted
    assert result.missing_deliverables == ("Risk Rating",)
    assert result.unsupported_claims == (claim.claim_id,)
    assert result.constraint_violations


def test_review_rejects_tampered_evidence_and_routes_only_complex_tasks() -> None:
    manifest, claim, evidence = review_fixture()
    tampered = evidence.model_copy(update={"content": "changed"})
    result = review_artifact(
        manifest,
        {"Summary": "Unsafe"},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (claim,),
        (tampered,),
    )
    assert not result.accepted
    assert result.unsupported_claims == (claim.claim_id,)
    assert "content hash mismatch" in result.citation_errors[0]
    assert requires_reviewer("COMPLEX_TASK")
    assert not requires_reviewer("SIMPLE_CHAT")
    with pytest.raises(ValueError):
        requires_reviewer("UNKNOWN")


def test_review_rejects_dates_and_numbers_not_present_in_cited_evidence() -> None:
    manifest, claim, evidence = review_fixture()
    fabricated = claim.model_copy(
        update={
            "content": "The supplier scored 98.5% on 2026-09-12.",
            "risk_level": Risk.READ,
        }
    )

    result = review_artifact(
        manifest,
        {"Summary": "Unsupported precision"},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (fabricated,),
        (evidence,),
    )

    assert not result.accepted
    assert fabricated.claim_id in result.unsupported_claims
    assert any("unsupported dates or numbers" in item for item in result.citation_errors)


def test_review_accepts_supported_decimals_with_equivalent_units() -> None:
    manifest, claim, evidence = review_fixture()
    source = evidence.model_copy(
        update={
            "content": "2024年平均值为2190.8mm，比2023年增加15.8%。",
            "content_hash": hashlib.sha256(
                "2024年平均值为2190.8mm，比2023年增加15.8%。".encode()
            ).hexdigest(),
        }
    )
    grounded = claim.model_copy(
        update={
            "content": "2024年平均值为2190.8毫米，比2023年增加15.8%。",
            "risk_level": Risk.READ,
        }
    )

    result = review_artifact(
        manifest,
        {"Summary": grounded.content},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (grounded,),
        (source,),
    )

    assert result.accepted


@pytest.mark.parametrize(
    ("locator", "content", "expected_error"),
    (
        ("web:web.search", "search unavailable", "no retrievable web citation locator"),
        (
            "https://search.example/api",
            json.dumps({"query": "weather tomorrow", "results": []}),
            "empty web-search result envelope",
        ),
    ),
)
def test_review_rejects_invalid_web_source_evidence(
    locator: str,
    content: str,
    expected_error: str,
) -> None:
    manifest, claim, evidence = review_fixture()
    empty_web = evidence.model_copy(
        update={
            "source_type": "WEB",
            "source_id": "web.search",
            "locator": locator,
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }
    )

    result = review_artifact(
        manifest,
        {"Summary": claim.content},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (claim,),
        (empty_web,),
    )

    assert not result.accepted
    assert result.unsupported_claims == (claim.claim_id,)
    assert any(expected_error in item for item in result.citation_errors)


def test_review_accepts_equivalent_iso_and_localized_dates() -> None:
    manifest, claim, evidence = review_fixture()
    source_content = "官方记录的截止日期为2026 年 9 月 13 日。"
    source = evidence.model_copy(
        update={
            "content": source_content,
            "content_hash": hashlib.sha256(source_content.encode()).hexdigest(),
        }
    )
    grounded = claim.model_copy(
        update={
            "content": "官方记录的截止日期为2026年9月13日。",
            "risk_level": Risk.READ,
        }
    )

    result = review_artifact(
        manifest,
        {"Summary": grounded.content},
        {"type": "object", "required": ["Summary"]},
        ("Summary",),
        (grounded,),
        (source,),
    )

    assert result.accepted


@pytest.mark.parametrize(("source", "claim"), [
    ("9/7/2026 maximum 92°F", "2026-09-07 maximum 92°F"),
    ("September 16, 2026 score 2-0", "2026-09-16 score 2-0"),
    ("16/09/2026 score 2-0", "2026-09-16 score 2-0"),
    ("气温25～34℃", "气温25℃至34℃"),
])
def test_final_review_uses_same_fact_normalization_as_generation(source, claim):
    from enterprise_agent.capabilities.builtins import _normalized_fact_text as generation_normalize
    from enterprise_agent.runtime.reviewer import _normalized_fact_text as review_normalize
    from enterprise_agent.runtime.reviewer import _unsupported_fact_tokens
    assert generation_normalize is review_normalize
    _, _, evidence = review_fixture()
    evidence = evidence.model_copy(update={"content": source})
    assert _unsupported_fact_tokens(claim, [evidence]) == ()
    assert _unsupported_fact_tokens(claim + " 999℃", [evidence]) == ("999℃",)


def test_semantic_attestation_accepts_paraphrase_but_is_bound_to_claim_and_evidence():
    from enterprise_agent.runtime.reviewer import _verified_semantic_claims
    manifest, original, original_source = review_fixture()
    text = "The stock contains thirty-one units."
    source = original_source.model_copy(update={"content": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
    claim = original.model_copy(update={"content": "The stock contains 31 units."})
    record = {"assessment": {"decision": "PASS", "acceptable": True, "answered_core_request": True, "unsupported_claim_indexes": []},
        "claim_hashes": {str(claim.claim_id): hashlib.sha256(claim.model_dump_json().encode()).hexdigest()},
        "evidence_hashes": {str(source.evidence_id): source.content_hash}}
    verified = _verified_semantic_claims((claim,), (source,), (record,))
    assert verified == frozenset((claim.claim_id,))
    assert not review_artifact(manifest, {"Summary": "31 units"}, {}, ("Summary",), (claim,), (source,)).accepted
    assert review_artifact(manifest, {"Summary": "31 units"}, {}, ("Summary",), (claim,), (source,), semantically_supported_claims=verified).accepted
    changed_claim = claim.model_copy(update={"content": "The stock contains 99 units."})
    assert not _verified_semantic_claims((changed_claim,), (source,), (record,))
    changed_source = source.model_copy(update={"content": "The stock is empty."})
    assert not _verified_semantic_claims((claim,), (changed_source,), (record,))
    rejected = {**record, "assessment": {"decision": "REVISE", "acceptable": False}}
    assert not _verified_semantic_claims((claim,), (source,), (rejected,))
    crossed = source.model_copy(update={"workspace_id": uuid4()})
    assert not _verified_semantic_claims((claim,), (crossed,), (record,))
    # Semantic acceptance never bypasses the deterministic content-hash check.
    assert not review_artifact(manifest, {"Summary": "31 units"}, {}, ("Summary",), (claim,), (changed_source,), semantically_supported_claims=verified).accepted
