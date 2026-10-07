import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.capabilities.executor import CapabilityExecutor
from enterprise_agent.capabilities.registry import CapabilityRegistry
from enterprise_agent.contracts.domain import (
    ApprovalRequest,
    ApprovalResult,
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    Classification,
    FailureCode,
    RetryPolicy,
    Risk,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import PolicyInput, Role, Subject
from enterprise_agent.runtime.admission import AdmissionLimits, RedisAdmissionController
from enterprise_agent.runtime.failure_injector import (
    FailureDirective,
    FailureInjector,
    FailurePoint,
)
from enterprise_agent.storage.approvals import decide_approval, request_approval
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.operations import OperationIntent, prepare_operation
from enterprise_agent.storage.tables import (
    AuditRow,
    CapabilityCallRow,
    OperationRow,
    RuntimeGuardRow,
    TaskBudgetRow,
    TaskRow,
)
from tests.fakes.capability import RecordingCapability
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


def descriptor(*, external: bool = False) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="procurement.admission" if external else "supplier.lookup",
        name="Supplier capability",
        version="1.0.0",
        kind="MCP",
        description="Test governed capability",
        input_schema={
            "type": "object",
            "properties": {"supplier_id": {"type": "string"}},
            "required": ["supplier_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
        tags=("supplier",),
        risk_level=Risk.EXTERNAL_SIDE_EFFECT if external else Risk.READ,
        side_effect_mode="EXTERNAL" if external else "NONE",
        idempotency_mode="NATIVE_IDEMPOTENCY" if external else "READ_ONLY",
        compensation_mode="MANUAL" if external else "NONE",
        compensation_capability=None,
        reconciliation_capability="procurement.lookup" if external else None,
        timeout_sec=5,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_sec=1, max_delay_sec=5),
        required_permissions=frozenset({"supplier.write" if external else "supplier.read"}),
        approval_policy="REQUIRED" if external else "NONE",
        provider="procurement",
        health_status="ACTIVE",
        cost_hint=0,
    )


def fast_retry_descriptor(*, external: bool = False) -> CapabilityDescriptor:
    return descriptor(external=external).model_copy(
        update={
            "timeout_sec": 0.02,
            "retry_policy": RetryPolicy(
                max_attempts=3,
                initial_delay_sec=0.001,
                max_delay_sec=0.002,
            ),
        }
    )


def internal_descriptor() -> CapabilityDescriptor:
    value = descriptor().model_dump()
    value.update(
        {
            "id": "supplier.update_internal",
            "name": "Update supplier cache",
            "risk_level": Risk.WRITE,
            "side_effect_mode": "INTERNAL",
            "idempotency_mode": "NATIVE_IDEMPOTENCY",
            "required_permissions": frozenset({"supplier.write"}),
        }
    )
    return CapabilityDescriptor.model_validate(value)


def policy(
    capability: CapabilityDescriptor,
    workspace_id,
    task_id,
    *,
    role: Role = Role.WORKSPACE_ADMIN,
) -> PolicyInput:
    return PolicyInput(
        subject=Subject(
            actor_id=uuid4(),
            actor_type="USER",
            workspace_id=workspace_id,
            role=role,
        ),
        resource_workspace_id=workspace_id,
        resource_id=task_id,
        capability=capability,
        capability_allowlist=frozenset({capability.id}),
        classification=Classification.INTERNAL,
        external_provider=False,
        policy_version="policy-1",
    )


async def running_attempt(database):
    workspace_id, task_id, step_id = await seed(database)
    attempt_id = uuid4()
    async with database.begin() as session:
        session.add(
            TaskBudgetRow(
                task_id=task_id,
                workspace_id=workspace_id,
                max_steps=10,
                max_replans=2,
                max_llm_calls=10,
                max_capability_calls=10,
                max_tokens=1000,
                max_cost=10,
                deadline=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        token = (
            await acquire_attempt(
                session,
                workspace_id,
                step_id,
                attempt_id,
                "capability-worker",
                "trace-capability",
            )
        ).fencing_token
    return workspace_id, task_id, step_id, attempt_id, token


def request_for(workspace_id, task_id, step_id, attempt_id, token, capability, operation_id=None):
    return CapabilityRequest(
        command_id=uuid4(),
        operation_id=operation_id or uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments={"supplier_id": str(uuid4())},
    )


async def test_policy_denial_is_persisted_before_provider_invocation(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = descriptor(external=True)
    handler = RecordingCapability(output={"status": "created"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    executor = CapabilityExecutor(database, registry)
    request = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)

    with pytest.raises(DomainError) as raised:
        await executor.execute(
            request,
            policy(capability, workspace_id, task_id, role=Role.MEMBER),
        )
    assert raised.value.code == FailureCode.POLICY_DENIED
    assert handler.calls == []
    async with database() as session:
        call = await session.scalar(
            select(CapabilityCallRow).where(CapabilityCallRow.command_id == request.command_id)
        )
        assert call is not None and call.state == "DENIED"
        count = await session.scalar(
            select(func.count())
            .select_from(AuditRow)
            .where(AuditRow.resource_id == call.id, AuditRow.action == "capability.policy.deny")
        )
        assert count == 1


async def test_read_only_call_validates_schema_and_replays_once(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = descriptor()
    handler = RecordingCapability(output={"status": "found"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    executor = CapabilityExecutor(database, registry)
    request = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)
    policy_input = policy(capability, workspace_id, task_id)

    first = await executor.execute(request, policy_input)
    replay = await executor.execute(request, policy_input)
    assert first == replay
    assert len(handler.calls) == 1

    changed = request.model_copy(update={"arguments": {"supplier_id": str(uuid4())}})
    with pytest.raises(DomainError) as raised:
        await executor.execute(changed, policy_input)
    assert raised.value.code == FailureCode.CONSISTENCY_VIOLATION

    async with database() as session:
        calls = (
            await session.scalars(
                select(CapabilityCallRow).where(
                    CapabilityCallRow.workspace_id == workspace_id,
                    CapabilityCallRow.command_id == request.command_id,
                )
            )
        ).all()
        assert len(calls) == 1 and calls[0].state == "SUCCEEDED"


async def test_executor_waits_for_real_redis_mcp_admission_and_clears_reason(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = descriptor()
    handler = RecordingCapability(output={"status": "found"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    redis = Redis.from_url("redis://127.0.0.1:56379/0", decode_responses=True)
    namespace = "test:executor-admission:" + str(uuid4())
    controller = RedisAdmissionController(
        redis,
        AdmissionLimits(
            global_tasks=10,
            workspace_tasks=10,
            model_provider=10,
            capability=1,
            mcp=1,
        ),
        namespace,
    )
    blocker_decision, blocker = await controller.try_acquire_call(
        workspace_id,
        "procurement",
        "other.lookup",
        is_mcp=True,
        lease_ms=5_000,
    )
    assert blocker_decision.allowed and blocker is not None
    executor = CapabilityExecutor(
        database,
        registry,
        admission_controller=controller,
        admission_lease_ms=5_000,
        admission_poll_seconds=0.02,
    )
    request = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)
    pending = asyncio.create_task(
        executor.execute(request, policy(capability, workspace_id, task_id))
    )
    try:
        await asyncio.sleep(0.1)
        assert handler.calls == []
        async with database() as session:
            task = await session.get(TaskRow, task_id)
            assert task is not None
            assert task.schedule_block_reason == "mcp_concurrency"
        await controller.release(blocker)
        result = await asyncio.wait_for(pending, timeout=2)
        assert result.outcome == "SUCCEEDED"
        assert len(handler.calls) == 1
        async with database() as session:
            task = await session.get(TaskRow, task_id)
            assert task is not None and task.schedule_block_reason is None
        keys = [key async for key in redis.scan_iter(match=namespace + ":*")]
        counts = [await redis.zcard(key) for key in keys]
        assert all(count == 0 for count in counts)
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        keys = [key async for key in redis.scan_iter(match=namespace + ":*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_read_only_timeout_retries_with_descriptor_policy(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = fast_retry_descriptor()
    calls = 0

    async def timeout_twice(request: CapabilityRequest) -> CapabilityResult:
        nonlocal calls
        calls += 1
        if calls < 3:
            await asyncio.sleep(0.05)
        return CapabilityResult(
            operation_id=request.operation_id,
            outcome="SUCCEEDED",
            output={"status": "found"},
            failure=None,
        )

    registry = CapabilityRegistry()
    registry.register(capability, timeout_twice)
    request = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)

    result = await CapabilityExecutor(database, registry).execute(
        request, policy(capability, workspace_id, task_id)
    )

    assert result.outcome == "SUCCEEDED"
    assert calls == 3


async def test_capability_budget_exhaustion_prevents_provider_invocation(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    async with database.begin() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        assert budget is not None
        budget.max_capability_calls = 1
    capability = descriptor()
    handler = RecordingCapability(output={"status": "found"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    executor = CapabilityExecutor(database, registry)
    policy_input = policy(capability, workspace_id, task_id)
    first = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)
    second = request_for(workspace_id, task_id, step_id, attempt_id, token, capability)

    assert (await executor.execute(first, policy_input)).outcome == "SUCCEEDED"
    rejected = await executor.execute(second, policy_input)
    assert rejected.outcome == "FAILED"
    assert rejected.failure is not None
    assert rejected.failure.code == FailureCode.BUDGET_EXCEEDED
    assert len(handler.calls) == 1


async def test_repeated_capability_arguments_abort_before_fourth_invocation(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = descriptor()
    handler = RecordingCapability(output={"status": "found"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    executor = CapabilityExecutor(database, registry)
    policy_input = policy(capability, workspace_id, task_id)
    arguments = {"supplier_id": str(uuid4())}
    results = []
    for _ in range(4):
        call_request = request_for(
            workspace_id,
            task_id,
            step_id,
            attempt_id,
            token,
            capability,
        ).model_copy(update={"arguments": arguments})
        results.append(await executor.execute(call_request, policy_input))

    assert [item.outcome for item in results] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "SUCCEEDED",
        "FAILED",
    ]
    assert results[-1].failure is not None
    assert results[-1].failure.code == FailureCode.CONSISTENCY_VIOLATION
    assert len(handler.calls) == 3
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        guard = await session.get(RuntimeGuardRow, task_id)
        assert task is not None and task.state == "FAILED"
        assert guard is not None and guard.state == "ABORTED"


async def test_external_effect_requires_exact_approval_and_updates_operation(database):
    workspace_id, task_id, step_id, approval_attempt_id, approval_token = (
        await running_attempt(database)
    )
    capability = descriptor(external=True)
    arguments = {"supplier_id": str(uuid4())}
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="admission",
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    async with database.begin() as session:
        prepared = await prepare_operation(
            session, intent, approval_attempt_id, approval_token
        )
        operation = await session.get(OperationRow, prepared.operation_id)
        assert operation is not None
        parameter_hash = operation.parameter_hash
    handler = RecordingCapability(output={"status": "created"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    executor = CapabilityExecutor(database, registry)
    pre_approval_request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=approval_attempt_id,
        fencing_token=approval_token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    with pytest.raises(DomainError) as raised:
        await executor.execute(
            pre_approval_request,
            policy(capability, workspace_id, task_id),
        )
    assert raised.value.code == FailureCode.APPROVAL_REJECTED
    assert handler.calls == []
    approval_id = uuid4()
    async with database.begin() as session:
        await request_approval(
            session,
            ApprovalRequest(
                approval_id=approval_id,
                workspace_id=workspace_id,
                task_id=task_id,
                operation_id=prepared.operation_id,
                capability_id=capability.id,
                capability_version=capability.version,
                parameters=intent.arguments,
                parameter_hash=parameter_hash,
                policy_version="policy-1",
                reason="create supplier admission",
                risk=Risk.EXTERNAL_SIDE_EFFECT,
                evidence_ids=(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            approval_attempt_id,
            approval_token,
        )
    async with database.begin() as session:
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
    execution_attempt_id = uuid4()
    async with database.begin() as session:
        execution_token = (
            await acquire_attempt(
                session,
                workspace_id,
                step_id,
                execution_attempt_id,
                "approved-capability-worker",
                "trace-approved-capability",
            )
        ).fencing_token

    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=execution_attempt_id,
        fencing_token=execution_token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    result = await executor.execute(
        request,
        policy(capability, workspace_id, task_id),
        approval_id=approval_id,
    )
    assert result.outcome == "SUCCEEDED"
    assert len(handler.calls) == 1
    async with database() as session:
        operation = await session.get(OperationRow, prepared.operation_id)
        call = await session.scalar(
            select(CapabilityCallRow).where(CapabilityCallRow.command_id == request.command_id)
        )
        assert operation is not None and operation.state == "SUCCEEDED"
        assert call is not None and call.state == "SUCCEEDED"


async def test_internal_side_effect_uses_persisted_allow_decision(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = internal_descriptor()
    arguments = {"supplier_id": str(uuid4())}
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="internal-cache-update",
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    async with database.begin() as session:
        prepared = await prepare_operation(session, intent, attempt_id, token)
    handler = RecordingCapability(output={"status": "updated"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    result = await CapabilityExecutor(database, registry).execute(
        request,
        policy(capability, workspace_id, task_id),
    )
    assert result.outcome == "SUCCEEDED"
    assert len(handler.calls) == 1
    async with database() as session:
        operation = await session.get(OperationRow, prepared.operation_id)
        call = await session.scalar(
            select(CapabilityCallRow).where(CapabilityCallRow.command_id == request.command_id)
        )
        assert operation is not None and operation.state == "SUCCEEDED"
        assert call is not None and call.policy_decision == "ALLOW"


async def test_side_effect_timeout_is_unknown_and_never_retried(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = fast_retry_descriptor().model_copy(
        update={
            "id": "supplier.update_internal",
            "name": "Update supplier cache",
            "risk_level": Risk.WRITE,
            "side_effect_mode": "INTERNAL",
            "idempotency_mode": "NATIVE_IDEMPOTENCY",
            "required_permissions": frozenset({"supplier.write"}),
        }
    )
    arguments = {"supplier_id": str(uuid4())}
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="timeout-internal-update",
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    async with database.begin() as session:
        prepared = await prepare_operation(session, intent, attempt_id, token)
    calls = 0

    async def lose_response(request: CapabilityRequest) -> CapabilityResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        raise AssertionError("timeout context should cancel this invocation")

    registry = CapabilityRegistry()
    registry.register(capability, lose_response)
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    executor = CapabilityExecutor(database, registry)
    policy_input = policy(capability, workspace_id, task_id)

    result = await executor.execute(request, policy_input)
    replay = await executor.execute(request, policy_input)

    assert result == replay
    assert result.outcome == "UNKNOWN"
    assert result.failure is not None
    assert result.failure.code == FailureCode.CAPABILITY_UNKNOWN_OUTCOME
    assert calls == 1
    async with database() as session:
        operation = await session.get(OperationRow, prepared.operation_id)
        assert operation is not None and operation.state == "UNKNOWN"


async def test_injected_response_loss_commits_unknown_after_real_handler_success(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = internal_descriptor()
    arguments = {"supplier_id": str(uuid4())}
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="injected-response-loss",
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    async with database.begin() as session:
        prepared = await prepare_operation(session, intent, attempt_id, token)
    handler = RecordingCapability(output={"status": "committed-remotely"})
    registry = CapabilityRegistry()
    registry.register(capability, handler)
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    injector = FailureInjector(
        (
            FailureDirective(
                point=FailurePoint.CAPABILITY_RESPONSE_LOST,
                occurrences=(1,),
            ),
        )
    )
    executor = CapabilityExecutor(database, registry, injector)

    result = await executor.execute(request, policy(capability, workspace_id, task_id))
    replay = await executor.execute(request, policy(capability, workspace_id, task_id))

    assert result == replay
    assert result.outcome == "UNKNOWN"
    assert result.failure is not None
    assert result.failure.code is FailureCode.CAPABILITY_UNKNOWN_OUTCOME
    assert len(handler.calls) == 1
    async with database() as session:
        operation = await session.get(OperationRow, prepared.operation_id)
        call = await session.scalar(
            select(CapabilityCallRow).where(CapabilityCallRow.command_id == request.command_id)
        )
        assert operation is not None and operation.state == "UNKNOWN"
        assert call is not None and call.state == "UNKNOWN"


async def test_native_idempotent_dispatch_resumes_after_worker_cancellation(database):
    workspace_id, task_id, step_id, attempt_id, token = await running_attempt(database)
    capability = internal_descriptor()
    arguments = {"supplier_id": str(uuid4())}
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="cancelled-after-native-dispatch",
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    async with database.begin() as session:
        prepared = await prepare_operation(session, intent, attempt_id, token)

    calls = 0

    async def cancel_once(request: CapabilityRequest) -> CapabilityResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return CapabilityResult(
            operation_id=request.operation_id,
            outcome="SUCCEEDED",
            output={"status": "updated"},
            failure=None,
        )

    registry = CapabilityRegistry()
    registry.register(capability, cancel_once)
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=prepared.operation_id,
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        attempt_id=attempt_id,
        fencing_token=token,
        capability_id=capability.id,
        capability_version=capability.version,
        arguments=arguments,
    )
    executor = CapabilityExecutor(database, registry)
    policy_input = policy(capability, workspace_id, task_id)

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(request, policy_input)
    async with database() as session:
        dispatched_call = await session.scalar(
            select(CapabilityCallRow).where(
                CapabilityCallRow.command_id == request.command_id
            )
        )
        dispatched_operation = await session.get(OperationRow, prepared.operation_id)
        budget = await session.get(TaskBudgetRow, task_id)
        assert dispatched_call is not None and dispatched_call.state == "DISPATCHED"
        assert dispatched_call.dispatch_id is not None
        original_dispatch_id = dispatched_call.dispatch_id
        assert dispatched_operation is not None and dispatched_operation.state == "DISPATCHED"
        assert budget is not None and budget.used_capability_calls == 1

    result = await executor.execute(request, policy_input)

    assert result.outcome == "SUCCEEDED"
    assert calls == 2
    async with database() as session:
        recovered_call = await session.scalar(
            select(CapabilityCallRow).where(
                CapabilityCallRow.command_id == request.command_id
            )
        )
        recovered_operation = await session.get(OperationRow, prepared.operation_id)
        budget = await session.get(TaskBudgetRow, task_id)
        assert recovered_call is not None and recovered_call.state == "SUCCEEDED"
        assert recovered_call.dispatch_id == original_dispatch_id
        assert recovered_operation is not None and recovered_operation.state == "SUCCEEDED"
        assert recovered_operation.dispatch_id == original_dispatch_id
        assert budget is not None and budget.used_capability_calls == 1
