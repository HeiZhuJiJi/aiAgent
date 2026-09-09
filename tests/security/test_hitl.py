import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import ApprovalRequest, ApprovalResult, FailureCode, Risk
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.approvals import decide_approval, request_approval
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.operations import OperationIntent, mark_dispatched, prepare_operation
from enterprise_agent.storage.tables import ApprovalRow, OperationRow, StepRow, TaskRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def setup_operation(database):
    workspace_id, task_id, step_id = await seed(database)
    attempt_id = uuid4()
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="protected-external-effect",
        capability_id="procurement.create_supplier_admission_request",
        capability_version="1.0.0",
        arguments={"supplier_id": str(uuid4())},
    )
    async with database.begin() as session:
        token = (
            await acquire_attempt(
                session, workspace_id, step_id, attempt_id, "approval-worker", "trace-hitl"
            )
        ).fencing_token
        prepared = await prepare_operation(session, intent, attempt_id, token)
        operation = await session.get(OperationRow, prepared.operation_id)
        parameter_hash = operation.parameter_hash
    return workspace_id, task_id, step_id, attempt_id, token, intent, prepared, parameter_hash


def approval_request(workspace_id, task_id, intent, prepared, parameter_hash):
    return ApprovalRequest(
        approval_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        operation_id=prepared.operation_id,
        capability_id=intent.capability_id,
        capability_version=intent.capability_version,
        parameters=intent.arguments,
        parameter_hash=parameter_hash,
        policy_version="policy-1",
        reason="external side effect requires a human",
        risk=Risk.EXTERNAL_SIDE_EFFECT,
        evidence_ids=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def test_external_effect_cannot_dispatch_without_matching_approval(database):
    workspace_id, _, _, attempt_id, token, _, prepared, _ = await setup_operation(database)
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await mark_dispatched(
                session,
                workspace_id,
                prepared.operation_id,
                attempt_id,
                token,
                prepared.version,
                uuid4(),
                uuid4(),
            )
    assert raised.value.code == FailureCode.APPROVAL_REJECTED


async def test_approved_operation_resumes_with_new_attempt_and_is_audited(database):
    (
        workspace_id,
        task_id,
        step_id,
        attempt_id,
        token,
        intent,
        prepared,
        digest,
    ) = await setup_operation(database)
    request = approval_request(workspace_id, task_id, intent, prepared, digest)
    tampered = request.model_copy(
        update={
            "approval_id": uuid4(),
            "parameters": {**request.parameters, "supplier_id": str(uuid4())},
        }
    )
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await request_approval(session, tampered, attempt_id, token)
    assert raised.value.code == FailureCode.VALIDATION_FAILED
    async with database.begin() as session:
        pending = await request_approval(session, request, attempt_id, token)
        assert pending.state == "PENDING"
    async with database.begin() as session:
        replay = await request_approval(session, request, attempt_id, token)
        assert replay.id == request.approval_id
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        step = await session.get(StepRow, step_id)
        assert task.state == "WAITING_HITL"
        assert step.state == "WAITING_HITL"
    actor_id = uuid4()
    async with database.begin() as session:
        approved = await decide_approval(
            session,
            workspace_id,
            ApprovalResult(
                approval_id=request.approval_id,
                decision="APPROVED",
                actor_id=actor_id,
                decided_at=datetime.now(UTC),
            ),
            1,
        )
        assert approved.state == "APPROVED" and approved.actor_id == actor_id
    execution_attempt = uuid4()
    async with database.begin() as session:
        execution_token = (
            await acquire_attempt(
                session, workspace_id, step_id, execution_attempt, "executor", "trace-execute"
            )
        ).fencing_token
        dispatched = await mark_dispatched(
            session,
            workspace_id,
            prepared.operation_id,
            execution_attempt,
            execution_token,
            prepared.version,
            uuid4(),
            request.approval_id,
        )
        assert dispatched.action == "RECONCILE"
    with pytest.raises(DomainError, match="state/version mismatch"):
        async with database.begin() as session:
            await decide_approval(
                session,
                workspace_id,
                ApprovalResult(
                    approval_id=request.approval_id,
                    decision="REJECTED",
                    actor_id=uuid4(),
                    decided_at=datetime.now(UTC),
                ),
                1,
            )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                update(ApprovalRow)
                .where(ApprovalRow.id == request.approval_id)
                .values(request_hash="f" * 64)
            )
    async with database() as session:
        stored = await session.scalar(
            select(ApprovalRow).where(ApprovalRow.id == request.approval_id)
        )
        assert stored.state == "APPROVED"


async def test_expired_request_never_moves_task_into_hitl(database):
    (
        workspace_id,
        task_id,
        step_id,
        attempt_id,
        token,
        intent,
        prepared,
        digest,
    ) = await setup_operation(database)
    request = approval_request(workspace_id, task_id, intent, prepared, digest).model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await request_approval(session, request, attempt_id, token)
    assert raised.value.code == FailureCode.APPROVAL_REJECTED
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        step = await session.get(StepRow, step_id)
        assert task.state == "RUNNING"
        assert step.state == "RUNNING"
        assert await session.get(ApprovalRow, request.approval_id) is None
