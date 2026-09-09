import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import ApprovalRequest, ApprovalResult, Risk
from enterprise_agent.domain.states import DomainError
from enterprise_agent.procurement.contracts import AdmissionInput
from enterprise_agent.procurement.service import (
    AdmissionRow,
    SupplierRow,
    create_admission,
    lookup_admission,
)
from enterprise_agent.storage.approvals import decide_approval, request_approval
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.operations import (
    OperationAction,
    OperationIntent,
    mark_dispatched,
    prepare_operation,
    reconcile_operation,
    record_outcome,
)
from enterprise_agent.storage.tables import OperationRow, StepRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def databases():
    platform_engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    procurement_engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    yield (
        async_sessionmaker(platform_engine, expire_on_commit=False),
        async_sessionmaker(procurement_engine, expire_on_commit=False),
    )
    await platform_engine.dispose()
    await procurement_engine.dispose()


async def approve_operation(
    platform,
    workspace_id,
    task_id,
    intent,
    prepared,
    approval_attempt_id,
    approval_token,
    execution_attempt_id,
):
    approval_id = uuid4()
    async with platform() as session:
        operation = await session.get(OperationRow, prepared.operation_id)
        parameter_hash = operation.parameter_hash
    request = ApprovalRequest(
        approval_id=approval_id,
        workspace_id=workspace_id,
        task_id=task_id,
        operation_id=prepared.operation_id,
        capability_id=intent.capability_id,
        capability_version=intent.capability_version,
        parameters=intent.arguments,
        parameter_hash=parameter_hash,
        policy_version="policy-1",
        reason="external supplier admission",
        risk=Risk.EXTERNAL_SIDE_EFFECT,
        evidence_ids=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with platform.begin() as session:
        await request_approval(session, request, approval_attempt_id, approval_token)
    async with platform.begin() as session:
        await decide_approval(
            session,
            workspace_id,
            ApprovalResult(
                approval_id=approval_id,
                decision="APPROVED",
                actor_id=uuid4(),
                decided_at=datetime.now(UTC),
            ),
            expected_version=1,
        )
    async with platform.begin() as session:
        execution_token = (
            await acquire_attempt(
                session,
                workspace_id,
                intent.step_id,
                execution_attempt_id,
                "worker-approved",
                "trace-approved",
            )
        ).fencing_token
    return approval_id, execution_token


async def test_lost_response_reconciles_without_duplicate_side_effect(databases):
    platform, procurement = databases
    workspace_id, task_id, step_id = await seed(platform)
    approval_attempt_id = uuid4()
    attempt_id = uuid4()
    supplier_id = uuid4()
    requested_by = uuid4()
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="supplier-admission",
        capability_id="procurement.create_supplier_admission_request",
        capability_version="1.0.0",
        arguments={"supplier_id": str(supplier_id), "report_sha256": "a" * 64},
    )
    async with platform.begin() as session:
        token = (
            await acquire_attempt(
                session, workspace_id, step_id, approval_attempt_id, "worker-a", "trace-a"
            )
        ).fencing_token
        prepared = await prepare_operation(session, intent, approval_attempt_id, token)
    approval_id, token = await approve_operation(
        platform,
        workspace_id,
        task_id,
        intent,
        prepared,
        approval_attempt_id,
        token,
        attempt_id,
    )
    async with platform.begin() as session:
        dispatch_id = uuid4()
        await mark_dispatched(
            session,
            workspace_id,
            prepared.operation_id,
            attempt_id,
            token,
            prepared.version,
            dispatch_id,
            approval_id,
        )
    async with procurement.begin() as session:
        session.add(
            SupplierRow(
                id=supplier_id,
                workspace_id=workspace_id,
                profile={},
                purchase_history=[],
                risk_flags=[],
                contracts=[],
            )
        )
    request = AdmissionInput(
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        supplier_id=supplier_id,
        report_sha256="a" * 64,
        requested_by=requested_by,
    )
    async with procurement.begin() as session:
        created = await create_admission(session, request)
    # The remote commit succeeded; deliberately discard its response.
    async with platform.begin() as session:
        unknown = await record_outcome(
            session,
            workspace_id,
            prepared.operation_id,
            attempt_id,
            token,
            dispatch_id,
            "UNKNOWN",
            None,
        )
        assert unknown.action is OperationAction.RECONCILE
        retry = await prepare_operation(session, intent, attempt_id, token)
        assert retry.action is OperationAction.RECONCILE
        assert retry.version == unknown.version
    async with procurement() as session:
        found = await lookup_admission(session, workspace_id, prepared.operation_id)
        assert found is not None
    async with platform.begin() as session:
        reconciled = await reconcile_operation(
            session,
            workspace_id,
            prepared.operation_id,
            attempt_id,
            token,
            retry.version,
            "FOUND",
            found.model_dump(mode="json"),
        )
        assert reconciled.action is OperationAction.RETURN_RESULT
    async with procurement.begin() as session:
        replay = await create_admission(session, request)
        assert replay.admission_id == created.admission_id
    async with procurement() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AdmissionRow)
            .where(
                AdmissionRow.workspace_id == workspace_id,
                AdmissionRow.operation_id == prepared.operation_id,
            )
        )
        assert count == 1


async def test_zombie_cannot_record_side_effect_outcome(databases):
    platform, _ = databases
    workspace_id, task_id, step_id = await seed(platform)
    approval_attempt, old_attempt, new_attempt = uuid4(), uuid4(), uuid4()
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="admission",
        capability_id="procurement.admission",
        capability_version="1.0.0",
        arguments={"supplier_id": str(uuid4())},
    )
    async with platform.begin() as session:
        old_token = (
            await acquire_attempt(
                session, workspace_id, step_id, approval_attempt, "worker-a", "trace-a"
            )
        ).fencing_token
        prepared = await prepare_operation(session, intent, approval_attempt, old_token)
    approval_id, old_token = await approve_operation(
        platform,
        workspace_id,
        task_id,
        intent,
        prepared,
        approval_attempt,
        old_token,
        old_attempt,
    )
    async with platform.begin() as session:
        dispatch_id = uuid4()
        dispatched = await mark_dispatched(
            session,
            workspace_id,
            prepared.operation_id,
            old_attempt,
            old_token,
            prepared.version,
            dispatch_id,
            approval_id,
        )
    async with platform.begin() as session:
        await session.execute(
            update(StepRow).where(StepRow.id == step_id).values(lease_expires_at=func.now())
        )
    async with platform.begin() as session:
        new_token = (
            await acquire_attempt(
                session, workspace_id, step_id, new_attempt, "worker-b", "trace-b"
            )
        ).fencing_token
    with pytest.raises(DomainError, match="operation owner rejected"):
        async with platform.begin() as session:
            await record_outcome(
                session,
                workspace_id,
                prepared.operation_id,
                old_attempt,
                old_token,
                dispatch_id,
                "SUCCEEDED",
                {"admission_id": str(uuid4())},
            )
    async with platform.begin() as session:
        retry = await prepare_operation(session, intent, new_attempt, new_token)
        assert retry.action is OperationAction.RECONCILE
        reconciled = await reconcile_operation(
            session,
            workspace_id,
            prepared.operation_id,
            new_attempt,
            new_token,
            dispatched.version,
            "FOUND",
            {"admission_id": str(uuid4()), "recovered_at": datetime.now(UTC).isoformat()},
        )
        assert reconciled.action is OperationAction.RETURN_RESULT
    async with platform() as session:
        row = await session.scalar(
            select(OperationRow).where(
                OperationRow.workspace_id == workspace_id,
                OperationRow.id == prepared.operation_id,
            )
        )
        assert row is not None and row.state == "SUCCEEDED"


async def test_stable_operation_rejects_changed_parameters(databases):
    platform, _ = databases
    workspace_id, _, step_id = await seed(platform)
    attempt_id = uuid4()
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="same-logical-operation",
        capability_id="procurement.admission",
        capability_version="1.0.0",
        arguments={"supplier_id": "original"},
    )
    async with platform.begin() as session:
        token = (
            await acquire_attempt(session, workspace_id, step_id, attempt_id, "worker", "trace")
        ).fencing_token
        await prepare_operation(session, intent, attempt_id, token)
        with pytest.raises(DomainError, match="parameters changed"):
            await prepare_operation(
                session,
                intent.model_copy(update={"arguments": {"supplier_id": "changed"}}),
                attempt_id,
                token,
            )
