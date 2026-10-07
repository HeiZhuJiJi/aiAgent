import asyncio
import os
from uuid import uuid4

import httpx
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Classification
from enterprise_agent.models.gateway import Message, ModelConfig, ModelGateway
from enterprise_agent.runtime.admission import (
    AdmissionDecision,
    AdmissionLimits,
    RedisAdmissionController,
    record_schedule_decision,
)
from enterprise_agent.storage.tables import TaskRow
from tests.integration.test_fencing import seed


async def test_redis_admission_limits_all_scopes_and_reclaims_expired_leases():
    redis = Redis.from_url("redis://127.0.0.1:56379/0", decode_responses=True)
    namespace = "test:admission:" + str(uuid4())
    controller = RedisAdmissionController(
        redis,
        AdmissionLimits(
            global_tasks=2,
            workspace_tasks=1,
            model_provider=1,
            capability=1,
            mcp=1,
        ),
        namespace,
    )
    workspace_a, workspace_b, workspace_c = uuid4(), uuid4(), uuid4()
    try:
        first, first_lease = await controller.try_acquire(
            workspace_a, "local-model", "model.complete", is_model=True
        )
        assert first.allowed and first_lease is not None
        refreshed = await controller.heartbeat(first_lease)
        assert refreshed.expires_at_ms >= first_lease.expires_at_ms
        first_lease = refreshed
        same_workspace, _ = await controller.try_acquire(
            workspace_a, "other-model", "other.complete", is_model=True
        )
        assert same_workspace.block_reason == "workspace_task_concurrency"
        same_provider, _ = await controller.try_acquire(
            workspace_b, "local-model", "other.complete", is_model=True
        )
        assert same_provider.block_reason == "model_provider_concurrency"
        same_capability, _ = await controller.try_acquire(
            workspace_b, "other-model", "model.complete", is_model=True
        )
        assert same_capability.block_reason == "capability_concurrency"
        second, second_lease = await controller.try_acquire(
            workspace_b, "second-model", "other.complete", is_model=True
        )
        assert second.allowed and second_lease is not None
        global_block, _ = await controller.try_acquire(
            workspace_c, "third-model", "third.complete", is_model=True
        )
        assert global_block.block_reason == "global_task_concurrency"
        await controller.release(first_lease)
        await controller.release(second_lease)

        expiring, expiring_lease = await controller.try_acquire(
            workspace_a,
            "procurement",
            "procurement.lookup",
            is_mcp=True,
            lease_ms=100,
        )
        assert expiring.allowed and expiring_lease is not None
        mcp_block, _ = await controller.try_acquire(
            workspace_b,
            "procurement",
            "procurement.other",
            is_mcp=True,
            lease_ms=100,
        )
        assert mcp_block.block_reason == "mcp_concurrency"
        await asyncio.sleep(0.15)
        reclaimed, reclaimed_lease = await controller.try_acquire(
            workspace_a,
            "procurement",
            "procurement.lookup",
            is_mcp=True,
            lease_ms=100,
        )
        assert reclaimed.allowed and reclaimed_lease is not None
        await controller.release(reclaimed_lease)
    finally:
        keys = [key async for key in redis.scan_iter(match=namespace + ":*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_blocked_task_stays_ready_with_canonical_reason():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    async with database.begin() as session:
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.state = "READY"
    async with database.begin() as session:
        await record_schedule_decision(
            session,
            workspace_id,
            task_id,
            AdmissionDecision(
                allowed=False,
                block_reason="workspace_task_concurrency",
            ),
        )
    async with database() as session:
        task = await session.get(TaskRow, task_id)
    assert task is not None and task.state == "READY"
    assert task.schedule_block_reason == "workspace_task_concurrency"
    await engine.dispose()


async def test_direct_model_gateway_waits_for_provider_admission():
    redis = Redis.from_url("redis://127.0.0.1:56379/0", decode_responses=True)
    namespace = "test:model-admission:" + str(uuid4())
    controller = RedisAdmissionController(
        redis,
        AdmissionLimits(
            global_tasks=10,
            workspace_tasks=10,
            model_provider=1,
            capability=10,
            mcp=10,
        ),
        namespace,
    )
    workspace_id = uuid4()
    blocker_decision, blocker = await controller.try_acquire_call(
        workspace_id,
        "OpenAICompatible",
        "model.other",
        is_model=True,
        lease_ms=5_000,
    )
    assert blocker_decision.allowed and blocker is not None
    calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ready"}}],
                "model": "admission-test",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    config = ModelConfig(
        provider="OpenAICompatible",
        base_url="http://model.invalid/v1",
        model="admission-test",
        external=False,
        timeout_sec=5,
        version="1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            config,
            SecretStr(""),
            client,
            admission_workspace_id=workspace_id,
            admission_controller=controller,
        )
        pending = asyncio.create_task(
            gateway.chat(
                (Message(role="user", content="hello"),),
                Classification.INTERNAL,
            )
        )
        try:
            await asyncio.sleep(0.1)
            assert calls == 0
            await controller.release(blocker)
            reply = await asyncio.wait_for(pending, timeout=2)
            assert reply.content == "ready"
            assert calls == 1
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    keys = [key async for key in redis.scan_iter(match=namespace + ":*")]
    counts = [await redis.zcard(key) for key in keys]
    assert all(count == 0 for count in counts)
    if keys:
        await redis.delete(*keys)
    await redis.aclose()
