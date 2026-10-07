import calendar
import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    Claim,
    Evidence,
    ReviewResult,
)


def _normalized_fact_text(value: str) -> str:
    normalized = value.casefold().replace(",", "").replace("，", "")
    normalized = re.sub(
        r"(?<!\d)([0-3]?\d)/([0-3]?\d)/((?:19|20)\d{2})(?!\d)",
        lambda m: (
            f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
            if 1 <= int(m[1]) <= 12 and 1 <= int(m[2]) <= 31
            else f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
            if 13 <= int(m[1]) <= 31 and 1 <= int(m[2]) <= 12
            else m[0]
        ),
        normalized,
    )
    months = {name.casefold(): index for index in range(1, 13)
              for name in (calendar.month_name[index], calendar.month_abbr[index])}
    normalized = re.sub(
        r"\b(" + "|".join(months) + r")\.?\s+(\d{1,2})\s+((?:19|20)\d{2})\b",
        lambda m: f"{m[3]}-{months[m[1]]:02d}-{int(m[2]):02d}", normalized,
    )
    normalized = normalized.replace("％", "%").replace("°c", "℃")
    normalized = re.sub(r"(?<=\d)\s+(?=%|℃|°f)", "", normalized)
    normalized = re.sub(
        r"(?<!\d)([-+]?\d+(?:\.\d+)?)\s*(?:/|~|～|至|—|–)\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*℃",
        lambda match: f"{match.group(1)}℃/{match.group(2)}℃",
        normalized,
    )
    normalized = re.sub(
        r"((?:19|20)\d{2})\s*[-/年]\s*([01]?\d)\s*[-/月]\s*([0-3]?\d)\s*日?",
        lambda match: f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}",
        normalized,
    )
    normalized = re.sub(r"(?<=\d)\s*mm(?=$|[^a-z])", "毫米", normalized)
    normalized = re.sub(r"(?<=\d)\s*cm(?=$|[^a-z])", "厘米", normalized)
    normalized = re.sub(r"(?<=\d)\s*km(?=$|[^a-z])", "公里", normalized)
    normalized = re.sub(r"(?<=\d)\s*m(?:3|³)(?=$|[^a-z])", "立方米", normalized)
    return normalized


def _normalized_fact_tokens(value: str) -> tuple[str, ...]:
    normalized = _normalized_fact_text(value)
    dates = re.findall(
        r"(?:19|20)\d{2}(?:[-/年][01]?\d(?:[-/月][0-3]?\d日?)?)?",
        normalized,
    )
    numbers = re.findall(
        r"(?<![a-z0-9_.])[-+]?\d+(?:\.\d+)?(?:%|℃|元|美元|"
        r"人民币|毫米|厘米|公里|立方米|米|万|亿)?",
        normalized,
    )
    return tuple(dict.fromkeys((*dates, *numbers)))


def _unsupported_fact_tokens(claim: str, evidence: Sequence[Evidence]) -> tuple[str, ...]:
    combined = _normalized_fact_text("\n".join(item.content for item in evidence))
    return tuple(token for token in _normalized_fact_tokens(claim) if token not in combined)


def _web_evidence_source_error(item: Evidence) -> str | None:
    """Reject provider envelopes that do not identify a real web source."""
    if item.source_type != "WEB":
        return None
    parsed = urlsplit(item.locator)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "has no retrievable web citation locator"
    if not item.content.strip():
        return "has empty web source content"
    try:
        payload = json.loads(item.content)
    except (json.JSONDecodeError, TypeError):
        return None
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("results"), list)
        and not payload["results"]
    ):
        return "contains an empty web-search result envelope"
    return None


def requires_reviewer(task_kind: str) -> bool:
    if task_kind not in {"SIMPLE_CHAT", "COMPLEX_TASK"}:
        raise ValueError("unknown task kind")
    return task_kind == "COMPLEX_TASK"


def _verified_semantic_claims(
    claims: Sequence[Claim], evidence: Sequence[Evidence], attestations: Sequence[dict[str, JsonValue]],
) -> frozenset[UUID]:
    """Only exact, task-scoped audited judgments replace the lexical numeric heuristic."""
    by_id = {str(item.evidence_id): item for item in evidence}
    verified: set[UUID] = set()
    for record in attestations:
        assessment = record.get("assessment")
        claim_hashes, source_hashes = record.get("claim_hashes"), record.get("evidence_hashes")
        if not isinstance(assessment, dict) or not isinstance(claim_hashes, dict) or not isinstance(source_hashes, dict):
            continue
        if (assessment.get("decision") not in {"PASS", "PARTIAL_ACCEPT"}
            or assessment.get("acceptable") is not True
            or assessment.get("answered_core_request") is not True
            or assessment.get("unsupported_claim_indexes")):
            continue
        for claim in claims:
            if claim_hashes.get(str(claim.claim_id)) != hashlib.sha256(claim.model_dump_json().encode()).hexdigest():
                continue
            if claim.evidence_ids and all(
                (item := by_id.get(str(eid))) is not None
                and source_hashes.get(str(eid)) == item.content_hash
                and item.content_hash == hashlib.sha256(item.content.encode()).hexdigest()
                and item.task_id == claim.task_id and item.workspace_id == claim.workspace_id
                for eid in claim.evidence_ids
            ):
                verified.add(claim.claim_id)
    return frozenset(verified)


def review_artifact(
    manifest: ArtifactManifest,
    artifact_payload: dict[str, JsonValue],
    artifact_schema: dict[str, JsonValue],
    required_deliverables: Sequence[str],
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
    *,
    semantically_supported_claims: frozenset[UUID] = frozenset(),
) -> ReviewResult:
    """Deterministically review one complex-task artifact at a Review Step boundary."""
    constraint_violations: list[str] = []
    citation_errors: list[str] = []
    unsupported: set[UUID] = set()
    try:
        validator = Draft202012Validator(cast(dict[str, Any], artifact_schema))
        validator.check_schema(cast(dict[str, Any], artifact_schema))
        for error in sorted(
            validator.iter_errors(artifact_payload), key=lambda item: list(item.path)
        ):
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
            if source_error := _web_evidence_source_error(item):
                citation_errors.append(f"evidence {evidence_id} {source_error}")
                continue
            grounded.append(item)
        eligible = [item for item in grounded if item.confidence >= 0.5 and item.relevance >= 0.5]
        missing_fact_tokens = () if claim.claim_id in semantically_supported_claims else _unsupported_fact_tokens(claim.content, eligible)
        if missing_fact_tokens:
            citation_errors.append(
                f"claim {claim.claim_id} contains unsupported dates or numbers: "
                + ", ".join(missing_fact_tokens)
            )
        if not eligible or missing_fact_tokens:
            unsupported.add(claim.claim_id)

    result_data = {
        "accepted": not (missing or unsupported or constraint_violations or citation_errors),
        "missing_deliverables": missing,
        "unsupported_claims": tuple(sorted(unsupported, key=str)),
        "constraint_violations": tuple(constraint_violations),
        "citation_errors": tuple(citation_errors),
    }
    return ReviewResult.model_validate(result_data)
