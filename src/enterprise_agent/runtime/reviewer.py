import hashlib
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    Claim,
    Evidence,
    ReviewResult,
    Risk,
)


def requires_reviewer(task_kind: str) -> bool:
    if task_kind not in {"SIMPLE_CHAT", "COMPLEX_TASK"}:
        raise ValueError("unknown task kind")
    return task_kind == "COMPLEX_TASK"


def review_artifact(
    manifest: ArtifactManifest,
    artifact_payload: dict[str, JsonValue],
    artifact_schema: dict[str, JsonValue],
    required_deliverables: Sequence[str],
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
) -> ReviewResult:
    """Deterministically review one complex-task artifact at a Review Step boundary."""
    constraint_violations: list[str] = []
    citation_errors: list[str] = []
    unsupported: set[UUID] = set()
    try:
        validator = Draft202012Validator(cast(dict[str, Any], artifact_schema))
        validator.check_schema(cast(dict[str, Any], artifact_schema))
        for error in sorted(validator.iter_errors(artifact_payload), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            constraint_violations.append(f"schema:{path}:{error.message}")
    except SchemaError as exc:
        constraint_violations.append(f"schema-definition:{exc.message}")

    missing = tuple(
        name
        for name in required_deliverables
        if name not in artifact_payload
        or artifact_payload[name] is None
        or artifact_payload[name] == ""
    )
    claim_by_id = {item.claim_id: item for item in claims}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    for claim_id in manifest.claim_ids:
        if claim_id not in claim_by_id:
            citation_errors.append(f"artifact references unknown claim {claim_id}")
    for claim in claims:
        if claim.workspace_id != manifest.workspace_id or claim.task_id != manifest.source_task:
            unsupported.add(claim.claim_id)
            citation_errors.append(f"claim {claim.claim_id} crosses artifact boundary")
            continue
        grounded: list[Evidence] = []
        for evidence_id in claim.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                citation_errors.append(
                    f"claim {claim.claim_id} references missing evidence {evidence_id}"
                )
                continue
            if item.workspace_id != claim.workspace_id or item.task_id != claim.task_id:
                citation_errors.append(
                    f"claim {claim.claim_id} references cross-boundary evidence {evidence_id}"
                )
                continue
            actual_hash = hashlib.sha256(item.content.encode()).hexdigest()
            if actual_hash != item.content_hash:
                citation_errors.append(f"evidence {evidence_id} content hash mismatch")
                continue
            if not item.locator.strip():
                citation_errors.append(f"evidence {evidence_id} has no citation locator")
                continue
            grounded.append(item)
        if not grounded or (
            claim.risk_level != Risk.READ
            and not any(
                item.confidence >= 0.5 and item.relevance >= 0.5
                for item in grounded
            )
        ):
            unsupported.add(claim.claim_id)

    result_data = {
        "accepted": not (
            missing or unsupported or constraint_violations or citation_errors
        ),
        "missing_deliverables": missing,
        "unsupported_claims": tuple(sorted(unsupported, key=str)),
        "constraint_violations": tuple(constraint_violations),
        "citation_errors": tuple(citation_errors),
    }
    return ReviewResult.model_validate(result_data)
