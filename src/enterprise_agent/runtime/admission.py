import asyncio
import os
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.contracts.domain import Contract, FailureCode, TaskState
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import TaskRow

ACQUIRE_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
for index, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  if redis.call('ZCARD', key) >= tonumber(ARGV[index + 2]) then
    return {0, index}
  end
end
local expires = now + tonumber(ARGV[2])
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, expires, ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2]) * 2)
end
return {1, 0, expires}
"""

RELEASE_SCRIPT = """
for _, key in ipairs(KEYS) do
  redis.call('ZREM', key, ARGV[1])
end
return 1
"""

HEARTBEAT_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local expires = now + tonumber(ARGV[2])
for _, key in ipairs(KEYS) do
  if redis.call('ZSCORE', key, ARGV[1]) == false then
    return 0
  end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, expires, ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2]) * 2)
end
return expires
"""


@dataclass(frozen=True)
class AdmissionLimits:
    global_tasks: int
    workspace_tasks: int
    model_provider: int
    capability: int
    mcp: int

    def __post_init__(self) -> None:
        if min(
            self.global_tasks,
            self.workspace_tasks,
            self.model_provider,
            self.capability,
            self.mcp,
        ) < 1:
            raise ValueError("admission limits must be positive")


class AdmissionDecision(Contract):
    allowed: bool
    lease_id: UUID | None = None
    block_reason: str | None = None
    expires_at_ms: int | None = None


@dataclass(frozen=True)
class AdmissionLease:
    lease_id: UUID
    keys: tuple[str, ...]
    expires_at_ms: int


class RedisAdmissionController:
    def __init__(self, redis: Redis, limits: AdmissionLimits, namespace: str) -> None:
        self._redis = redis
        self._limits = limits
        self._namespace = namespace

    async def try_acquire(
        self,
        workspace_id: UUID,
        provider: str,
        capability_id: str,
        *,
        is_model: bool = False,
        is_mcp: bool = False,
        lease_ms: int = 30_000,
    ) -> tuple[AdmissionDecision, AdmissionLease | None]:
        if lease_ms < 100:
            raise DomainError(FailureCode.VALIDATION_FAILED, "admission lease is too short")
        scopes: list[tuple[str, int, str]] = [
            ("global", self._limits.global_tasks, "global_task_concurrency"),
            (
                "workspace:" + str(workspace_id),
                self._limits.workspace_tasks,
                "workspace_task_concurrency",
            ),
            (
                "capability:" + capability_id,
                self._limits.capability,
                "capability_concurrency",
            ),
        ]
        if is_model:
            scopes.append(
                (
                    "model:" + provider,
                    self._limits.model_provider,
                    "model_provider_concurrency",
                )
            )
        if is_mcp:
            scopes.append(("mcp:" + provider, self._limits.mcp, "mcp_concurrency"))
        keys = tuple(self._namespace + ":" + item[0] for item in scopes)
        lease_id = uuid4()
        raw = await cast(
            Awaitable[Any],
            self._redis.eval(
                ACQUIRE_SCRIPT,
                len(keys),
                *keys,
                str(lease_id),
                str(lease_ms),
                *(str(item[1]) for item in scopes),
            ),
        )
        values = list(raw)
        if int(values[0]) == 0:
            blocked_index = int(values[1]) - 1
            return (
                AdmissionDecision(
                    allowed=False,
                    block_reason=scopes[blocked_index][2],
                ),
                None,
            )
        expires_at_ms = int(values[2])
        lease = AdmissionLease(
            lease_id=lease_id,
            keys=keys,
            expires_at_ms=expires_at_ms,
        )
        return (
            AdmissionDecision(
                allowed=True,
                lease_id=lease_id,
                expires_at_ms=expires_at_ms,
            ),
            lease,
        )

    async def try_acquire_task(
        self,
        workspace_id: UUID,
        *,
        lease_ms: int = 30_000,
    ) -> tuple[AdmissionDecision, AdmissionLease | None]:
        if lease_ms < 100:
            raise DomainError(FailureCode.VALIDATION_FAILED, "admission lease is too short")
        scopes = [
            ("global", self._limits.global_tasks, "global_task_concurrency"),
            (
                "workspace:" + str(workspace_id),
                self._limits.workspace_tasks,
                "workspace_task_concurrency",
            ),
        ]
        keys = tuple(self._namespace + ":" + item[0] for item in scopes)
        lease_id = uuid4()
        raw = await cast(
            Awaitable[Any],
            self._redis.eval(
                ACQUIRE_SCRIPT,
                len(keys),
                *keys,
                str(lease_id),
                str(lease_ms),
                *(str(item[1]) for item in scopes),
            ),
        )
        values = list(raw)
        if int(values[0]) == 0:
            blocked_index = int(values[1]) - 1
            return (
                AdmissionDecision(
                    allowed=False,
                    block_reason=scopes[blocked_index][2],
                ),
                None,
            )
        expires_at_ms = int(values[2])
        lease = AdmissionLease(
            lease_id=lease_id,
            keys=keys,
            expires_at_ms=expires_at_ms,
        )
        return (
            AdmissionDecision(
                allowed=True,
                lease_id=lease_id,
                expires_at_ms=expires_at_ms,
            ),
            lease,
        )

    async def try_acquire_call(
        self,
        workspace_id: UUID,
        provider: str,
        capability_id: str,
        *,
        is_model: bool = False,
        is_mcp: bool = False,
        lease_ms: int = 30_000,
    ) -> tuple[AdmissionDecision, AdmissionLease | None]:
        del workspace_id
        if lease_ms < 100:
            raise DomainError(FailureCode.VALIDATION_FAILED, "admission lease is too short")
        scopes: list[tuple[str, int, str]] = [
            (
                "capability:" + capability_id,
                self._limits.capability,
                "capability_concurrency",
            ),
        ]
        if is_model:
            scopes.append(
                (
                    "model:" + provider,
                    self._limits.model_provider,
                    "model_provider_concurrency",
                )
            )
        if is_mcp:
            scopes.append(("mcp:" + provider, self._limits.mcp, "mcp_concurrency"))
        keys = tuple(self._namespace + ":" + item[0] for item in scopes)
        lease_id = uuid4()
        raw = await cast(
            Awaitable[Any],
            self._redis.eval(
                ACQUIRE_SCRIPT,
                len(keys),
                *keys,
                str(lease_id),
                str(lease_ms),
                *(str(item[1]) for item in scopes),
            ),
        )
        values = list(raw)
        if int(values[0]) == 0:
            blocked_index = int(values[1]) - 1
            return (
                AdmissionDecision(
                    allowed=False,
                    block_reason=scopes[blocked_index][2],
                ),
                None,
            )
        expires_at_ms = int(values[2])
        lease = AdmissionLease(
            lease_id=lease_id,
            keys=keys,
            expires_at_ms=expires_at_ms,
        )
        return (
            AdmissionDecision(
                allowed=True,
                lease_id=lease_id,
                expires_at_ms=expires_at_ms,
            ),
            lease,
        )

    async def heartbeat(self, lease: AdmissionLease, lease_ms: int = 30_000) -> AdmissionLease:
        raw = await cast(
            Awaitable[Any],
            self._redis.eval(
                HEARTBEAT_SCRIPT,
                len(lease.keys),
                *lease.keys,
                str(lease.lease_id),
                str(lease_ms),
            ),
        )
        expires_at_ms = int(raw)
        if expires_at_ms == 0:
            raise DomainError(FailureCode.STALE_ATTEMPT, "admission lease expired")
        return AdmissionLease(lease.lease_id, lease.keys, expires_at_ms)

    async def release(self, lease: AdmissionLease) -> None:
        await cast(
            Awaitable[Any],
            self._redis.eval(
                RELEASE_SCRIPT,
                len(lease.keys),
                *lease.keys,
                str(lease.lease_id),
            ),
        )


def load_admission_controller() -> tuple[Redis, RedisAdmissionController, int]:
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    limits = AdmissionLimits(
        global_tasks=int(os.environ.get("ADMISSION_GLOBAL_TASKS", "32")),
        workspace_tasks=int(os.environ.get("ADMISSION_WORKSPACE_TASKS", "4")),
        model_provider=int(os.environ.get("ADMISSION_MODEL_PROVIDER", "8")),
        capability=int(os.environ.get("ADMISSION_CAPABILITY", "16")),
        mcp=int(os.environ.get("ADMISSION_MCP", "8")),
    )
    controller = RedisAdmissionController(
        redis,
        limits,
        os.environ.get("ADMISSION_NAMESPACE", "enterprise-agent:admission"),
    )
    return redis, controller, int(os.environ.get("ADMISSION_LEASE_MS", "360000"))


@asynccontextmanager
async def admitted_call(
    controller: RedisAdmissionController,
    workspace_id: UUID,
    provider: str,
    capability_id: str,
    *,
    is_model: bool = False,
    is_mcp: bool = False,
    lease_ms: int = 360_000,
    poll_seconds: float = 0.25,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    task_id: UUID | None = None,
) -> AsyncIterator[AdmissionLease]:
    if poll_seconds <= 0 or (sessions is None) != (task_id is None):
        raise ValueError("admission call context is invalid")
    lease: AdmissionLease | None = None
    while lease is None:
        decision, lease = await controller.try_acquire_call(
            workspace_id,
            provider,
            capability_id,
            is_model=is_model,
            is_mcp=is_mcp,
            lease_ms=lease_ms,
        )
        if sessions is not None and task_id is not None:
            async with sessions.begin() as session:
                await record_schedule_decision(
                    session,
                    workspace_id,
                    task_id,
                    decision,
                )
        if lease is None:
            await asyncio.sleep(poll_seconds)
    try:
        yield lease
    finally:
        await controller.release(lease)


async def record_schedule_decision(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    decision: AdmissionDecision,
) -> None:
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    admissible_states = {
        TaskState.READY.value,
        TaskState.RUNNING.value,
        TaskState.WAITING_HITL.value,
        TaskState.PAUSED.value,
    }
    if not decision.allowed and task.state not in admissible_states:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "terminal or unplanned task cannot be admission blocked",
        )
    block_reason = None if decision.allowed else decision.block_reason
    if task.schedule_block_reason != block_reason:
        task.schedule_block_reason = block_reason
        task.version += 1
    await session.flush()
