from datetime import UTC, datetime, timedelta
from uuid import uuid4

from enterprise_agent.runtime.activities import (
    _schema_object_arguments,
    _select_plan_execution_candidates,
)
from enterprise_agent.storage.tables import StepRow


def _step(state: str, *, lease_delta: int | None = None) -> StepRow:
    now = datetime.now(UTC)
    return StepRow(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        state=state,
        lease_expires_at=(None if lease_delta is None else now + timedelta(seconds=lease_delta)),
    )


def test_expired_running_step_is_selected_for_fenced_takeover() -> None:
    now = datetime.now(UTC)
    expired = _step("RUNNING", lease_delta=-30)
    blocked = _step("BLOCKED")

    selected, retry_after = _select_plan_execution_candidates([expired, blocked], now)

    assert [row.id for row in selected] == [expired.id]
    assert retry_after is None


def test_active_running_step_waits_until_lease_expiry() -> None:
    now = datetime.now(UTC)
    active = _step("RUNNING", lease_delta=30)

    selected, retry_after = _select_plan_execution_candidates([active], now)

    assert selected == []
    assert retry_after is not None
    assert 0 < retry_after <= 31


def test_missing_running_lease_is_recoverable_not_terminal() -> None:
    now = datetime.now(UTC)
    orphaned = _step("RUNNING")

    selected, retry_after = _select_plan_execution_candidates([orphaned], now)

    assert [row.id for row in selected] == [orphaned.id]
    assert retry_after is None


def test_optional_null_is_omitted_when_capability_schema_requires_integer() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
    }

    result = _schema_object_arguments(
        schema,
        {"query": "请假制度", "limit": None},
        {},
    )

    assert result == {"query": "请假制度"}


def test_optional_null_is_preserved_when_capability_schema_allows_null() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["query"],
    }

    result = _schema_object_arguments(
        schema,
        {"query": "请假制度", "cursor": None},
        {},
    )

    assert result == {"query": "请假制度", "cursor": None}


def test_replanned_query_overrides_original_without_changing_document_or_identity() -> None:
    import json

    from enterprise_agent.runtime.activities import (
        _capability_arguments,
        _replanned_query_overrides,
    )

    workspace, actor, operation, document = (uuid4() for _ in range(4))
    schema = {
        "type": "object",
        "properties": {
            name: {"type": "string"}
            for name in (
                "query",
                "document_version_id",
                "workspace_id",
            )
        },
        "required": ["query", "document_version_id", "workspace_id"],
    }
    result = _capability_arguments(
        schema,
        workspace,
        actor,
        operation,
        json.dumps(
            {
                "query": "old query",
                "document_version_id": str(document),
                "workspace_id": "untrusted",
            }
        ),
        "missing leave approval step",
        context_overrides=_replanned_query_overrides(
            "knowledge.hybrid_search",
            "missing leave approval step",
        ),
    )
    assert result == {
        "query": "missing leave approval step",
        "document_version_id": str(document),
        "workspace_id": str(workspace),
    }
    assert _replanned_query_overrides("procurement.create_admission", "changed") == {}
    assert _replanned_query_overrides("web.search", None) == {}


def test_quality_replan_preserves_missing_aspects_instead_of_accepting_partial_answer() -> None:
    import pytest

    from enterprise_agent.capabilities.builtins import (
        AnswerQualityAssessment,
        _require_quality_replan,
    )
    from enterprise_agent.domain.states import DomainError

    assessment = AnswerQualityAssessment(
        decision="REPLAN",
        acceptable=False,
        answered_core_request=False,
        language_matches=True,
        appropriate_depth=False,
        supported_claim_indexes=(0,),
        missing_aspects=("judicial branch leader",),
        critique="collect the missing branch before finalizing",
    )
    with pytest.raises(DomainError, match="EVIDENCE_INSUFFICIENT.*judicial branch leader"):
        _require_quality_replan(assessment)
    for decision in ("PARTIAL_ACCEPT", "UNAVAILABLE", "REVISE", "PASS"):
        with pytest.raises(DomainError, match="EVIDENCE_INSUFFICIENT.*judicial branch leader"):
            _require_quality_replan(assessment.model_copy(update={"decision": decision}))
    for decision in ("CLARIFY",):
        _require_quality_replan(assessment.model_copy(update={"decision": decision}))
    _require_quality_replan(
        assessment.model_copy(update={"decision": "PARTIAL_ACCEPT", "missing_aspects": ()})
    )
    _require_quality_replan(None)


def test_synthesis_recovery_carries_original_question_and_refinement():
    import json

    from enterprise_agent.runtime.activities import _capability_arguments
    schema = {"type": "object", "properties": {"instruction": {"type": "string"}}, "required": ["instruction"]}
    result = _capability_arguments(schema, uuid4(), uuid4(), uuid4(),
        json.dumps({"instruction": "Compare the two inventory records"}), "revised objective",
        context_overrides={"synthesis_guidance": "Correct the unit attribution using existing evidence"})
    assert "Compare the two inventory records" in result["instruction"]
    assert "Correct the unit attribution using existing evidence" in result["instruction"]
    assert set(result) == {"instruction"}
