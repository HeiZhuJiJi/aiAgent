import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import (
    CapabilityRequest,
    Evidence,
    FailureCode,
    Risk,
    TaskGoal,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import (
    ModelConfig,
    ModelGateway,
    ModelInvocationError,
    ModelUsage,
)
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.storage.tables import (
    AttemptRow,
    AuditRow,
    ClaimEvidenceLinkRow,
    ClaimRow,
    EvidenceRow,
    RuntimeGuardRow,
    StepRow,
    TaskBudgetRow,
    TaskRow,
    UsageRecordRow,
    Workspace,
)


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _config(model: str) -> ModelConfig:
    return ModelConfig(
        provider="OpenAICompatible",
        base_url=f"https://{model}.example/v1",
        model=model,
        external=False,
        version="1",
    )


def _request(
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
) -> CapabilityRequest:
    return CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=1,
        capability_id="model.structured_output",
        capability_version="1.0.0",
        arguments={"instruction": "Assess the supplied evidence."},
    )


async def _seed(database):
    workspace_id, task_id, step_id, attempt_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    goal = TaskGoal(
        goal="Assess evidence",
        constraints=("Use evidence",),
        deliverables=("Assessment",),
        workspace_id=workspace_id,
        time_budget_sec=600,
        token_budget=10_000,
        cost_budget=10,
    )
    evidence = Evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        source_type="DOCUMENT",
        source_id="test-document",
        locator="paragraph:1",
        content="The verified policy statement is present.",
        content_hash=hashlib.sha256(b"The verified policy statement is present.").hexdigest(),
        source_hash="a" * 64,
        source_version="1",
        confidence=1,
        relevance=1,
        retrieved_at=datetime.now(UTC),
        generated_by=attempt_id,
        derived_from=(),
    )
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="model-handler-fencing"))
        await session.flush()
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="RUNNING",
                goal_payload=goal.model_dump(mode="json"),
            )
        )
        await session.flush()
        session.add(new_task_budget(task_id, goal))
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        session.add(
            StepRow(
                id=step_id,
                workspace_id=workspace_id,
                task_id=task_id,
                state="RUNNING",
                fencing_token=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.flush()
        session.add(
            AttemptRow(
                id=attempt_id,
                workspace_id=workspace_id,
                step_id=step_id,
                worker_id="worker-a",
                fencing_token=1,
                state="RUNNING",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                trace_id="model-handler-fencing",
            )
        )
        await session.flush()
        session.add(
            EvidenceRow(
                id=evidence.evidence_id,
                workspace_id=workspace_id,
                step_id=step_id,
                attempt_id=attempt_id,
                payload=evidence.model_dump(mode="json"),
            )
        )
    return workspace_id, task_id, step_id, attempt_id, evidence.evidence_id


async def _model_chain(*_args):
    return (
        (
            (_config("primary"), SecretStr("primary-secret")),
            (_config("fallback"), SecretStr("fallback-secret")),
        ),
        None,
        None,
    )


async def test_failed_fallback_is_audited_and_all_consumed_tokens_are_charged(
    database,
    monkeypatch,
) -> None:
    workspace_id, task_id, step_id, attempt_id, _ = await _seed(database)
    request = _request(workspace_id, task_id, step_id, attempt_id)
    monkeypatch.setattr(builtins, "_task_model_chain", _model_chain)

    async def fail_structured(self, _messages, _schema, _classification, **options):
        before_call = options.get("before_call")
        if before_call is not None:
            await before_call()
        usage = (
            ModelUsage(input_tokens=11, output_tokens=4)
            if self.config.model == "primary"
            else ModelUsage(input_tokens=7, output_tokens=3)
        )
        after_call = options.get("after_call")
        if after_call is not None:
            await after_call(usage)
        raise ModelInvocationError(
            FailureCode.LLM_SCHEMA_ERROR,
            "scripted invalid structured output",
            usage,
        )

    monkeypatch.setattr(ModelGateway, "structured", fail_structured)

    with pytest.raises(DomainError) as raised:
        await builtins._model_handler(database, request)

    assert raised.value.code is FailureCode.LLM_SCHEMA_ERROR
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        audit = await session.scalar(
            select(AuditRow).where(
                AuditRow.workspace_id == workspace_id,
                AuditRow.resource_id == request.operation_id,
                AuditRow.action == "model.fallback.attempted",
            )
        )
        claim_count = await session.scalar(
            select(func.count()).select_from(ClaimRow).where(
                ClaimRow.workspace_id == workspace_id,
                ClaimRow.task_id == task_id,
            )
        )
        usage_rows = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.task_id == task_id,
                    UsageRecordRow.kind == "LLM_CALL",
                )
            )
        ).all()

    assert budget is not None
    assert budget.used_llm_calls == 2
    assert budget.used_tokens == 25
    assert audit is not None
    assert audit.result == {
        "trigger": FailureCode.LLM_SCHEMA_ERROR.value,
        "outcome": "FAILED",
        "failure_code": FailureCode.LLM_SCHEMA_ERROR.value,
        "primary_provider": "OpenAICompatible",
        "primary_model": "primary",
        "fallback_provider": "OpenAICompatible",
        "fallback_model": "fallback",
    }
    assert "secret" not in str(audit.result).casefold()
    assert claim_count == 0
    actual_usage_rows = [row for row in usage_rows if row.detail.get("post_call_actual") is True]
    assert sorted(row.tokens for row in actual_usage_rows) == [10, 15]
    assert all(row.units == 0 for row in actual_usage_rows)
    assert len([row for row in usage_rows if row.units == 1 and row.tokens == 0]) == 2


async def test_zombie_model_result_cannot_persist_claims_or_evidence_links(
    database,
    monkeypatch,
) -> None:
    workspace_id, task_id, step_id, attempt_id, evidence_id = await _seed(database)
    request = _request(workspace_id, task_id, step_id, attempt_id)

    monkeypatch.setattr(builtins, "_task_model_chain", _model_chain)

    async def return_after_takeover(self, _messages, schema, _classification, **options):
        before_call = options.get("before_call")
        if before_call is not None:
            await before_call()
        if self.config.model == "primary":
            raise DomainError(FailureCode.LLM_SERVER_ERROR, "primary became unavailable")
        replacement_attempt_id = uuid4()
        async with database.begin() as session:
            old_attempt = await session.get(AttemptRow, attempt_id)
            step = await session.get(StepRow, step_id)
            assert old_attempt is not None and step is not None
            old_attempt.state = "UNKNOWN"
            old_attempt.completed_at = datetime.now(UTC)
            step.fencing_token = 2
            step.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            session.add(
                AttemptRow(
                    id=replacement_attempt_id,
                    workspace_id=workspace_id,
                    step_id=step_id,
                    worker_id="worker-b",
                    fencing_token=2,
                    state="RUNNING",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    trace_id="replacement-attempt",
                )
            )
        result = schema.model_validate(
            {
                "decision": "ADMIT",
                "risk_level": "LOW",
                "summary": "A stale result that must not commit.",
                "key_findings": ["Verified statement"],
                "missing_information": [],
                "recommendation": "Do not persist this zombie result.",
                "evidence_sufficient": True,
                "claims": [
                    {
                        "content": "The verified policy statement is present.",
                        "importance": "HIGH",
                        "risk_level": Risk.READ.value,
                        "evidence_ids": [str(evidence_id)],
                    }
                ],
            }
        )
        usage = ModelUsage(input_tokens=5, output_tokens=2)
        after_call = options.get("after_call")
        if after_call is not None:
            await after_call(usage)
        return result, usage

    monkeypatch.setattr(ModelGateway, "structured", return_after_takeover)

    with pytest.raises(DomainError) as raised:
        await builtins._model_handler(database, request)

    assert raised.value.code is FailureCode.STALE_ATTEMPT
    async with database() as session:
        claim_count = await session.scalar(
            select(func.count()).select_from(ClaimRow).where(
                ClaimRow.workspace_id == workspace_id,
                ClaimRow.task_id == task_id,
            )
        )
        link_count = await session.scalar(
            select(func.count()).select_from(ClaimEvidenceLinkRow).where(
                ClaimEvidenceLinkRow.workspace_id == workspace_id
            )
        )
        started_audits = await session.scalar(
            select(func.count()).select_from(AuditRow).where(
                AuditRow.workspace_id == workspace_id,
                AuditRow.resource_id == request.operation_id,
                AuditRow.action == "model.fallback.started",
            )
        )
        terminal_audits = await session.scalar(
            select(func.count()).select_from(AuditRow).where(
                AuditRow.workspace_id == workspace_id,
                AuditRow.resource_id == request.operation_id,
                AuditRow.action == "model.fallback.attempted",
            )
        )

    assert claim_count == 0
    assert link_count == 0
    assert started_audits == 1
    assert terminal_audits == 0


@pytest.mark.parametrize("skill,source_type", [("web-research", "WEB"), ("document-analysis", "DOCUMENT")])
async def test_generation_http_request_contains_question_and_collected_evidence(database, monkeypatch, skill, source_type):
    import json

    import httpx

    from tests.fakes.model import ScriptedModel

    workspace_id, task_id, step_id, attempt_id, evidence_id = await _seed(database)
    question = "What does the inventory record say?"
    content = "Inventory record\n网页正文摘录：\nThe inventory contains 31 items."
    async with database.begin() as session:
        row = await session.get(EvidenceRow, evidence_id)
        assert row is not None
        row.payload = {**row.payload, "source_type": source_type,
            "locator": "https://inventory.example.gov/report" if source_type == "WEB" else "inventory.txt:1",
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()}
    request = _request(workspace_id, task_id, step_id, attempt_id).model_copy(
        update={"arguments": {"instruction": question}})

    async def selected_skill(*args):
        return skill
    async def chain(*args):
        return (((_config("primary"), SecretStr("unused-test-key")),), question, skill)
    async def context(*args):
        value = {"original_request": question, "resolved_request": question}
        if skill == "web-research":
            value["research_progress"] = await builtins._load_research_progress(database, request)
        return value
    monkeypatch.setattr(builtins, "_task_requested_skill", selected_skill)
    monkeypatch.setattr(builtins, "_task_model_chain", chain)
    monkeypatch.setattr(builtins, "_task_user_context", context)
    answer = {"answer_status": "ANSWERED", "answer": "The inventory contains 31 items.",
        "evidence_ids": [str(evidence_id)], "claims": [{"content": "The inventory contains 31 items.",
        "supports": [{"evidence_id": str(evidence_id), "quote": "The inventory contains 31 items."}]}]}
    quality = {"coverage": [{"requirement": question, "status": "ANSWERED", "entity_alignment": "MATCH", "period_alignment": "MATCH", "evidence_ids": [str(evidence_id)]}],
               "decision": "PASS", "acceptable": True, "answered_core_request": True,
               "language_matches": True, "appropriate_depth": True, "critique": "Supported by the inventory record."}
    if skill == "web-research":
        from enterprise_agent.contracts.domain import Evidence
        async with database() as session:
            source_row = await session.get(EvidenceRow, evidence_id)
            source = Evidence.model_validate(source_row.payload)
        partial_quality = builtins.AnswerQualityAssessment(
            decision="REPLAN", acceptable=False, answered_core_request=False,
            language_matches=True, appropriate_depth=True, supported_claim_indexes=(1,),
            missing_aspects=("Additional requested detail",),
        )
        await builtins._record_answer_quality_assessment(
            database, request.model_copy(update={"operation_id": uuid4()}), partial_quality,
            candidate=builtins.WebStructuredAnswer.model_validate(answer), evidence=(source,),
        )
        other_workspace = request.model_copy(update={"workspace_id": uuid4()})
        assert await builtins._load_answer_draft(database, other_workspace, (source,)) == ()
        assert await builtins._load_research_progress(database, other_workspace) == {}
        assert await builtins._load_research_progress(database, request.model_copy(update={"task_id": uuid4()})) == {}
        progress = await builtins._load_research_progress(database, request)
        assert progress["open_gaps"] == ["Additional requested detail"]
    transport = ScriptedModel([json.dumps(answer), json.dumps(quality)])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(builtins.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    result = await builtins._model_handler(database, request)
    assert result.outcome == "SUCCEEDED"
    async with database() as session:
        attestation = await session.scalar(select(AuditRow).where(
            AuditRow.task_id == task_id, AuditRow.action == "model.grounding.accepted"))
        assert attestation is not None
        assert attestation.result["assessment"]["decision"] == "PASS"
        assert str(evidence_id) in attestation.result["evidence_hashes"]
    review_request = request.model_copy(update={"capability_id": "runtime.review", "operation_id": uuid4()})
    reviewed = await builtins._review_handler(database, review_request)
    assert reviewed.outcome == "SUCCEEDED"
    assert reviewed.output["accepted"] is True
    generation = transport.payloads[0]
    sent = json.loads(generation["messages"][1]["content"])
    if skill == "web-research":
        assert sent["supported_answer_draft"] == [claim.model_dump(mode="json") for claim in builtins.WebStructuredAnswer.model_validate(answer).claims]
        assert "one coherent updated answer" in sent["incremental_completion"]
        assert sent["research_progress"] == progress
        checked = json.loads(transport.payloads[1]["messages"][1]["content"])
        # Progress still guides generation above, but is not an independent
        # review's source of user requirements. Retain its audited history.
        assert "research_progress" not in checked
        assert "resolved_search_query" not in checked
        assert checked["original_request"] == question
        assert checked["reference_resolution_hint"] is None
        async with database() as session:
            records = (await session.scalars(select(AuditRow).where(
                AuditRow.task_id == task_id, AuditRow.action == "model.validator.assessed"))).all()
        manifest = next(row.result["reviewed_evidence_manifest"] for row in records
                        if "reviewed_evidence_manifest" in row.result)
        assert manifest[0]["evidence_id"] == str(evidence_id)
        reviewed_text = checked["evidence"][0]["content"]
        assert manifest[0]["excerpt_sha256"] == hashlib.sha256(reviewed_text.encode()).hexdigest()
        assert manifest[0]["excerpt_characters"] == len(reviewed_text)
    else:
        assert "supported_answer_draft" not in sent
        assert "research_progress" not in sent
    assert sent["instruction"] == question
    assert sent["evidence"][0]["evidence_id"] == str(evidence_id)
    assert "The inventory contains 31 items." in sent["evidence"][0]["content"]
    assert sent["evidence"][0]["locator"].endswith("report" if source_type == "WEB" else "inventory.txt:1")
    assert generation["model"] == "primary"
    assert any("schema" in message["content"].lower() or "answer_status" in message["content"] for message in generation["messages"])


async def test_research_progress_uses_latest_revision_and_replay_is_idempotent(database):
    workspace_id, task_id, step_id, attempt_id, _ = await _seed(database)
    request = _request(workspace_id, task_id, step_id, attempt_id)
    missing = builtins.AnswerQualityAssessment(
        decision="REPLAN", acceptable=False, answered_core_request=False,
        language_matches=True, appropriate_depth=True, missing_aspects=("Release date",),
    )
    completed = missing.model_copy(update={"decision": "PASS", "acceptable": True,
        "answered_core_request": True, "missing_aspects": ()})
    await builtins._record_answer_quality_assessment(database, request, missing)
    assert (await builtins._load_research_progress(database, request))["open_gaps"] == ["Release date"]
    await builtins._record_answer_quality_assessment(database, request, completed)
    await builtins._record_answer_quality_assessment(database, request, completed)
    progress = await builtins._load_research_progress(database, request)
    assert progress["previous_decision"] == "PASS"
    assert progress["open_gaps"] == []
    async with database() as session:
        rows = (await session.scalars(select(AuditRow).where(
            AuditRow.task_id == task_id, AuditRow.action == "model.validator.assessed"))).all()
    assert len(rows) == 2


async def test_unavailable_review_retains_unapproved_candidate_with_context_isolation(database):
    workspace, task, step, attempt, evidence_id = await _seed(database)
    request = _request(workspace, task, step, attempt)
    async with database() as session:
        row = await session.get(EvidenceRow, evidence_id)
        source = Evidence.model_validate(row.payload)
    candidate = builtins.WebStructuredAnswer(answer=source.content, answer_status="ANSWERED",
        evidence_ids=(source.evidence_id,), claims=(builtins.GroundedAnswerClaim(content=source.content,
            supports=(builtins.EvidenceSupport(evidence_id=source.evidence_id, quote=source.content),)),))
    context_hash = builtins._pending_review_context('Assess evidence', None)
    await builtins._record_answer_quality_assessment(database, request, None,
        candidate=candidate, evidence=(source,), review_context_hash=context_hash)
    restored = await builtins._load_pending_review(database, request, context_hash, (source,))
    assert restored == candidate
    assert await builtins._load_pending_review(database, request, 'changed-question', (source,)) is None
    assert await builtins._load_pending_review(database, request.model_copy(update={'task_id': uuid4()}), context_hash, (source,)) is None
    assert await builtins._load_pending_review(database, request.model_copy(update={'workspace_id': uuid4()}), context_hash, (source,)) is None
    changed = source.model_copy(update={'content': source.content + ' Modified.'})
    assert await builtins._load_pending_review(database, request, context_hash, (changed,)) is None
    async with database() as session:
        record = await session.scalar(select(AuditRow).where(AuditRow.task_id == task,
            AuditRow.action == 'model.validator.assessed'))
        assert record.result['outcome'] == 'UNAVAILABLE'
        assert record.result['pending_review']['status'] == 'UNREVIEWED'
        assert 'supported_draft' not in record.result
        assert await session.scalar(select(func.count()).select_from(ClaimRow).where(ClaimRow.task_id == task)) == 0


async def test_pending_review_resumes_with_one_model_call_and_still_requires_acceptance(database, monkeypatch):
    import json

    import httpx

    from tests.fakes.model import ScriptedModel

    workspace, task, step, attempt, evidence_id = await _seed(database)
    question = 'What does the policy state?'
    request = _request(workspace, task, step, attempt).model_copy(update={'arguments': {'instruction': question}})
    async with database() as session:
        row = await session.get(EvidenceRow, evidence_id)
        source = Evidence.model_validate(row.payload)
    candidate = builtins.WebStructuredAnswer(answer=source.content, answer_status='ANSWERED',
        evidence_ids=(source.evidence_id,), claims=(builtins.GroundedAnswerClaim(content=source.content,
            supports=(builtins.EvidenceSupport(evidence_id=source.evidence_id, quote=source.content),)),))
    await builtins._record_answer_quality_assessment(database, request.model_copy(update={'operation_id': uuid4()}), None,
        candidate=candidate, evidence=(source,), review_context_hash=builtins._pending_review_context(question, None))

    async def skill(*args):
        return 'web-research'
    async def chain(*args):
        return (((_config('primary'), SecretStr('test-key')),), question, 'web-research')
    async def context(*args):
        return {'original_request': question, 'resolved_request': question}
    monkeypatch.setattr(builtins, '_task_requested_skill', skill)
    monkeypatch.setattr(builtins, '_task_model_chain', chain)
    monkeypatch.setattr(builtins, '_task_user_context', context)
    quality = {'coverage': [{'requirement': question, 'status': 'ANSWERED', 'entity_alignment': 'MATCH',
        'period_alignment': 'MATCH', 'evidence_ids': [str(evidence_id)]}], 'decision': 'PASS', 'acceptable': True,
        'answered_core_request': True, 'language_matches': True, 'appropriate_depth': True}
    transport = ScriptedModel([json.dumps(quality)])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(builtins.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
    result = await builtins._model_handler(database, request)
    assert result.outcome == 'SUCCEEDED'
    assert len(transport.payloads) == 1
    assert 'Independently assess' in transport.payloads[0]['messages'][0]['content']
    async with database() as session:
        accepted = await session.scalar(select(AuditRow).where(AuditRow.task_id == task,
            AuditRow.action == 'model.grounding.accepted'))
        assert accepted is not None and accepted.result['assessment']['decision'] == 'PASS'


async def test_local_reread_replaces_model_context_and_requires_fresh_review(database, monkeypatch):
    import json

    import httpx

    from tests.fakes.model import ScriptedModel

    workspace_id, task_id, step_id, attempt_id, evidence_id = await _seed(database)
    question = "What are the inventory count and warehouse?"
    content = "Inventory record\n网页正文摘录：\nThe inventory contains 31 items. Warehouse: Alpha."
    async with database.begin() as session:
        row = await session.get(EvidenceRow, evidence_id)
        row.payload = {**row.payload, "source_type": "WEB", "locator": "https://inventory.example.gov/report",
                       "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()}
    request = _request(workspace_id, task_id, step_id, attempt_id).model_copy(
        update={"arguments": {"instruction": question}})
    async def selected_skill(*args):
        return "web-research"
    async def chain(*args):
        return (((_config("primary"), SecretStr("unused-test-key")),), question, "web-research")
    async def context(*args):
        return {"original_request": question, "resolved_request": question}
    monkeypatch.setattr(builtins, "_task_requested_skill", selected_skill)
    monkeypatch.setattr(builtins, "_task_model_chain", chain)
    monkeypatch.setattr(builtins, "_task_user_context", context)
    pack = builtins._analysis_evidence_payload
    def incomplete_preview(*args, **kwargs):
        payload = pack(*args, **kwargs)
        if "missing warehouse" not in str(args):
            for item in payload:
                item["content"] = item["content"].replace(" Warehouse: Alpha.", "")
                item["excerpt_manifest"] = {"source_fully_included": False}
        return payload
    monkeypatch.setattr(builtins, "_analysis_evidence_payload", incomplete_preview)
    def answer(text):
        return {"answer_status": "ANSWERED", "answer": text, "evidence_ids": [str(evidence_id)],
                "claims": [{"content": text, "supports": [{"evidence_id": str(evidence_id), "quote": text}]}]}
    missing = {"decision": "REPLAN", "acceptable": False, "answered_core_request": False,
               "language_matches": True, "appropriate_depth": True, "critique": "missing warehouse",
               "missing_aspects": ["warehouse"], "coverage": [{"requirement": "warehouse", "status": "MISSING_EVIDENCE"}]}
    passed = {"decision": "PASS", "acceptable": True, "answered_core_request": True,
              "language_matches": True, "appropriate_depth": True, "critique": "Both facts supported.",
              "coverage": [{"requirement": question, "status": "ANSWERED", "entity_alignment": "MATCH",
                            "period_alignment": "MATCH", "evidence_ids": [str(evidence_id)]}]}
    transport = ScriptedModel([json.dumps(answer("The inventory contains 31 items.")), json.dumps(missing),
                               json.dumps(answer("The inventory contains 31 items. Warehouse: Alpha.")), json.dumps(passed)])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(builtins.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    result = await builtins._model_handler(database, request)
    assert result.outcome == "SUCCEEDED"
    assert len(transport.payloads) == 4
    first = json.loads(transport.payloads[0]["messages"][1]["content"])
    repaired = json.loads(transport.payloads[2]["messages"][1]["content"])
    reviewed = json.loads(transport.payloads[3]["messages"][1]["content"])
    assert "Warehouse: Alpha" not in first["evidence"][0]["content"]
    assert "Warehouse: Alpha" in repaired["evidence"][0]["content"]
    assert repaired["instruction"] == question
    assert "Warehouse: Alpha" in reviewed["evidence"][0]["content"]


async def test_conflicting_review_is_corrected_without_regenerating_supported_answer(database, monkeypatch):
    import json

    import httpx

    from tests.fakes.model import ScriptedModel

    workspace, task, step, attempt, evidence_id = await _seed(database)
    question = 'What does the policy state?'
    request = _request(workspace, task, step, attempt).model_copy(update={'arguments': {'instruction': question}})
    async with database() as session:
        row = await session.get(EvidenceRow, evidence_id)
        source = Evidence.model_validate(row.payload)
    candidate = builtins.WebStructuredAnswer(answer=source.content, answer_status='ANSWERED',
        evidence_ids=(source.evidence_id,), claims=(builtins.GroundedAnswerClaim(content=source.content,
            supports=(builtins.EvidenceSupport(evidence_id=source.evidence_id, quote=source.content),)),))
    await builtins._record_answer_quality_assessment(database, request.model_copy(update={'operation_id': uuid4()}), None,
        candidate=candidate, evidence=(source,), review_context_hash=builtins._pending_review_context(question, None))

    async def skill(*args):
        return 'web-research'
    async def chain(*args):
        return (((_config('primary'), SecretStr('test-key')),), question, 'web-research')
    async def context(*args):
        return {'original_request': question, 'resolved_request': question}
    monkeypatch.setattr(builtins, '_task_requested_skill', skill)
    monkeypatch.setattr(builtins, '_task_model_chain', chain)
    monkeypatch.setattr(builtins, '_task_user_context', context)
    quality = {'coverage': [{'requirement': question, 'status': 'ANSWERED', 'entity_alignment': 'MATCH',
        'period_alignment': 'MATCH', 'evidence_ids': [str(evidence_id)]}], 'decision': 'PASS', 'acceptable': True,
        'answered_core_request': True, 'language_matches': True, 'appropriate_depth': True}
    conflicting = {**quality, "coverage": [{**quality["coverage"][0], "period_alignment": "PARTIAL"}]}
    transport = ScriptedModel([json.dumps(conflicting), json.dumps(quality)])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(builtins.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
    result = await builtins._model_handler(database, request)
    assert result.outcome == 'SUCCEEDED'
    assert len(transport.payloads) == 2
    assert "conflicts" in json.dumps(transport.payloads[1])
    assert all("Independently assess" in payload["messages"][0]["content"] for payload in transport.payloads)
    assert 'Independently assess' in transport.payloads[0]['messages'][0]['content']
    async with database() as session:
        accepted = await session.scalar(select(AuditRow).where(AuditRow.task_id == task,
            AuditRow.action == 'model.grounding.accepted'))
        assert accepted is not None and accepted.result['assessment']['decision'] == 'PASS'



async def test_review_patch_preserves_facts_and_is_independently_reviewed(database, monkeypatch):
    import json

    import httpx

    from tests.fakes.model import ScriptedModel

    workspace, task, step, attempt, evidence_id = await _seed(database)
    question = 'What does the policy state?'
    request = _request(workspace, task, step, attempt).model_copy(update={'arguments': {'instruction': question}})
    async with database() as session:
        row = await session.get(EvidenceRow, evidence_id)
        source = Evidence.model_validate(row.payload)
    candidate = builtins.WebStructuredAnswer(answer=source.content + " Missing detail.", answer_status='ANSWERED',
        evidence_ids=(source.evidence_id,), claims=(builtins.GroundedAnswerClaim(content=source.content,
            supports=(builtins.EvidenceSupport(evidence_id=source.evidence_id, quote=source.content),)),))
    await builtins._record_answer_quality_assessment(database, request.model_copy(update={'operation_id': uuid4()}), None,
        candidate=candidate, evidence=(source,), review_context_hash=builtins._pending_review_context(question, None))

    async def skill(*args):
        return 'web-research'
    async def chain(*args):
        return (((_config('primary'), SecretStr('test-key')),), question, 'web-research')
    async def context(*args):
        return {'original_request': question, 'resolved_request': question}
    monkeypatch.setattr(builtins, '_task_requested_skill', skill)
    monkeypatch.setattr(builtins, '_task_model_chain', chain)
    monkeypatch.setattr(builtins, '_task_user_context', context)
    quality = {'coverage': [{'requirement': question, 'status': 'ANSWERED', 'entity_alignment': 'MATCH',
        'period_alignment': 'MATCH', 'evidence_ids': [str(evidence_id)]}], 'decision': 'PASS', 'acceptable': True,
        'answered_core_request': True, 'language_matches': True, 'appropriate_depth': True}
    conflicting = {**quality, "decision": "REVISE", "acceptable": False,
        "repair_patch": {"edits": [{"before": " Missing detail.", "after": ""}]}}
    transport = ScriptedModel([json.dumps(conflicting), json.dumps(quality)])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(builtins.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
    result = await builtins._model_handler(database, request)
    assert result.outcome == 'SUCCEEDED'
    assert len(transport.payloads) == 2
    second = json.loads(transport.payloads[1]['messages'][1]['content'])
    assert second['candidate']['answer'] == source.content
    assert second['candidate']['claims'][0]['content'] == source.content
    assert second['citation_quotes'][0]['quote'] == source.content
    assert second['citation_quotes'][0]['evidence_id'] == str(source.evidence_id)
    assert all("Independently assess" in payload["messages"][0]["content"] for payload in transport.payloads)
    assert 'Independently assess' in transport.payloads[0]['messages'][0]['content']
    async with database() as session:
        accepted = await session.scalar(select(AuditRow).where(AuditRow.task_id == task,
            AuditRow.action == 'model.grounding.accepted'))
        assert accepted is not None and accepted.result['assessment']['decision'] == 'PASS'

