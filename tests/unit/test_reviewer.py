import hashlib
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
