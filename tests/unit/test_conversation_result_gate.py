import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from enterprise_agent.contracts.domain import Evidence, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.conversation_results import _model_result_payload


def _evidence(
    *,
    workspace_id: UUID,
    task_id: UUID,
    evidence_id: UUID,
    source_type: str,
    source_id: str,
    content: str,
) -> Evidence:
    return Evidence.model_validate(
        {
            "evidence_id": evidence_id,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "step_id": uuid4(),
            "source_type": source_type,
            "source_id": source_id,
            "locator": (
                "https://example.com/source" if source_type == "WEB" else f"task:{task_id}"
            ),
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "source_hash": hashlib.sha256(("source:" + content).encode()).hexdigest(),
            "source_version": "1",
            "confidence": 1,
            "relevance": 1,
            "retrieved_at": datetime.now(UTC),
            "generated_by": uuid4(),
            "derived_from": [],
        }
    )


def _result_content(evidence_ids: list[UUID]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "decision": "ADMIT",
            "risk_level": "LOW",
            "summary": "Verified answer.",
            "key_findings": ["Verified answer."],
            "missing_information": [],
            "recommendation": "Use the verified answer.",
            "evidence_sufficient": True,
            "claims": [
                {
                    "schema_version": 1,
                    "claim_id": str(uuid4()),
                    "content": "Verified answer.",
                    "importance": "HIGH",
                    "risk_level": "READ",
                    "evidence_ids": [str(item) for item in evidence_ids],
                }
            ],
        }
    )


def test_materialization_gate_accepts_claims_bound_to_task_source_evidence() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = _evidence(
        workspace_id=workspace_id,
        task_id=task_id,
        evidence_id=source_id,
        source_type="WEB",
        source_id="web.search",
        content="Verified answer.",
    )
    result = _evidence(
        workspace_id=workspace_id,
        task_id=task_id,
        evidence_id=uuid4(),
        source_type="DERIVED",
        source_id="model.structured_output",
        content=_result_content([source_id]),
    )

    assert _model_result_payload((source, result), task_id).claims[0].evidence_ids == (
        source_id,
    )


def test_materialization_gate_rejects_factual_answer_without_claims() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    payload = json.loads(_result_content([uuid4()]))
    payload["claims"] = []
    result = _evidence(
        workspace_id=workspace_id,
        task_id=task_id,
        evidence_id=uuid4(),
        source_type="DERIVED",
        source_id="model.structured_output",
        content=json.dumps(payload),
    )

    with pytest.raises(DomainError) as raised:
        _model_result_payload((result,), task_id)

    assert raised.value.code is FailureCode.CONSISTENCY_VIOLATION


def test_materialization_gate_rejects_claim_with_unknown_evidence() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    result = _evidence(
        workspace_id=workspace_id,
        task_id=task_id,
        evidence_id=uuid4(),
        source_type="DERIVED",
        source_id="model.structured_output",
        content=_result_content([uuid4()]),
    )

    with pytest.raises(DomainError) as raised:
        _model_result_payload((result,), task_id)

    assert raised.value.code is FailureCode.CONSISTENCY_VIOLATION
