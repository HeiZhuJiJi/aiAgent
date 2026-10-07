"""Shared real-PostgreSQL fixtures for Completion integration tests."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.completion.assessment import assess_requirement
from enterprise_agent.contracts.domain import (
    Claim,
    ContractVersion,
    Evidence,
    ExecutionSnapshot,
    Requirement,
    RequirementType,
    VerificationGates,
    VerificationMetrics,
    VerificationResult,
    VersionRef,
)
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.provenance import persist_claim
from enterprise_agent.storage.tables import StepRow, UserRow
from tests.integration.test_fencing import evidence, seed
from tests.integration.test_immutable_plans import plan


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@dataclass(frozen=True)
class CompletionFixture:
    workspace_id: UUID
    task_id: UUID
    evidence_step_id: UUID
    review_step_id: UUID
    user_id: UUID
    snapshot: ExecutionSnapshot
    contract: ContractVersion
    requirement: Requirement


def _version_ref(name: str) -> VersionRef:
    return VersionRef(identifier=name, version="1.0.0", sha256="a" * 64)


def make_snapshot(workspace_id: UUID) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id=uuid4(),
        workspace_id=workspace_id,
        runtime_version="runtime.v1",
        workflow_definition_version="workflow.v1",
        planner_prompt_version=_version_ref("planner"),
        router_prompt_version=_version_ref("router"),
        reviewer_prompt_version=_version_ref("reviewer"),
        model_config_versions=(_version_ref("model"),),
        skill_versions=(),
        capability_versions=(_version_ref("capability"),),
        policy_version=_version_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=_version_ref("context"),
        schema_versions=(_version_ref("domain"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )


async def prepare(database) -> CompletionFixture:
    workspace_id, task_id, evidence_step_id = await seed(database)
    review_step_id = uuid4()
    user_id = uuid4()
    snapshot = make_snapshot(workspace_id)
    task_plan = plan(workspace_id, task_id, snapshot.snapshot_id)
    contract = ContractVersion(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        version=1,
        content_hash="b" * 64,
        goal="Produce a verified report",
        completion_policy={"allow_partial": True, "version": "completion.v1"},
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    requirement = Requirement(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        contract_version_id=contract.id,
        key="verified-report",
        type=RequirementType.VERIFICATION,
        mandatory=True,
        description="The report must be supported by verified evidence.",
        completion_rule="The verification gates pass.",
    )
    async with database.begin() as session:
        session.add(
            UserRow(
                id=user_id,
                email=f"completion-{user_id}@example.invalid",
                normalized_email=f"completion-{user_id}@example.invalid",
                password_hash="test-only",
            )
        )
        session.add(
            StepRow(
                id=review_step_id,
                workspace_id=workspace_id,
                task_id=task_id,
                state="READY",
            )
        )
        await persist_snapshot(session, snapshot)
        await persist_plan(session, task_plan)
        from enterprise_agent.storage.completion import create_contract_version, create_requirement

        await create_contract_version(session, contract)
        await create_requirement(session, requirement)
    return CompletionFixture(
        workspace_id=workspace_id,
        task_id=task_id,
        evidence_step_id=evidence_step_id,
        review_step_id=review_step_id,
        user_id=user_id,
        snapshot=snapshot,
        contract=contract,
        requirement=requirement,
    )


async def evidence_and_claim(database, fixture: CompletionFixture) -> tuple[Evidence, Claim]:
    evidence_attempt_id = uuid4()
    async with database.begin() as session:
        attempt = await acquire_attempt(
            session,
            fixture.workspace_id,
            fixture.evidence_step_id,
            evidence_attempt_id,
            "completion-evidence-worker",
            "completion-evidence-trace",
        )
        item = evidence(
            fixture.workspace_id,
            fixture.task_id,
            fixture.evidence_step_id,
            evidence_attempt_id,
        )
        await commit_evidence(
            session,
            fixture.workspace_id,
            evidence_attempt_id,
            attempt.fencing_token,
            (item,),
        )
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=fixture.workspace_id,
        task_id=fixture.task_id,
        content="The report is supported by the collected record.",
        importance="HIGH",
        risk_level="READ",
        evidence_ids=(item.evidence_id,),
    )
    async with database.begin() as session:
        await persist_claim(session, claim)
    return item, claim


async def review_attempt(database, fixture: CompletionFixture):
    async with database.begin() as session:
        return await acquire_attempt(
            session,
            fixture.workspace_id,
            fixture.review_step_id,
            uuid4(),
            "completion-review-worker",
            "completion-review-trace",
        )


def verification_for(
    fixture: CompletionFixture,
    attempt,
    item: Evidence,
    claim: Claim,
    *,
    entity_match: bool = True,
    certainty: str = "CERTAIN",
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        workspace_id=fixture.workspace_id,
        task_id=fixture.task_id,
        claim_ids=(claim.claim_id,),
        evidence_ids=(item.evidence_id,),
        gates=VerificationGates(
            entity_match=entity_match,
            temporal_match=True,
            evidence_support=True,
            authorization=True,
            operation_certainty=certainty,
            schema_validity=True,
        ),
        metrics=VerificationMetrics(
            completeness=1.0,
            freshness=1.0,
            source_quality=1.0,
            relevance=1.0,
            redundancy=1.0,
        ),
        verifier_version="integration-verifier.v1",
        policy_version="completion.v1",
        input_snapshot_hash="c" * 64,
        created_at=datetime.now(UTC),
        attempt_id=attempt.id,
        fencing_token=attempt.fencing_token,
    )


def assessment_for(fixture: CompletionFixture, verification: VerificationResult):
    return assess_requirement(
        fixture.requirement,
        verification,
        assessment_id=uuid4(),
        evaluator_version="completion-policy.v1",
    )
