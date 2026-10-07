import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities.registry import CapabilityRegistry
from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    Failure,
    FailureCode,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import Decision, PolicyInput, evaluate
from enterprise_agent.observability.metrics import (
    CAPABILITY_CALL_TOTAL,
    CAPABILITY_FAILURE_TOTAL,
    POLICY_DENIED_TOTAL,
)
from enterprise_agent.runtime.admission import (
    AdmissionLease,
    RedisAdmissionController,
    record_schedule_decision,
)
from enterprise_agent.runtime.budgets import BudgetDelta, BudgetKind, reserve_budget
from enterprise_agent.runtime.failure_injector import FailureInjector, FailurePoint
from enterprise_agent.runtime.loop_guard import capability_fingerprint, check_capability_loop
from enterprise_agent.runtime.retry import classify_failure, permits_inline_retry, retry_delay
from enterprise_agent.storage.operations import (
    OperationIntent,
    mark_dispatched,
    mark_dispatched_by_policy,
    operation_digest,
    record_outcome,
    require_owner,
)
from enterprise_agent.storage.runtime_locks import lock_task_runtime
from enterprise_agent.storage.tables import (
    AuditRow,
    CapabilityCallRow,
    OperationRow,
    StepRow,
)


def _request_digest(request: CapabilityRequest) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], request.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _validate_schema(
    schema: dict[str, JsonValue],
    value: object,
    label: str,
    failure_code: FailureCode = FailureCode.VALIDATION_FAILED,
) -> None:
    try:
        validator = Draft202012Validator(cast(dict[str, Any], schema))
        validator.check_schema(cast(dict[str, Any], schema))
        validator.validate(value)
    except (SchemaError, ValidationError) as exc:
        raise DomainError(failure_code, f"{label} schema validation failed") from exc


def _failure(
    request: CapabilityRequest,
    code: FailureCode,
    detail: str,
    trace_id: str,
    *,
    unknown: bool = False,
) -> CapabilityResult:
    return CapabilityResult(
        operation_id=request.operation_id,
        outcome="UNKNOWN" if unknown else "FAILED",
        output=None,
        failure=Failure(code=code, safe_detail=detail, trace_id=trace_id),
    )


class CapabilityExecutor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: CapabilityRegistry,
        failure_injector: FailureInjector | None = None,
        *,
        admission_controller: RedisAdmissionController | None = None,
        admission_lease_ms: int = 360_000,
        admission_poll_seconds: float = 0.25,
    ) -> None:
        if admission_lease_ms < 100 or admission_poll_seconds <= 0:
            raise ValueError("capability admission timing must be positive")
        self._sessions = sessions
        self._registry = registry
        self._failure_injector = failure_injector or FailureInjector()
        self._admission_controller = admission_controller
        self._admission_lease_ms = admission_lease_ms
        self._admission_poll_seconds = admission_poll_seconds

    async def execute(
        self,
        request: CapabilityRequest,
        policy_input: PolicyInput,
        *,
        approval_id: UUID | None = None,
    ) -> CapabilityResult:
        entry = self._registry.resolve(request.capability_id, request.capability_version)
        descriptor = entry.descriptor
        self._validate_identity(request, descriptor, policy_input)
        _validate_schema(descriptor.input_schema, request.arguments, "input")
        policy = evaluate(policy_input)
        needs_dispatch = policy.decision != Decision.DENY and not (
            policy.decision == Decision.REQUIRE_APPROVAL and approval_id is None
        )
        lease: AdmissionLease | None = None
        if self._admission_controller is not None and needs_dispatch:
            lease = await self._wait_for_admission(request, descriptor)
        try:
            return await self._execute_admitted(
                request,
                policy_input,
                approval_id=approval_id,
            )
        finally:
            if lease is not None:
                assert self._admission_controller is not None
                await self._admission_controller.release(lease)

    async def _wait_for_admission(
        self,
        request: CapabilityRequest,
        descriptor: CapabilityDescriptor,
    ) -> AdmissionLease:
        assert self._admission_controller is not None
        retry_window_ms = int(
            (
                descriptor.timeout_sec * descriptor.retry_policy.max_attempts
                + descriptor.retry_policy.max_delay_sec
                * (descriptor.retry_policy.max_attempts - 1)
                + 5
            )
            * 1000
        )
        lease_ms = max(self._admission_lease_ms, retry_window_ms)
        while True:
            decision, lease = await self._admission_controller.try_acquire_call(
                request.workspace_id,
                descriptor.provider,
                descriptor.id,
                is_model=descriptor.kind == "MODEL",
                is_mcp=descriptor.kind == "MCP",
                lease_ms=lease_ms,
            )
            async with self._sessions.begin() as session:
                await record_schedule_decision(
                    session,
                    request.workspace_id,
                    request.task_id,
                    decision,
                )
            if lease is not None:
                return lease
            await asyncio.sleep(self._admission_poll_seconds)

    async def _execute_admitted(
        self,
        request: CapabilityRequest,
        policy_input: PolicyInput,
        *,
        approval_id: UUID | None = None,
    ) -> CapabilityResult:
        entry = self._registry.resolve(request.capability_id, request.capability_version)
        descriptor = entry.descriptor
        self._validate_identity(request, descriptor, policy_input)
        _validate_schema(descriptor.input_schema, request.arguments, "input")
        policy = evaluate(policy_input)
        payload, digest = _request_digest(request)
        trace_id = "capability:" + str(request.command_id)
        rejection: DomainError | None = None
        preflight_result: CapabilityResult | None = None
        dispatch_id: UUID | None = None
        native_replay = False

        async with self._sessions.begin() as session:
            await lock_task_runtime(session, request.workspace_id, request.task_id)
            existing = await session.scalar(
                select(CapabilityCallRow)
                .where(
                    CapabilityCallRow.workspace_id == request.workspace_id,
                    CapabilityCallRow.command_id == request.command_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "capability command replay changed request",
                    )
                if (
                    existing.policy_version != policy.policy_version
                    or existing.policy_decision != policy.decision.value
                ):
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "capability command replay changed policy decision",
                    )
                await require_owner(
                    session,
                    request.workspace_id,
                    request.step_id,
                    request.attempt_id,
                    request.fencing_token,
                )
                if existing.state == "DENIED":
                    raise DomainError(FailureCode.POLICY_DENIED, existing.policy_reason)
                if existing.result_payload is not None:
                    return CapabilityResult.model_validate(existing.result_payload)
                if existing.state == "DISPATCHED":
                    if descriptor.idempotency_mode != "NATIVE_IDEMPOTENCY":
                        raise DomainError(
                            FailureCode.CAPABILITY_UNKNOWN_OUTCOME,
                            "in-flight capability call requires reconciliation",
                        )
                    if existing.dispatch_id is None:
                        raise DomainError(
                            FailureCode.CONSISTENCY_VIOLATION,
                            "native-idempotent dispatch identity is missing",
                        )
                    native_replay = True
                    dispatch_id = existing.dispatch_id
                call = existing
            else:
                await self._validate_owner_and_resource(session, request)
                call = CapabilityCallRow(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"enterprise-capability-call:{request.workspace_id}:{request.command_id}",
                    ),
                    workspace_id=request.workspace_id,
                    task_id=request.task_id,
                    step_id=request.step_id,
                    attempt_id=request.attempt_id,
                    operation_id=request.operation_id,
                    command_id=request.command_id,
                    request_hash=digest,
                    request_payload=payload,
                    capability_id=descriptor.id,
                    capability_version=descriptor.version,
                    policy_decision=policy.decision.value,
                    policy_reason=policy.reason,
                    policy_version=policy.policy_version,
                    state="PENDING",
                    trace_id=trace_id,
                )
                session.add(call)
                session.add(
                    AuditRow(
                        workspace_id=request.workspace_id,
                        actor_type=policy_input.subject.actor_type,
                        actor_id=policy_input.subject.actor_id,
                        task_id=request.task_id,
                        step_id=request.step_id,
                        resource_type="CAPABILITY_CALL",
                        resource_id=call.id,
                        capability_id=descriptor.id,
                        policy_decision=policy.decision.value,
                        action="capability.policy." + policy.decision.value.lower(),
                        result={"state": "PENDING", "reason": policy.reason},
                        trace_id=trace_id,
                    )
                )
                await session.flush()

            if policy.decision == Decision.DENY:
                POLICY_DENIED_TOTAL.labels("capability").inc()
                call.state = "DENIED"
                call.version += 1
                rejection = DomainError(FailureCode.POLICY_DENIED, policy.reason)
            elif policy.decision == Decision.REQUIRE_APPROVAL and approval_id is None:
                call.state = "WAITING_APPROVAL"
                call.version += 1
                rejection = DomainError(
                    FailureCode.APPROVAL_REJECTED,
                    "operation-bound approval required before capability dispatch",
                )
            elif native_replay:
                pass
            else:
                loop = await check_capability_loop(
                    session,
                    request.workspace_id,
                    request.task_id,
                    request.step_id,
                    request.attempt_id,
                    policy_input.subject.actor_id,
                    trace_id,
                    capability_fingerprint(
                        descriptor.id,
                        descriptor.version,
                        request.arguments,
                    ),
                )
                if not loop.allowed:
                    preflight_result = _failure(
                        request,
                        FailureCode.CONSISTENCY_VIOLATION,
                        "runtime loop detected: " + (loop.reason or "unknown"),
                        trace_id,
                    )
                    call.state = "FAILED"
                    call.result_payload = cast(
                        dict[str, object],
                        preflight_result.model_dump(mode="json"),
                    )
                    call.completed_at = datetime.now(UTC)
                    call.version += 1
                else:
                    budget = await reserve_budget(
                        session,
                        request.workspace_id,
                        request.task_id,
                        policy_input.subject.actor_id,
                        trace_id,
                        BudgetDelta(
                            kind=BudgetKind.CAPABILITY_CALL,
                            cost=descriptor.cost_hint,
                        ),
                    )
                    if not budget.allowed:
                        preflight_result = _failure(
                            request,
                            FailureCode.BUDGET_EXCEEDED,
                            "task budget exceeded: " + (budget.reason or "unknown"),
                            trace_id,
                        )
                        call.state = "FAILED"
                        call.result_payload = cast(
                            dict[str, object],
                            preflight_result.model_dump(mode="json"),
                        )
                        call.completed_at = datetime.now(UTC)
                        call.version += 1
                    elif descriptor.side_effect_mode == "NONE":
                        call.state = "DISPATCHED"
                        call.version += 1
                    else:
                        operation = await self._validate_operation(
                            session, request, descriptor
                        )
                        dispatch_id = uuid5(
                            NAMESPACE_URL,
                            "enterprise-capability-dispatch:"
                            f"{request.workspace_id}:{request.command_id}",
                        )
                        if policy.decision == Decision.REQUIRE_APPROVAL:
                            assert approval_id is not None
                            await mark_dispatched(
                                session,
                                request.workspace_id,
                                request.operation_id,
                                request.attempt_id,
                                request.fencing_token,
                                operation.version,
                                dispatch_id,
                                approval_id,
                            )
                        else:
                            await mark_dispatched_by_policy(
                                session,
                                request.workspace_id,
                                request.operation_id,
                                request.attempt_id,
                                request.fencing_token,
                                operation.version,
                                dispatch_id,
                                call.id,
                            )
                        call.state = "DISPATCHED"
                        call.dispatch_id = dispatch_id
                        call.version += 1

        if rejection is not None:
            raise rejection
        if preflight_result is not None:
            return preflight_result

        has_side_effect = descriptor.side_effect_mode != "NONE"
        for provider_attempt in range(1, descriptor.retry_policy.max_attempts + 1):
            try:
                CAPABILITY_CALL_TOTAL.labels(descriptor.id).inc()
                async with asyncio.timeout(descriptor.timeout_sec):
                    if self._failure_injector.should_fail(FailurePoint.CONTEXT_OVERFLOW):
                        raise DomainError(FailureCode.CONTEXT_OVERFLOW, "context window exceeded")
                    result = await entry.handler(request)
                    if has_side_effect and self._failure_injector.should_fail(
                        FailurePoint.CAPABILITY_RESPONSE_LOST
                    ):
                        result = _failure(
                            request,
                            FailureCode.CAPABILITY_UNKNOWN_OUTCOME,
                            "capability response was lost after dispatch",
                            trace_id,
                            unknown=True,
                        )
                self._validate_result(request, descriptor, result)
            except TimeoutError:
                result = _failure(
                    request,
                    FailureCode.CAPABILITY_UNKNOWN_OUTCOME
                    if has_side_effect
                    else FailureCode.CAPABILITY_TIMEOUT,
                    "capability timed out",
                    trace_id,
                    unknown=has_side_effect,
                )
            except DomainError as exc:
                result = _failure(
                    request,
                    exc.code,
                    exc.detail,
                    trace_id,
                    unknown=has_side_effect,
                )
            except Exception:  # noqa: BLE001 - provider adapters are an external trust boundary
                result = _failure(
                    request,
                    FailureCode.CAPABILITY_UNAVAILABLE,
                    "capability invocation failed",
                    trace_id,
                    unknown=has_side_effect,
                )

            if result.outcome == "SUCCEEDED" or has_side_effect:
                break
            assert result.failure is not None
            action = classify_failure(result.failure.code)
            if (
                not permits_inline_retry(action)
                or provider_attempt == descriptor.retry_policy.max_attempts
            ):
                break
            await asyncio.sleep(retry_delay(descriptor.retry_policy, provider_attempt))

        async with self._sessions.begin() as session:
            await lock_task_runtime(session, request.workspace_id, request.task_id)
            await self._validate_owner_and_resource(session, request)
            committed_call = await session.scalar(
                select(CapabilityCallRow)
                .where(
                    CapabilityCallRow.workspace_id == request.workspace_id,
                    CapabilityCallRow.command_id == request.command_id,
                )
                .with_for_update()
            )
            if committed_call is None or committed_call.state != "DISPATCHED":
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "capability call changed before result commit",
                )
            if descriptor.side_effect_mode != "NONE":
                if dispatch_id is None:
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "side effect dispatch identity missing",
                    )
                await record_outcome(
                    session,
                    request.workspace_id,
                    request.operation_id,
                    request.attempt_id,
                    request.fencing_token,
                    dispatch_id,
                    result.outcome,
                    (
                        result.output
                        if isinstance(result.output, dict)
                        else {"value": result.output}
                    )
                    if result.outcome == "SUCCEEDED"
                    else None,
                )
            committed_call.state = result.outcome
            committed_call.result_payload = cast(
                dict[str, object], result.model_dump(mode="json")
            )
            committed_call.completed_at = datetime.now(UTC)
            committed_call.version += 1
            session.add(
                AuditRow(
                    workspace_id=request.workspace_id,
                    actor_type="AGENT",
                    actor_id=request.attempt_id,
                    task_id=request.task_id,
                    step_id=request.step_id,
                    resource_type="CAPABILITY_CALL",
                    resource_id=committed_call.id,
                    capability_id=descriptor.id,
                    policy_decision=committed_call.policy_decision,
                    action="capability." + result.outcome.lower(),
                    result={"state": result.outcome},
                    trace_id=trace_id,
                )
            )
        if result.outcome != "SUCCEEDED":
            CAPABILITY_FAILURE_TOTAL.labels(descriptor.id, result.outcome).inc()
        return result

    @staticmethod
    def _validate_result(
        request: CapabilityRequest,
        descriptor: CapabilityDescriptor,
        result: CapabilityResult,
    ) -> None:
        if result.operation_id != request.operation_id:
            raise DomainError(
                FailureCode.CAPABILITY_BAD_OUTPUT,
                "capability returned a different operation id",
            )
        if result.outcome == "SUCCEEDED":
            _validate_schema(
                descriptor.output_schema,
                result.output,
                "output",
                FailureCode.CAPABILITY_BAD_OUTPUT,
            )
            if result.failure is not None:
                raise DomainError(
                    FailureCode.CAPABILITY_BAD_OUTPUT,
                    "successful capability result cannot contain failure",
                )
        elif result.failure is None:
            raise DomainError(
                FailureCode.CAPABILITY_BAD_OUTPUT,
                "non-success capability result requires failure",
            )

    @staticmethod
    def _validate_identity(
        request: CapabilityRequest,
        descriptor: CapabilityDescriptor,
        policy_input: PolicyInput,
    ) -> None:
        if (
            request.capability_id != descriptor.id
            or request.capability_version != descriptor.version
            or policy_input.capability != descriptor
            or policy_input.resource_workspace_id != request.workspace_id
            or policy_input.resource_id != request.task_id
        ):
            raise DomainError(FailureCode.POLICY_DENIED, "capability identity binding mismatch")

    @staticmethod
    async def _validate_owner_and_resource(
        session: AsyncSession, request: CapabilityRequest
    ) -> None:
        await require_owner(
            session,
            request.workspace_id,
            request.step_id,
            request.attempt_id,
            request.fencing_token,
        )
        task_id = await session.scalar(
            select(StepRow.task_id).where(
                StepRow.workspace_id == request.workspace_id,
                StepRow.id == request.step_id,
            )
        )
        if task_id != request.task_id:
            raise DomainError(FailureCode.POLICY_DENIED, "capability task binding mismatch")

    @staticmethod
    async def _validate_operation(
        session: AsyncSession,
        request: CapabilityRequest,
        descriptor: CapabilityDescriptor,
    ) -> OperationRow:
        operation = await session.scalar(
            select(OperationRow)
            .where(
                OperationRow.workspace_id == request.workspace_id,
                OperationRow.id == request.operation_id,
            )
            .with_for_update()
        )
        if (
            operation is None
            or operation.step_id != request.step_id
            or operation.capability_id != descriptor.id
            or operation.capability_version != descriptor.version
        ):
            raise DomainError(FailureCode.POLICY_DENIED, "side effect operation binding mismatch")
        intent = OperationIntent(
            workspace_id=request.workspace_id,
            step_id=request.step_id,
            logical_key=operation.logical_key,
            capability_id=descriptor.id,
            capability_version=descriptor.version,
            arguments=request.arguments,
        )
        if operation.parameter_hash != operation_digest(intent):
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "operation arguments changed")
        return operation
