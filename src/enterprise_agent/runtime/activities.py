import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from minio import Minio
from pydantic import JsonValue, SecretStr
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.exceptions import ApplicationError

from enterprise_agent.capabilities.builtins import (
    load_artifact_store,
    register_builtin_capabilities,
)
from enterprise_agent.capabilities.executor import CapabilityExecutor
from enterprise_agent.contracts.domain import (
    ApprovalRequest,
    ArtifactManifest,
    CapabilityDescriptor,
    CapabilityRequest,
    Classification,
    Evidence,
    Failure,
    FailureCode,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    StepState,
    TaskGoal,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.governance.policy import Decision, PolicyInput, Role, Subject, evaluate
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.mcp.adapter import load_mcp_registry
from enterprise_agent.models.gateway import ModelConfig, ModelGateway, ModelUsage
from enterprise_agent.models.settings import load_embedding_settings
from enterprise_agent.runtime.admission import (
    AdmissionLease,
    load_admission_controller,
    record_schedule_decision,
)
from enterprise_agent.runtime.budgets import (
    BudgetDelta,
    BudgetKind,
    record_actual_replan_llm_usage,
    reserve_budget,
)
from enterprise_agent.runtime.conversation_results import (
    materialize_chat_task_turn,
)
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.runtime.replanner import ReplanContext, ReplanPlanner
from enterprise_agent.runtime.retry import classify_failure, permits_inline_retry, retry_delay
from enterprise_agent.storage.approvals import request_approval
from enterprise_agent.storage.attempts import (
    acquire_attempt,
    commit_evidence,
    fail_attempt,
    list_task_evidence,
    renew_attempt_lease,
)
from enterprise_agent.storage.checkpoints import create_checkpoint, load_latest_checkpoint
from enterprise_agent.storage.knowledge_jobs import (
    claim_knowledge_ingestion,
    complete_knowledge_ingestion,
    format_knowledge_ingestion_failure,
    get_knowledge_ingestion,
    record_knowledge_ingestion_attempt_failure,
)
from enterprise_agent.storage.model_configs import load_frozen_task_model_gateway_chain
from enterprise_agent.storage.operations import OperationIntent, operation_digest, prepare_operation
from enterprise_agent.storage.plans import load_plan_versions, load_snapshot
from enterprise_agent.storage.replans import (
    ReplanTrigger,
    apply_replan,
    get_replan_from_plan,
    select_replan_trigger,
)
from enterprise_agent.storage.tables import (
    ApprovalRow,
    ArtifactRow,
    AttemptRow,
    AuditRow,
    DomainCommandRow,
    PlanRow,
    ReplanDecisionRow,
    StepRow,
    TaskRow,
    WorkspaceMemberRow,
)


class CommitStepInput(TypedDict):
    command_id: str
    task_id: str
    workspace_id: str
    step_key: str
    final_step: bool
    failure_hold_seconds: float


class CommitStepResult(TypedDict):
    command_id: str
    step_key: str
    first_commit: bool


class LoadApprovalInput(TypedDict):
    workspace_id: str
    approval_id: str


class LoadApprovalResult(TypedDict):
    approval_id: str
    decision: str
    version: int


class FinalizeCancelInput(TypedDict):
    task_id: str
    workspace_id: str


class MaterializeChatTaskTurnInput(TypedDict):
    task_id: str
    workspace_id: str


class MaterializeChatTaskTurnResult(TypedDict):
    materialized: bool
    assistant_message_id: NotRequired[str]


async def _model_settings_for_task(
    session: AsyncSession,
    workspace_id: UUID,
    execution_snapshot_id: UUID,
) -> tuple[ModelConfig, SecretStr]:
    """Resolve Replan's primary model only from the immutable Task snapshot."""

    snapshot = await load_snapshot(session, workspace_id, execution_snapshot_id)
    needs_cipher = any(
        reference.identifier.startswith("model.config:")
        for reference in snapshot.model_config_versions
    )
    master_key = os.environ.get("SECRET_MASTER_KEY") if needs_cipher else None
    chain = await load_frozen_task_model_gateway_chain(
        session,
        workspace_id,
        snapshot.model_config_versions,
        None if master_key is None else SecretCipher(master_key),
    )
    return chain[0]


class KnowledgeIngestionInput(TypedDict):
    workspace_id: str
    ingestion_id: str


class KnowledgeIngestionResult(TypedDict):
    ingestion_id: str
    document_version_id: str


class PlanExecutionStateInput(TypedDict):
    workspace_id: str
    task_id: str


class PlanExecutionStateResult(TypedDict):
    state: str
    ready_step_ids: list[str]
    retry_after_seconds: float
    approval_ids: list[str]
    plan_id: str
    completed_step_count: int
    replan_count: int
    failed_step_keys: NotRequired[list[str]]
    replan_trigger: NotRequired[str]
    replan_reason: NotRequired[str]


class ExecutePlanStepInput(TypedDict):
    workspace_id: str
    task_id: str
    step_id: str
    attempt_id: str


class ExecutePlanStepResult(TypedDict):
    step_id: str
    state: str
    approval_id: str | None


def _select_plan_execution_candidates(
    rows: Sequence[StepRow], now: datetime
) -> tuple[list[StepRow], float | None]:
    """Select runnable steps and the next canonical wake-up deadline.

    A worker may disappear after fencing a step but before Temporal receives the
    Activity result.  Once that lease expires, the step must be offered for a
    fenced takeover.  ``acquire_attempt`` then marks the prior attempt UNKNOWN
    before issuing a higher fencing token.  Treating this state as an invariant
    error leaves the Task permanently RUNNING and makes Temporal retry the same
    deterministic exception.
    """

    runnable = [
        row
        for row in rows
        if StepState(row.state) is StepState.READY
        or (
            StepState(row.state) is StepState.RETRY_WAIT
            and row.retry_available_at is not None
            and row.retry_available_at <= now
        )
        or (
            StepState(row.state) is StepState.RUNNING
            and (row.lease_expires_at is None or row.lease_expires_at <= now)
        )
    ]
    if runnable:
        serial = [
            row
            for row in runnable
            if row.spec_payload is not None
            and (
                not PlanStepSpec.model_validate(row.spec_payload).parallelizable
                or PlanStepSpec.model_validate(row.spec_payload).risk_hint.value
                == "EXTERNAL_SIDE_EFFECT"
            )
        ]
        return (serial[:1] if serial else runnable), None

    wake_at = [
        row.retry_available_at
        for row in rows
        if StepState(row.state) is StepState.RETRY_WAIT
        and row.retry_available_at is not None
        and row.retry_available_at > now
    ]
    wake_at.extend(
        row.lease_expires_at
        for row in rows
        if StepState(row.state) is StepState.RUNNING
        and row.lease_expires_at is not None
        and row.lease_expires_at > now
    )
    if wake_at:
        return [], max(0.0, min((item - now).total_seconds() for item in wake_at))
    return [], None


class GenerateReplanInput(TypedDict):
    workspace_id: str
    task_id: str
    plan_id: str
    failed_step_keys: list[str]
    trigger: str
    reason: str


class GenerateReplanResult(TypedDict):
    status: str
    replan_id: str
    to_plan_id: str | None


class PlanExecutionBindingInput(TypedDict):
    task_id: str
    active_plan_id: str
    resume_token: str
    runtime_version: str


class PlanExecutionBindingResult(TypedDict):
    workspace_id: str
    active_plan_id: str
    resume_token: str
    runtime_version: str


class PersistContinuationInput(PlanExecutionBindingInput):
    workspace_id: str


class PersistContinuationResult(PlanExecutionBindingInput):
    pass


class PlanAdmissionInput(TypedDict):
    workspace_id: str
    task_id: str


class PlanAdmissionLeaseInput(TypedDict):
    lease_id: str
    keys: list[str]
    expires_at_ms: int


class PlanAdmissionResult(TypedDict):
    allowed: bool
    block_reason: str | None
    lease_id: str | None
    keys: list[str]
    expires_at_ms: int | None


def _activity_trace_id() -> str:
    return (activity.info().workflow_id or "temporal-activity")[:64]


def _activity_attempt_settlement_scope(trace_id: str) -> str:
    try:
        info = activity.info()
    except RuntimeError:
        # Direct activity tests do not install Temporal's activity context. Their
        # fixed trace id plus the immutable source Plan still forms a stable scope.
        return "direct:" + trace_id
    return ":".join(
        (
            info.workflow_id or "no-workflow",
            info.workflow_run_id or "no-run",
            info.activity_id,
            str(info.attempt),
        )
    )


async def _await_knowledge_operation[T](
    operation: Awaitable[T],
    heartbeat_message: Callable[[], str],
    *,
    interval_seconds: float = 5,
) -> T:
    task = asyncio.ensure_future(operation)
    try:
        while True:
            activity.heartbeat(heartbeat_message())
            done, _ = await asyncio.wait((task,), timeout=interval_seconds)
            if done:
                return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _classify_knowledge_exception(exc: Exception) -> tuple[FailureCode, str]:
    if isinstance(exc, DomainError):
        return exc.code, exc.detail
    if isinstance(exc, httpx.TimeoutException):
        return FailureCode.LLM_TIMEOUT, "embedding request timed out"
    if isinstance(exc, httpx.HTTPError):
        return FailureCode.LLM_SERVER_ERROR, "embedding service request failed"
    if isinstance(exc, SQLAlchemyError):
        return FailureCode.DB_UNAVAILABLE, "knowledge database operation failed"
    return FailureCode.WORKER_CRASH, "unexpected ingestion error: " + type(exc).__name__


def _knowledge_failure_retryable(code: FailureCode) -> bool:
    return permits_inline_retry(classify_failure(code)) or code is FailureCode.WORKER_CRASH


def _knowledge_failure_is_terminal(
    *,
    retryable: bool,
    attempt: int,
    maximum_attempts: int,
) -> bool:
    return not retryable or (maximum_attempts > 0 and attempt >= maximum_attempts)


@activity.defn(name="acquire_plan_admission")
async def acquire_plan_admission(value: PlanAdmissionInput) -> PlanAdmissionResult:
    activity.heartbeat("requesting plan admission")
    workspace_id = UUID(value["workspace_id"])
    task_id = UUID(value["task_id"])
    redis, controller, lease_ms = load_admission_controller()
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        decision, lease = await controller.try_acquire_task(
            workspace_id,
            lease_ms=lease_ms,
        )
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
            await record_schedule_decision(
                session,
                workspace_id,
                task_id,
                decision,
            )
        return {
            "allowed": decision.allowed,
            "block_reason": decision.block_reason,
            "lease_id": None if lease is None else str(lease.lease_id),
            "keys": [] if lease is None else list(lease.keys),
            "expires_at_ms": None if lease is None else lease.expires_at_ms,
        }
    finally:
        await redis.aclose()
        await engine.dispose()


@activity.defn(name="heartbeat_plan_admission")
async def heartbeat_plan_admission(
    value: PlanAdmissionLeaseInput,
) -> PlanAdmissionLeaseInput:
    activity.heartbeat("renewing plan admission")
    redis, controller, lease_ms = load_admission_controller()
    try:
        refreshed = await controller.heartbeat(
            AdmissionLease(
                lease_id=UUID(value["lease_id"]),
                keys=tuple(value["keys"]),
                expires_at_ms=value["expires_at_ms"],
            ),
            lease_ms=lease_ms,
        )
        return {
            "lease_id": str(refreshed.lease_id),
            "keys": list(refreshed.keys),
            "expires_at_ms": refreshed.expires_at_ms,
        }
    finally:
        await redis.aclose()


@activity.defn(name="release_plan_admission")
async def release_plan_admission(value: PlanAdmissionLeaseInput) -> None:
    activity.heartbeat("releasing plan admission")
    redis, controller, _ = load_admission_controller()
    try:
        await controller.release(
            AdmissionLease(
                lease_id=UUID(value["lease_id"]),
                keys=tuple(value["keys"]),
                expires_at_ms=value["expires_at_ms"],
            )
        )
    finally:
        await redis.aclose()


@activity.defn(name="commit_domain_step")
async def commit_domain_step(value: CommitStepInput) -> CommitStepResult:
    activity.heartbeat("starting")
    command_id = UUID(value["command_id"])
    task_id = UUID(value["task_id"])
    workspace_id = UUID(value["workspace_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            task = await session.scalar(
                select(TaskRow)
                .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise ValueError("task does not exist in the requested workspace")
            if task.state == TaskState.READY:
                task.state = transition(TaskState.READY, TaskState.RUNNING)
                task.version += 1
            inserted = await session.execute(
                insert(DomainCommandRow)
                .values(
                    id=command_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    step_key=value["step_key"],
                    result={"step_key": value["step_key"]},
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(DomainCommandRow.id)
            )
            first_commit = inserted.scalar_one_or_none() is not None
            stored = await session.scalar(
                select(DomainCommandRow).where(
                    DomainCommandRow.workspace_id == workspace_id,
                    DomainCommandRow.id == command_id,
                )
            )
            if stored is None or stored.task_id != task_id or stored.step_key != value["step_key"]:
                raise ValueError("idempotent command payload mismatch")
            if value["final_step"]:
                current = TaskState(task.state)
                if current is not TaskState.COMPLETED:
                    task.state = transition(current, TaskState.COMPLETED)
                    task.version += 1
        activity.heartbeat("committed")
        if first_commit and value["failure_hold_seconds"] > 0:
            await asyncio.sleep(value["failure_hold_seconds"])
        return {
            "command_id": value["command_id"],
            "step_key": value["step_key"],
            "first_commit": first_commit,
        }
    finally:
        await engine.dispose()


@activity.defn(name="finalize_task_cancel")
async def finalize_task_cancel(value: FinalizeCancelInput) -> None:
    activity.heartbeat("finalizing cancellation")
    task_id = UUID(value["task_id"])
    workspace_id = UUID(value["workspace_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
            task = await session.scalar(
                select(TaskRow)
                .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise ValueError("task does not exist in the requested workspace")
            if task.state == TaskState.CANCELLED:
                return
            task.state = transition(TaskState(task.state), TaskState.CANCELLED)
            task.version += 1
    finally:
        await engine.dispose()


@activity.defn(name="materialize_chat_task_turn")
async def materialize_chat_task_turn_activity(
    value: MaterializeChatTaskTurnInput,
) -> MaterializeChatTaskTurnResult:
    activity.heartbeat("materializing terminal chat Turn")
    task_id = UUID(value["task_id"])
    workspace_id = UUID(value["workspace_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
            message = await materialize_chat_task_turn(
                session,
                workspace_id=workspace_id,
                task_id=task_id,
                trace_id=f"task-turn:{task_id}",
            )
        activity.heartbeat("terminal chat Turn materialized")
        if message is None:
            return {"materialized": False}
        return {
            "materialized": True,
            "assistant_message_id": str(message.message_id),
        }
    finally:
        await engine.dispose()


@activity.defn(name="load_approval_decision")
async def load_approval_decision(value: LoadApprovalInput) -> LoadApprovalResult:
    activity.heartbeat("loading canonical approval")
    workspace_id = UUID(value["workspace_id"])
    approval_id = UUID(value["approval_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            row = await session.scalar(
                select(ApprovalRow).where(
                    ApprovalRow.workspace_id == workspace_id,
                    ApprovalRow.id == approval_id,
                )
            )
            if row is None or row.state not in {"APPROVED", "REJECTED", "EXPIRED"}:
                raise RuntimeError("canonical approval decision is not available")
            return {
                "approval_id": str(row.id),
                "decision": row.state,
                "version": row.version,
            }
    finally:
        await engine.dispose()


@activity.defn(name="ingest_knowledge_document")
async def ingest_knowledge_document(value: KnowledgeIngestionInput) -> KnowledgeIngestionResult:
    activity.heartbeat("claiming knowledge ingestion")
    workspace_id = UUID(value["workspace_id"])
    ingestion_id = UUID(value["ingestion_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    claim = None
    phase = "CLAIM"
    heartbeat_message = "claiming knowledge ingestion"
    try:
        async with sessions.begin() as session:
            claim = await claim_knowledge_ingestion(session, workspace_id, ingestion_id)
        if claim is None:
            async with sessions() as session:
                completed = await get_knowledge_ingestion(session, workspace_id, ingestion_id)
            if completed.document_version_id is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "completed ingestion has no document version",
                )
            return {
                "ingestion_id": value["ingestion_id"],
                "document_version_id": str(completed.document_version_id),
            }
        minio_secret = os.environ.get("MINIO_SECRET_KEY")
        if not minio_secret:
            raise DomainError(
                FailureCode.CAPABILITY_UNAVAILABLE,
                "knowledge object storage is not configured",
            )
        ingestor = KnowledgeIngestor(
            Minio(
                os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
                access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
                secret_key=minio_secret,
                secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
            ),
            bucket=os.environ.get("KNOWLEDGE_BUCKET", "enterprise-knowledge"),
        )
        phase = "LOAD"
        heartbeat_message = "loading staged knowledge object"
        blob = await _await_knowledge_operation(
            ingestor.load_blob(claim.object_name, claim.source_hash),
            lambda: heartbeat_message,
        )
        phase = "EMBED_CONFIG"
        model_config, api_key = load_embedding_settings()
        try:
            embedding_batch_size = int(os.environ.get("KNOWLEDGE_EMBED_BATCH_SIZE", "32"))
        except ValueError:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "KNOWLEDGE_EMBED_BATCH_SIZE must be a positive integer",
            ) from None
        if embedding_batch_size <= 0:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "KNOWLEDGE_EMBED_BATCH_SIZE must be a positive integer",
            )
        heartbeat_message = "preparing knowledge chunks"
        async with httpx.AsyncClient() as client:
            gateway = ModelGateway(
                model_config,
                api_key,
                client,
                admission_workspace_id=workspace_id,
            )

            async def embed(texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
                return await gateway.embed(texts, Classification.INTERNAL)

            def report_progress(next_phase: str, current: int, total: int) -> None:
                nonlocal phase, heartbeat_message
                phase = next_phase
                heartbeat_message = (
                    f"{next_phase.casefold()} knowledge document ({current}/{total})"
                )
                activity.heartbeat(heartbeat_message)

            async with sessions.begin() as session:
                document_version_id = await _await_knowledge_operation(
                    ingestor.ingest(
                        session,
                        workspace_id=claim.workspace_id,
                        document_id=claim.document_id,
                        name=claim.name,
                        media_type=claim.media_type,
                        blob=blob,
                        parser_version=claim.parser_version,
                        token_counter=lambda text: max(1, len(text.split())),
                        embedder=embed,
                        staged_object_name=claim.object_name,
                        expected_source_hash=claim.source_hash,
                        embedding_batch_size=embedding_batch_size,
                        progress=report_progress,
                    ),
                    lambda: heartbeat_message,
                )
                phase = "COMMIT"
                completed = await complete_knowledge_ingestion(session, claim, document_version_id)
                session.add(
                    AuditRow(
                        workspace_id=claim.workspace_id,
                        actor_type="AGENT",
                        actor_id=claim.ingestion_id,
                        action="knowledge.ingestion.succeeded",
                        resource_type="KNOWLEDGE_DOCUMENT",
                        resource_id=claim.document_id,
                        result={
                            "ingestion_id": str(claim.ingestion_id),
                            "document_version_id": str(document_version_id),
                        },
                        trace_id=(activity.info().workflow_id or str(claim.ingestion_id))[:64],
                    )
                )
            activity.heartbeat("knowledge ingestion committed")
            if completed.document_version_id is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "ingestion commit has no document version",
                )
            return {
                "ingestion_id": str(completed.ingestion_id),
                "document_version_id": str(completed.document_version_id),
            }
    except Exception as exc:
        code, detail = _classify_knowledge_exception(exc)
        retryable = _knowledge_failure_retryable(code)
        info = activity.info()
        maximum_attempts = 1
        if info.retry_policy is not None:
            maximum_attempts = info.retry_policy.maximum_attempts
        terminal = _knowledge_failure_is_terminal(
            retryable=retryable,
            attempt=info.attempt,
            maximum_attempts=maximum_attempts,
        )
        if claim is not None:
            try:
                async with sessions.begin() as session:
                    await record_knowledge_ingestion_attempt_failure(
                        session,
                        claim,
                        format_knowledge_ingestion_failure(code, phase, detail),
                        terminal=terminal,
                    )
            except (DomainError, SQLAlchemyError) as persistence_error:
                activity.logger.warning(
                    "knowledge failure persistence failed: %s",
                    type(persistence_error).__name__,
                )
        raise ApplicationError(
            detail,
            type=code.value,
            non_retryable=not retryable,
        ) from exc
    finally:
        await engine.dispose()


@activity.defn(name="load_plan_execution_binding")
async def load_plan_execution_binding(
    value: PlanExecutionBindingInput,
) -> PlanExecutionBindingResult:
    task_id = UUID(value["task_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            task = await session.get(TaskRow, task_id)
            if task is None or task.execution_snapshot_id is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continued task binding is unavailable",
                )
            plan = await session.scalar(
                select(PlanRow)
                .where(
                    PlanRow.workspace_id == task.workspace_id,
                    PlanRow.task_id == task_id,
                )
                .order_by(PlanRow.version.desc())
                .limit(1)
            )
            if plan is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continued task active Plan is unavailable",
                )
            active_plan_id = UUID(value["active_plan_id"]) if value["active_plan_id"] else plan.id
            if plan.id != active_plan_id:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continued task active Plan changed",
                )
            snapshot = await load_snapshot(
                session,
                task.workspace_id,
                task.execution_snapshot_id,
            )
            runtime_version = value["runtime_version"] or snapshot.runtime_version
            if snapshot.runtime_version != runtime_version:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continued task runtime version changed",
                )
            initial_token = "initial:" + str(task_id)
            resume_token = value["resume_token"] or initial_token
            if resume_token != initial_token:
                checkpoint = await load_latest_checkpoint(
                    session,
                    task.workspace_id,
                    task_id,
                )
                if (
                    checkpoint is None
                    or checkpoint.resume_token != resume_token
                    or checkpoint.active_plan_id != active_plan_id
                    or checkpoint.execution_snapshot_id != task.execution_snapshot_id
                ):
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "continued task checkpoint binding changed",
                    )
            return {
                "workspace_id": str(task.workspace_id),
                "active_plan_id": str(active_plan_id),
                "resume_token": resume_token,
                "runtime_version": runtime_version,
            }
    finally:
        await engine.dispose()


@activity.defn(name="persist_plan_continuation")
async def persist_plan_continuation(
    value: PersistContinuationInput,
) -> PersistContinuationResult:
    workspace_id = UUID(value["workspace_id"])
    task_id = UUID(value["task_id"])
    active_plan_id = UUID(value["active_plan_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
            task = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id == task_id,
                )
            )
            plan = await session.scalar(
                select(PlanRow)
                .where(
                    PlanRow.workspace_id == workspace_id,
                    PlanRow.task_id == task_id,
                )
                .order_by(PlanRow.version.desc())
                .limit(1)
            )
            if (
                task is None
                or task.execution_snapshot_id is None
                or plan is None
                or plan.id != active_plan_id
            ):
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continuation must bind the current canonical Plan",
                )
            snapshot = await load_snapshot(
                session,
                workspace_id,
                task.execution_snapshot_id,
            )
            if snapshot.runtime_version != value["runtime_version"]:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continuation runtime version changed",
                )
            checkpoint = await create_checkpoint(
                session,
                workspace_id,
                task_id,
                resume_token=value["resume_token"],
                context_summary="Temporal Continue-As-New boundary",
            )
            if checkpoint.active_plan_id != active_plan_id:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "continuation checkpoint captured a different Plan",
                )
        return {
            "task_id": str(task_id),
            "active_plan_id": str(active_plan_id),
            "resume_token": value["resume_token"],
            "runtime_version": value["runtime_version"],
        }
    finally:
        await engine.dispose()


@activity.defn(name="load_plan_execution_state")
async def load_plan_execution_state(
    value: PlanExecutionStateInput,
) -> PlanExecutionStateResult:
    workspace_id = UUID(value["workspace_id"])
    task_id = UUID(value["task_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
            task = await session.scalar(
                select(TaskRow)
                .where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id == task_id,
                )
                .with_for_update()
            )
            plan = await session.scalar(
                select(PlanRow)
                .where(
                    PlanRow.workspace_id == workspace_id,
                    PlanRow.task_id == task_id,
                )
                .order_by(PlanRow.version.desc())
                .limit(1)
            )
            if task is None or plan is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "canonical task plan is unavailable",
                )
            rows = (
                await session.scalars(
                    select(StepRow)
                    .where(
                        StepRow.workspace_id == workspace_id,
                        StepRow.task_id == task_id,
                        StepRow.plan_id == plan.id,
                    )
                    .order_by(StepRow.step_key, StepRow.id)
                )
            ).all()
            if not rows:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "canonical task plan has no materialized steps",
                )
            completed_step_count = sum(
                StepState(row.state) in {StepState.COMPLETED, StepState.SKIPPED} for row in rows
            )
            replan_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ReplanDecisionRow)
                    .where(
                        ReplanDecisionRow.workspace_id == workspace_id,
                        ReplanDecisionRow.task_id == task_id,
                    )
                )
                or 0
            )
            task_state = TaskState(task.state)
            if task_state in {
                TaskState.COMPLETED,
                TaskState.CANCELLED,
                TaskState.FAILED,
            }:
                return {
                    "state": task_state.value,
                    "ready_step_ids": [],
                    "retry_after_seconds": 0,
                    "approval_ids": [],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                }
            if any(StepState(row.state) is StepState.FAILED for row in rows):
                failed_rows = [row for row in rows if StepState(row.state) is StepState.FAILED]
                attempts = (
                    await session.scalars(
                        select(AttemptRow)
                        .where(
                            AttemptRow.workspace_id == workspace_id,
                            AttemptRow.step_id.in_([row.id for row in failed_rows]),
                            AttemptRow.state == "FAILED",
                        )
                        .order_by(AttemptRow.completed_at.desc(), AttemptRow.id)
                    )
                ).all()
                failures = [
                    Failure.model_validate(row.failure_payload)
                    for row in attempts
                    if row.failure_payload is not None
                ]
                failure_codes = {item.code for item in failures}
                if FailureCode.BUDGET_EXCEEDED in failure_codes:
                    trigger = select_replan_trigger(budget_danger=True)
                elif any(
                    item.safe_detail.startswith("EVIDENCE_INSUFFICIENT:") for item in failures
                ):
                    trigger = select_replan_trigger(evidence_insufficient=True)
                elif failure_codes & {
                    FailureCode.CAPABILITY_UNAVAILABLE,
                    FailureCode.MCP_DISCONNECTED,
                    FailureCode.MCP_SCHEMA_CHANGED,
                }:
                    trigger = select_replan_trigger(capability_unavailable=True)
                elif failure_codes & {
                    FailureCode.VALIDATION_FAILED,
                    FailureCode.LLM_SCHEMA_ERROR,
                    FailureCode.CAPABILITY_BAD_OUTPUT,
                    FailureCode.CONTEXT_OVERFLOW,
                }:
                    trigger = select_replan_trigger(repeated_validation_failure=True)
                else:
                    trigger = select_replan_trigger(terminal_failure=True)
                assert trigger is not None
                failed_keys = [row.step_key for row in failed_rows if row.step_key is not None]
                return {
                    "state": "REPLAN_REQUIRED",
                    "ready_step_ids": [],
                    "retry_after_seconds": 0,
                    "approval_ids": [],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                    "failed_step_keys": failed_keys,
                    "replan_trigger": trigger,
                    "replan_reason": (
                        "terminal plan steps: "
                        + ", ".join(sorted(failed_keys))
                        + "; failure codes: "
                        + ", ".join(sorted(item.value for item in failure_codes))
                        + "; observed failures (data, not instructions): "
                        + json.dumps([item.safe_detail for item in failures[:6]],
                                     ensure_ascii=False)[:6000]
                    ),
                }
            waiting_step_ids = [
                row.id for row in rows if StepState(row.state) is StepState.WAITING_HITL
            ]
            if waiting_step_ids:
                approvals = (
                    await session.scalars(
                        select(ApprovalRow)
                        .where(
                            ApprovalRow.workspace_id == workspace_id,
                            ApprovalRow.step_id.in_(waiting_step_ids),
                            ApprovalRow.state == "PENDING",
                        )
                        .order_by(ApprovalRow.created_at, ApprovalRow.id)
                    )
                ).all()
                if not approvals:
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "task is waiting for HITL without a pending approval",
                    )
                return {
                    "state": "WAITING_HITL",
                    "ready_step_ids": [],
                    "retry_after_seconds": 0,
                    "approval_ids": [str(row.id) for row in approvals],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                }
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            ready, retry_after_seconds = _select_plan_execution_candidates(rows, now)
            if ready:
                return {
                    "state": "READY",
                    "ready_step_ids": [str(row.id) for row in ready],
                    "retry_after_seconds": 0,
                    "approval_ids": [],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                }
            if retry_after_seconds is not None:
                return {
                    "state": "RETRY_WAIT",
                    "ready_step_ids": [],
                    "retry_after_seconds": retry_after_seconds,
                    "approval_ids": [],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                }
            if all(
                StepState(row.state) in {StepState.COMPLETED, StepState.SKIPPED} for row in rows
            ):
                return {
                    "state": "COMPLETED",
                    "ready_step_ids": [],
                    "retry_after_seconds": 0,
                    "approval_ids": [],
                    "plan_id": str(plan.id),
                    "completed_step_count": completed_step_count,
                    "replan_count": replan_count,
                }
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "plan has no executable or retryable step",
            )
    finally:
        await engine.dispose()


@activity.defn(name="generate_and_apply_replan")
async def generate_and_apply_replan(
    value: GenerateReplanInput,
) -> GenerateReplanResult:
    workspace_id = UUID(value["workspace_id"])
    task_id = UUID(value["task_id"])
    from_plan_id = UUID(value["plan_id"])
    trigger = value["trigger"]
    if trigger not in {
        "TERMINAL_FAILURE",
        "EVIDENCE_INSUFFICIENT",
        "CAPABILITY_UNAVAILABLE",
        "VALIDATION_FAILURE",
        "USER_MODIFICATION",
        "BUDGET_DANGER",
    }:
        raise DomainError(FailureCode.VALIDATION_FAILED, "unknown Replan trigger")
    validated_trigger = cast(ReplanTrigger, trigger)
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            replay = await get_replan_from_plan(session, workspace_id, task_id, from_plan_id)
            if replay is not None:
                return {
                    "status": replay.status,
                    "replan_id": str(replay.replan_id),
                    "to_plan_id": (None if replay.to_plan_id is None else str(replay.to_plan_id)),
                }
            versions = await load_plan_versions(session, workspace_id, task_id)
            if not versions or versions[-1].plan_id != from_plan_id:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "Replan activity source is not the latest Plan",
                )
            current: Plan = versions[-1]
            task = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id == task_id,
                )
            )
            if task is None:
                raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "Replan task unavailable")
            actor_id = task.created_by or task_id
            if task.goal_payload is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "Replan task goal unavailable",
                )
            replan_goal = TaskGoal.model_validate(task.goal_payload)
            observed_evidence = await list_task_evidence(session, workspace_id, task_id)
            observed_sources = tuple(dict.fromkeys(
                f"{item.locator[:600]} | {item.content.splitlines()[0][:160] if item.content else ''}"
                for item in observed_evidence if item.source_type in {"WEB", "DOCUMENT"}
            ))[:12]
            if task.execution_snapshot_id is None:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "Replan task execution snapshot unavailable",
                )
            execution_snapshot_id = task.execution_snapshot_id

        fallback = ReplanDecision(
            reason="bounded Replan generation could not safely produce a new Plan",
            trigger=validated_trigger,
            action="ABORT_TASK",
            affected_steps=tuple(value["failed_step_keys"]),
            new_steps=(),
            preserve_completed_steps=True,
            budget_impact=0,
        )
        decision = fallback
        if trigger != "BUDGET_DANGER":
            try:
                async with sessions() as session:
                    config, api_key = await _model_settings_for_task(
                        session,
                        workspace_id,
                        execution_snapshot_id,
                    )
                async with httpx.AsyncClient(trust_env=False) as client:
                    planner = ReplanPlanner(
                        ModelGateway(
                            config,
                            api_key,
                            client,
                            admission_workspace_id=workspace_id,
                            admission_task_id=task_id,
                            admission_sessions=sessions,
                        )
                    )

                    async def reserve_model_call() -> None:
                        async with sessions.begin() as session:
                            budget = await reserve_budget(
                                session,
                                workspace_id,
                                task_id,
                                actor_id,
                                _activity_trace_id(),
                                BudgetDelta(kind=BudgetKind.LLM_CALL),
                            )
                        if not budget.allowed:
                            raise DomainError(
                                FailureCode.BUDGET_EXCEEDED,
                                "Replan model call budget exceeded",
                            )

                    trace_id = _activity_trace_id()
                    settlement_scope = _activity_attempt_settlement_scope(trace_id)
                    provider_reply_index = 0

                    async def settle_model_call(usage: ModelUsage) -> None:
                        nonlocal provider_reply_index
                        provider_reply_index += 1
                        async with sessions.begin() as session:
                            await record_actual_replan_llm_usage(
                                session,
                                workspace_id,
                                task_id,
                                from_plan_id,
                                actor_id,
                                trace_id,
                                f"{settlement_scope}:reply:{provider_reply_index}",
                                tokens=usage.input_tokens + usage.output_tokens,
                            )

                    activity.heartbeat("generating structured Replan decision")
                    generated, _ = await planner.generate(
                        ReplanContext(
                            plan=current,
                            trigger=validated_trigger,
                            reason=value["reason"],
                            failed_step_keys=tuple(value["failed_step_keys"]),
                            goal_context=replan_goal.model_dump_json(),
                            observed_sources=observed_sources,
                        ),
                        before_call=reserve_model_call,
                        after_call=settle_model_call,
                    )
                decision = generated
            except (DomainError, ValueError) as exc:
                activity.logger.warning("Replan generation rejected: %s", exc)
                decision = fallback

        try:
            async with sessions.begin() as session:
                applied = await apply_replan(
                    session,
                    workspace_id,
                    task_id,
                    actor_id,
                    _activity_trace_id(),
                    decision,
                    expected_from_plan_id=from_plan_id,
                )
        except (DomainError, ValueError):
            if decision.action == "ABORT_TASK":
                raise
            async with sessions.begin() as session:
                applied = await apply_replan(
                    session,
                    workspace_id,
                    task_id,
                    actor_id,
                    _activity_trace_id(),
                    fallback,
                    expected_from_plan_id=from_plan_id,
                )
        return {
            "status": applied.status,
            "replan_id": str(applied.replan_id),
            "to_plan_id": None if applied.to_plan_id is None else str(applied.to_plan_id),
        }
    finally:
        await engine.dispose()


def _replanned_objective(
    spec: PlanStepSpec, originals: Mapping[str, PlanStepSpec]
) -> str | None:
    original = originals.get(spec.step_key)
    if original is None or original.objective != spec.objective:
        return spec.objective
    return None


def _replanned_query_overrides(
    capability_id: str, objective: str | None
) -> dict[str, JsonValue]:
    # Only a persisted changed Plan may override the original read query.
    # Document identity, workspace, permission and write arguments stay governed.
    if objective is not None and capability_id in {"web.search", "knowledge.hybrid_search"}:
        return {"query": objective}
    return {}


def _capability_arguments(
    descriptor_schema: Mapping[str, JsonValue],
    workspace_id: UUID,
    actor_id: UUID,
    operation_id: UUID,
    user_context: str,
    objective: str,
    *,
    context_overrides: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    context: dict[str, JsonValue]
    if not user_context.strip():
        context = {}
    else:
        try:
            parsed = json.loads(user_context)
        except json.JSONDecodeError:
            parsed = {"user_context": user_context}
        context = (
            cast(dict[str, JsonValue], parsed)
            if isinstance(parsed, dict)
            else {"user_context": user_context}
        )
    if context_overrides:
        context.update(context_overrides)
    context.setdefault("query", objective)
    context.setdefault("instruction", objective)
    if context_overrides and isinstance(context_overrides.get("synthesis_guidance"), str):
        context["instruction"] = (
            str(context["instruction"]) + "\n\nRecovery objective (preserve the original request):\n"
            + str(context_overrides["synthesis_guidance"])
        )
    return _schema_object_arguments(
        descriptor_schema,
        context,
        {
            "workspace_id": str(workspace_id),
            "requested_by": str(actor_id),
            "operation_id": str(operation_id),
        },
    )


def _schema_object_arguments(
    schema: Mapping[str, JsonValue],
    context: Mapping[str, JsonValue],
    injected: Mapping[str, JsonValue],
    root_schema: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    if root_schema is None:
        root_schema = schema
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise DomainError(FailureCode.CAPABILITY_BAD_OUTPUT, "capability input schema is invalid")
    arguments: dict[str, JsonValue] = {}
    for name, property_schema in properties.items():
        resolved_schema = property_schema
        if isinstance(property_schema, dict):
            reference = property_schema.get("$ref")
            definitions = root_schema.get("$defs")
            if (
                isinstance(reference, str)
                and reference.startswith("#/$defs/")
                and isinstance(definitions, dict)
            ):
                resolved_schema = definitions.get(reference.removeprefix("#/$defs/"))
        if name in injected:
            arguments[name] = injected[name]
        elif name in context:
            value = context[name]
            # API clients commonly encode an omitted optional field as explicit
            # null. Do not turn that harmless representation into an invalid tool
            # call when the capability schema does not admit null. Required fields
            # still flow through validation unchanged.
            if (
                value is None
                and name not in required
                and not _schema_allows_null(resolved_schema)
            ):
                continue
            arguments[name] = value
        elif (
            isinstance(resolved_schema, dict)
            and resolved_schema.get("type") == "object"
            and name in required
        ):
            arguments[name] = _schema_object_arguments(
                resolved_schema,
                context,
                injected,
                root_schema,
            )
    missing = [name for name in required if isinstance(name, str) and name not in arguments]
    if missing:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "task context is missing capability inputs: " + ", ".join(sorted(missing)),
        )
    return arguments


def _schema_allows_null(schema: JsonValue) -> bool:
    if not isinstance(schema, dict):
        return False
    declared_type = schema.get("type")
    if declared_type == "null":
        return True
    if isinstance(declared_type, list) and "null" in declared_type:
        return True
    for keyword in ("anyOf", "oneOf"):
        alternatives = schema.get(keyword)
        if isinstance(alternatives, list) and any(
            _schema_allows_null(item) for item in alternatives
        ):
            return True
    return False


def _result_evidence(
    *,
    request: CapabilityRequest,
    descriptor: CapabilityDescriptor,
    result_output: JsonValue,
    derived_from: tuple[UUID, ...],
) -> tuple[Evidence, ...]:
    records: list[tuple[str, str, str, float]] = []
    if descriptor.kind == "RAG" and isinstance(result_output, dict):
        hits = result_output.get("hits")
        if isinstance(hits, list):
            for item in hits:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                source_id = item.get("source_id")
                locator = item.get("locator")
                # Rank scores (notably raw RRF values around 1 / 61) are not
                # probabilities. New retrieval output provides reviewer-facing
                # relevance explicitly; retain `score` as a legacy fallback.
                score = item.get("relevance", item.get("score", 1))
                if (
                    isinstance(content, str)
                    and isinstance(source_id, str)
                    and isinstance(locator, str)
                    and isinstance(score, (int, float))
                ):
                    records.append((source_id, locator, content, max(0, min(1, float(score)))))
        if not records:
            # An empty retrieval is an execution outcome, not selected-document
            # Evidence. Never promote the provider envelope to a confidence-1
            # DOCUMENT source; downstream fallback/review must see the real gap.
            return ()
    elif descriptor.kind == "WEB" and isinstance(result_output, dict):
        values = result_output.get("results")
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                content = item.get("snippet")
                if isinstance(url, str) and isinstance(content, str):
                    records.append((url, url, content, 1))
        elif isinstance(result_output.get("url"), str) and isinstance(
            result_output.get("content"), str
        ):
            url = cast(str, result_output["url"])
            records.append((url, url, cast(str, result_output["content"]), 1))
        if not records:
            # An empty search/fetch response is an execution outcome, not a source.
            # Do not turn the provider envelope into high-confidence WEB Evidence.
            return ()
    if not records:
        content = json.dumps(
            result_output,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        records.append(
            (
                descriptor.id,
                f"{descriptor.kind.casefold()}:{descriptor.id}",
                content,
                1,
            )
        )
    source_type = {
        "RAG": "DOCUMENT",
        "WEB": "WEB",
        "MCP": "MCP",
    }.get(descriptor.kind, "DERIVED")
    return tuple(
        Evidence.model_validate(
            {
                "evidence_id": uuid5(
                    NAMESPACE_URL,
                    f"plan-evidence:{request.command_id}:{index}",
                ),
                "workspace_id": request.workspace_id,
                "task_id": request.task_id,
                "step_id": request.step_id,
                "source_type": source_type,
                "source_id": source_id,
                "locator": locator,
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "source_hash": hashlib.sha256(content.encode()).hexdigest(),
                "source_version": request.capability_version,
                "confidence": 1,
                "relevance": relevance,
                "retrieved_at": datetime.now(UTC),
                "generated_by": request.attempt_id,
                "derived_from": derived_from if source_type == "DERIVED" else (),
            }
        )
        for index, (source_id, locator, content, relevance) in enumerate(records)
    )


def _supplier_admission_decision(
    evidence: tuple[Evidence, ...],
) -> tuple[str, Evidence]:
    candidates = sorted(
        (item for item in evidence if item.source_id == "model.structured_output"),
        key=lambda item: (item.retrieved_at, item.evidence_id),
        reverse=True,
    )
    for item in candidates:
        try:
            payload = json.loads(item.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        decision = payload.get("decision")
        sufficient = payload.get("evidence_sufficient")
        if decision in {"ADMIT", "REJECT", "NEEDS_MORE_EVIDENCE"} and isinstance(sufficient, bool):
            if not sufficient:
                return "NEEDS_MORE_EVIDENCE", item
            return cast(str, decision), item
    raise DomainError(
        FailureCode.VALIDATION_FAILED,
        "supplier admission eligibility decision is unavailable",
    )


def _admission_noop_evidence(
    *,
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    decision: str,
    decision_evidence: Evidence,
) -> Evidence:
    content = json.dumps(
        {
            "decision": decision,
            "outcome": "NO_OP",
            "reason": "reviewed supplier decision is not eligible for admission",
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    return Evidence.model_validate(
        {
            "evidence_id": uuid5(
                NAMESPACE_URL,
                f"conditional-admission-noop:{workspace_id}:{task_id}:{step_id}",
            ),
            "workspace_id": workspace_id,
            "task_id": task_id,
            "step_id": step_id,
            "source_type": "DERIVED",
            "source_id": "runtime.conditional_admission",
            "locator": "decision:" + str(decision_evidence.evidence_id),
            "content": content,
            "content_hash": digest,
            "source_hash": digest,
            "source_version": "1.0.0",
            "confidence": 1,
            "relevance": 1,
            "retrieved_at": datetime.now(UTC),
            "generated_by": attempt_id,
            "derived_from": (decision_evidence.evidence_id,),
        }
    )


@activity.defn(name="execute_governed_plan_step")
async def execute_governed_plan_step(
    value: ExecutePlanStepInput,
) -> ExecutePlanStepResult:
    activity.heartbeat("claiming canonical plan step")
    workspace_id = UUID(value["workspace_id"])
    task_id = UUID(value["task_id"])
    step_id = UUID(value["step_id"])
    attempt_id = UUID(value["attempt_id"])
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    admission_redis, admission_controller, admission_lease_ms = load_admission_controller()
    token = 0
    descriptor_for_retry = None
    try:
        async with sessions.begin() as session:
            existing_attempt = await session.scalar(
                select(AttemptRow).where(
                    AttemptRow.workspace_id == workspace_id,
                    AttemptRow.id == attempt_id,
                )
            )
            if existing_attempt is not None and existing_attempt.state != "RUNNING":
                if existing_attempt.state == "SUCCEEDED":
                    return {
                        "step_id": value["step_id"],
                        "state": StepState.COMPLETED.value,
                        "approval_id": None,
                    }
                approval = await session.scalar(
                    select(ApprovalRow)
                    .where(
                        ApprovalRow.workspace_id == workspace_id,
                        ApprovalRow.step_id == step_id,
                    )
                    .order_by(ApprovalRow.created_at.desc())
                    .limit(1)
                )
                if existing_attempt.state == "CANCELLED" and approval is not None:
                    return {
                        "step_id": value["step_id"],
                        "state": StepState.WAITING_HITL.value,
                        "approval_id": str(approval.id),
                    }
                step_state = await session.scalar(
                    select(StepRow.state).where(
                        StepRow.workspace_id == workspace_id,
                        StepRow.id == step_id,
                    )
                )
                return {
                    "step_id": value["step_id"],
                    "state": str(step_state or StepState.FAILED.value),
                    "approval_id": None,
                }
            attempt = await acquire_attempt(
                session,
                workspace_id,
                step_id,
                attempt_id,
                "temporal-plan-worker",
                _activity_trace_id(),
                lease_seconds=120,
            )
            token = attempt.fencing_token
            if existing_attempt is None:
                budget = await reserve_budget(
                    session,
                    workspace_id,
                    task_id,
                    attempt_id,
                    _activity_trace_id(),
                    BudgetDelta(kind=BudgetKind.STEP),
                )
                if not budget.allowed:
                    await fail_attempt(
                        session,
                        workspace_id,
                        attempt_id,
                        token,
                        Failure(
                            code=FailureCode.BUDGET_EXCEEDED,
                            safe_detail="task step budget exceeded",
                            trace_id=_activity_trace_id(),
                        ),
                        retryable=False,
                        retry_delay_seconds=0,
                    )
                    return {
                        "step_id": value["step_id"],
                        "state": StepState.FAILED.value,
                        "approval_id": None,
                    }
        async with sessions() as session:
            task = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id == task_id,
                )
            )
            step = await session.scalar(
                select(StepRow).where(
                    StepRow.workspace_id == workspace_id,
                    StepRow.id == step_id,
                    StepRow.task_id == task_id,
                )
            )
            if (
                task is None
                or step is None
                or task.created_by is None
                or task.goal_payload is None
                or task.execution_snapshot_id is None
                or step.spec_payload is None
            ):
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "plan step execution binding is incomplete",
                )
            goal = TaskGoal.model_validate(task.goal_payload)
            spec = PlanStepSpec.model_validate(step.spec_payload)
            plan_versions = await load_plan_versions(session, workspace_id, task_id)
            original_specs = {item.step_key: item for item in plan_versions[0].steps}
            revised_objective = _replanned_objective(spec, original_specs)
            snapshot = await load_snapshot(session, workspace_id, task.execution_snapshot_id)
            membership = await session.scalar(
                select(WorkspaceMemberRow).where(
                    WorkspaceMemberRow.workspace_id == workspace_id,
                    WorkspaceMemberRow.user_id == task.created_by,
                )
            )
            if membership is None:
                raise DomainError(FailureCode.POLICY_DENIED, "task creator membership unavailable")
            actor_id = task.created_by
            role = Role(membership.role)
            prior_evidence = await list_task_evidence(session, workspace_id, task_id)
        admission_context: dict[str, JsonValue] = {}
        if spec.step_key == "create-admission-if-eligible":
            decision, decision_evidence = _supplier_admission_decision(prior_evidence)
            if decision != "ADMIT":
                noop = _admission_noop_evidence(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    step_id=step_id,
                    attempt_id=attempt_id,
                    decision=decision,
                    decision_evidence=decision_evidence,
                )
                async with sessions.begin() as session:
                    await commit_evidence(
                        session,
                        workspace_id,
                        attempt_id,
                        token,
                        (noop,),
                    )
                    session.add(
                        AuditRow(
                            workspace_id=workspace_id,
                            actor_type="AGENT",
                            actor_id=attempt_id,
                            task_id=task_id,
                            step_id=step_id,
                            action="admission.noop",
                            resource_type="TASK_STEP",
                            resource_id=step_id,
                            result={
                                "state": StepState.COMPLETED.value,
                                "decision": decision,
                                "evidence_id": str(decision_evidence.evidence_id),
                            },
                            trace_id=_activity_trace_id(),
                        )
                    )
                return {
                    "step_id": value["step_id"],
                    "state": StepState.COMPLETED.value,
                    "approval_id": None,
                }
            async with sessions() as session:
                artifact_row = await session.scalar(
                    select(ArtifactRow)
                    .where(
                        ArtifactRow.workspace_id == workspace_id,
                        ArtifactRow.task_id == task_id,
                    )
                    .order_by(ArtifactRow.created_at.desc(), ArtifactRow.id.desc())
                    .limit(1)
                )
                if artifact_row is None:
                    raise DomainError(
                        FailureCode.VALIDATION_FAILED,
                        "eligible supplier admission requires an immutable report artifact",
                    )
                report = ArtifactManifest.model_validate(artifact_row.payload)
                await load_artifact_store().get(
                    session,
                    workspace_id,
                    report.artifact_id,
                )
            admission_context["report_sha256"] = report.sha256
        registry = await load_mcp_registry(sessions, workspace_id)
        register_builtin_capabilities(registry, sessions)
        version_by_id = {item.identifier: item.version for item in snapshot.capability_versions}
        executor = CapabilityExecutor(
            sessions,
            registry,
            admission_controller=admission_controller,
            admission_lease_ms=admission_lease_ms,
        )
        evidence: list[Evidence] = []
        for requirement_index, requirement in enumerate(spec.capability_requirements):
            version = version_by_id.get(requirement.capability_id)
            if version is None:
                raise DomainError(
                    FailureCode.CAPABILITY_UNAVAILABLE,
                    "capability is absent from execution snapshot",
                )
            entry = registry.resolve(requirement.capability_id, version)
            descriptor_for_retry = entry.descriptor
            logical_key = (
                f"plan:{task_id}:{step_id}:{requirement_index}:{requirement.capability_id}"
            )
            operation_id = uuid5(
                NAMESPACE_URL,
                f"enterprise-operation:{workspace_id}:{step_id}:{logical_key}",
            )
            arguments = _capability_arguments(
                entry.descriptor.input_schema,
                workspace_id,
                actor_id,
                operation_id,
                goal.user_context,
                spec.objective,
                context_overrides={
                    **_replanned_query_overrides(requirement.capability_id, revised_objective),
                    **({"synthesis_guidance": revised_objective}
                       if requirement.capability_id == "model.structured_output" and revised_objective is not None
                       else {}),
                    **admission_context,
                },
            )
            command_id = uuid5(
                NAMESPACE_URL,
                f"plan-capability:{workspace_id}:{attempt_id}:{requirement.capability_id}",
            )
            policy_input = PolicyInput(
                subject=Subject(
                    actor_id=actor_id,
                    actor_type="USER",
                    workspace_id=workspace_id,
                    role=role,
                ),
                resource_workspace_id=workspace_id,
                resource_id=task_id,
                capability=entry.descriptor,
                capability_allowlist=frozenset(version_by_id),
                classification=Classification.INTERNAL,
                external_provider=False,
                policy_version=snapshot.policy_version.sha256,
            )
            approval_id: UUID | None = None
            if entry.descriptor.side_effect_mode != "NONE":
                intent = OperationIntent(
                    workspace_id=workspace_id,
                    step_id=step_id,
                    logical_key=logical_key,
                    capability_id=requirement.capability_id,
                    capability_version=version,
                    arguments=arguments,
                )
                async with sessions.begin() as session:
                    prepared = await prepare_operation(session, intent, attempt_id, token)
                    if prepared.operation_id != operation_id:
                        raise DomainError(
                            FailureCode.CONSISTENCY_VIOLATION,
                            "prepared operation identity changed",
                        )
                    policy = evaluate(policy_input)
                    if policy.decision == Decision.REQUIRE_APPROVAL:
                        approval = await session.scalar(
                            select(ApprovalRow).where(
                                ApprovalRow.workspace_id == workspace_id,
                                ApprovalRow.operation_id == operation_id,
                            )
                        )
                        if approval is None:
                            database_now = await session.scalar(select(func.clock_timestamp()))
                            if database_now is None:
                                raise DomainError(
                                    FailureCode.DB_UNAVAILABLE,
                                    "database clock unavailable for approval deadline",
                                )
                            approval_id = uuid5(
                                NAMESPACE_URL,
                                f"plan-approval:{workspace_id}:{operation_id}",
                            )
                            approval = await request_approval(
                                session,
                                ApprovalRequest(
                                    approval_id=approval_id,
                                    workspace_id=workspace_id,
                                    task_id=task_id,
                                    operation_id=operation_id,
                                    capability_id=requirement.capability_id,
                                    capability_version=version,
                                    parameters=arguments,
                                    parameter_hash=operation_digest(intent),
                                    policy_version=policy.policy_version,
                                    reason=policy.reason,
                                    risk=entry.descriptor.risk_level,
                                    evidence_ids=tuple(item.evidence_id for item in prior_evidence),
                                    expires_at=database_now + timedelta(hours=24),
                                ),
                                attempt_id,
                                token,
                            )
                            return {
                                "step_id": value["step_id"],
                                "state": StepState.WAITING_HITL.value,
                                "approval_id": str(approval.id),
                            }
                        if approval.state == "PENDING":
                            raise DomainError(
                                FailureCode.CONSISTENCY_VIOLATION,
                                "pending approval is bound to a runnable step",
                            )
                        if approval.state != "APPROVED":
                            raise DomainError(
                                FailureCode.APPROVAL_REJECTED,
                                "operation approval was not granted",
                            )
                        approval_id = approval.id
            request = CapabilityRequest(
                command_id=command_id,
                operation_id=operation_id,
                workspace_id=workspace_id,
                task_id=task_id,
                step_id=step_id,
                attempt_id=attempt_id,
                fencing_token=token,
                capability_id=requirement.capability_id,
                capability_version=version,
                arguments=arguments,
            )
            heartbeat_capability_id = requirement.capability_id

            async def heartbeat_capability(
                capability_id: str = heartbeat_capability_id,
            ) -> None:
                while True:
                    await asyncio.sleep(5)
                    async with sessions.begin() as heartbeat_session:
                        await renew_attempt_lease(
                            heartbeat_session,
                            workspace_id,
                            attempt_id,
                            token,
                            lease_seconds=120,
                        )
                    activity.heartbeat(
                        "capability in progress",
                        capability_id,
                    )

            heartbeat_task = asyncio.create_task(heartbeat_capability())
            try:
                result = await executor.execute(
                    request,
                    policy_input,
                    approval_id=approval_id,
                )
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if result.outcome != "SUCCEEDED":
                assert result.failure is not None
                raise DomainError(result.failure.code, result.failure.safe_detail)
            if (
                isinstance(result.output, dict)
                and result.output.get("evidence_sufficient") is False
            ):
                raise DomainError(
                    FailureCode.VALIDATION_FAILED,
                    "EVIDENCE_INSUFFICIENT: " + json.dumps(
                        {"missing_information": result.output.get("missing_information", []),
                         "step": spec.step_key}, ensure_ascii=False
                    )[:3000],
                )
            evidence.extend(
                _result_evidence(
                    request=request,
                    descriptor=entry.descriptor,
                    result_output=result.output,
                    derived_from=tuple(item.evidence_id for item in prior_evidence)
                    + tuple(item.evidence_id for item in evidence),
                )
            )
            activity.heartbeat("capability committed", requirement.capability_id)
        async with sessions.begin() as session:
            await commit_evidence(
                session,
                workspace_id,
                attempt_id,
                token,
                tuple(evidence),
            )
        return {
            "step_id": value["step_id"],
            "state": StepState.COMPLETED.value,
            "approval_id": None,
        }
    except DomainError as exc:
        if token:
            retryable = permits_inline_retry(
                classify_failure(
                    exc.code,
                    has_side_effect=(
                        descriptor_for_retry is not None
                        and descriptor_for_retry.side_effect_mode != "NONE"
                    ),
                )
            )
            delay = (
                retry_delay(descriptor_for_retry.retry_policy, 1)
                if retryable and descriptor_for_retry is not None
                else 0
            )
            try:
                async with sessions.begin() as session:
                    state = await fail_attempt(
                        session,
                        workspace_id,
                        attempt_id,
                        token,
                        Failure(
                            code=exc.code,
                            safe_detail=exc.detail,
                            trace_id=_activity_trace_id(),
                        ),
                        retryable=retryable,
                        retry_delay_seconds=delay,
                    )
                return {
                    "step_id": value["step_id"],
                    "state": state.value,
                    "approval_id": None,
                }
            except DomainError:
                pass
        raise
    finally:
        await admission_redis.aclose()
        await engine.dispose()
