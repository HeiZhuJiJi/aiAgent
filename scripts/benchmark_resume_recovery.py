"""Repeated real Worker kills and lost-response reconciliation with measured recovery."""

import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.service import RPCError

from enterprise_agent.procurement.contracts import AdmissionInput
from enterprise_agent.procurement.service import (
    AdmissionRow,
    SupplierRow,
    create_admission,
    lookup_admission,
)
from enterprise_agent.runtime.workflows import DurableTaskInput, DurableTaskWorkflow
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.operations import (
    OperationAction,
    OperationIntent,
    mark_dispatched,
    prepare_operation,
    reconcile_operation,
    record_outcome,
)
from enterprise_agent.storage.tables import DomainCommandRow, TaskRow
from scripts.resume_benchmark_metrics import latency_summary
from scripts.run_resume_benchmark import (
    ROOT,
    append_json,
    emit,
    environment,
    safe_error,
    write_json,
)
from tests.failure.test_response_loss import approve_operation
from tests.integration.test_fencing import seed


async def count_commands(sessions, task_id):
    async with sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(DomainCommandRow)
            .where(DomainCommandRow.task_id == task_id)
        )


async def crash_trial(sessions, client, output, index):
    workspace_id, task_id, _ = await seed(sessions)
    queue = "resume-benchmark-" + str(uuid4())
    workflow_id = "resume-benchmark-" + str(uuid4())
    processes, logs = [], []
    handle = None
    complete = False

    def launch(label):
        env = os.environ.copy()
        env["TEMPORAL_ADDRESS"] = "127.0.0.1:57233"
        env["TEMPORAL_TASK_QUEUE"] = queue
        log = (output / f"worker-{index}-{label}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-m", "apps.worker.main"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process

    try:
        first = launch("before")
        handle = await client.start_workflow(
            DurableTaskWorkflow.run,
            DurableTaskInput(
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                step_keys=["protected-step", "after-recovery"],
                failure_hold_seconds=30,
            ),
            id=workflow_id,
            task_queue=queue,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if first.poll() is not None:
                raise AssertionError("benchmark-owned first Worker exited before commit")
            if await count_commands(sessions, task_id) == 1:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("first durable commit not observed before deadline")
        killed_at = datetime.now(UTC).isoformat()
        start = time.perf_counter()
        first.kill()
        first.wait(timeout=10)
        second = launch("after")
        result = await asyncio.wait_for(handle.result(), timeout=60)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.completed_steps == ["protected-step", "after-recovery"]
        async with sessions() as session:
            task = await session.get(TaskRow, task_id)
            rows = (
                await session.scalars(
                    select(DomainCommandRow)
                    .where(DomainCommandRow.task_id == task_id)
                    .order_by(DomainCommandRow.committed_at)
                )
            ).all()
            assert task is not None and task.state == "COMPLETED"
            assert [row.step_key for row in rows] == ["protected-step", "after-recovery"]
            assert len({row.id for row in rows}) == 2
            committed = [
                {"id": str(row.id), "step": row.step_key, "committed_at": str(row.committed_at)}
                for row in rows
            ]
        complete = True
        return {
            "success": True,
            "trial": index,
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "workflow_id": workflow_id,
            "task_queue": queue,
            "killed_pid": first.pid,
            "replacement_pid": second.pid,
            "killed_at": killed_at,
            "recovery_ms": elapsed_ms,
            "authoritative_commands": committed,
            "duplicate_commits": 0,
        }
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for log in logs:
            log.close()
        if handle is not None and not complete:
            try:
                await handle.terminate("benchmark-owned failure cleanup")
            except RPCError:
                pass


async def lost_response_trial(sessions, procurement, index):
    workspace_id, task_id, step_id = await seed(sessions)
    approval_attempt, attempt_id, supplier_id = uuid4(), uuid4(), uuid4()
    intent = OperationIntent(
        workspace_id=workspace_id,
        step_id=step_id,
        logical_key="resume-benchmark-admission",
        capability_id="procurement.create_supplier_admission_request",
        capability_version="1.0.0",
        arguments={"supplier_id": str(supplier_id), "report_sha256": "a" * 64},
    )
    async with sessions.begin() as session:
        token = (
            await acquire_attempt(
                session, workspace_id, step_id, approval_attempt, "benchmark", "benchmark"
            )
        ).fencing_token
        prepared = await prepare_operation(session, intent, approval_attempt, token)
    approval, token = await approve_operation(
        sessions, workspace_id, task_id, intent, prepared, approval_attempt, token, attempt_id
    )
    dispatch_id = uuid4()
    async with sessions.begin() as session:
        await mark_dispatched(
            session,
            workspace_id,
            prepared.operation_id,
            attempt_id,
            token,
            prepared.version,
            dispatch_id,
            approval,
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
        requested_by=uuid4(),
    )
    async with procurement.begin() as session:
        created = await create_admission(session, request)
    # The real external database committed; the platform intentionally receives no result.
    start = time.perf_counter()
    async with sessions.begin() as session:
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
        replay = await prepare_operation(session, intent, attempt_id, token)
        assert unknown.action is OperationAction.RECONCILE
        assert replay.action is OperationAction.RECONCILE
    async with procurement() as session:
        found = await lookup_admission(session, workspace_id, prepared.operation_id)
        assert found is not None
    async with sessions.begin() as session:
        recovered = await reconcile_operation(
            session,
            workspace_id,
            prepared.operation_id,
            attempt_id,
            token,
            replay.version,
            "FOUND",
            found.model_dump(mode="json"),
        )
        assert recovered.action is OperationAction.RETURN_RESULT
    elapsed_ms = (time.perf_counter() - start) * 1000
    # Reissue exactly the same external business request and inspect the independent DB.
    async with procurement.begin() as session:
        duplicate = await create_admission(session, request)
        assert duplicate.admission_id == created.admission_id
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
    return {
        "success": True,
        "trial": index,
        "workspace_id": str(workspace_id),
        "task_id": str(task_id),
        "operation_id": str(prepared.operation_id),
        "admission_id": str(created.admission_id),
        "external_rows": count,
        "reconciliation_ms": elapsed_ms,
        "duplicate_writes": count - 1,
    }


async def run(args):
    values = environment()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    engines = [
        create_async_engine(os.environ[key])
        for key in ("TEST_DATABASE_URL", "PROCUREMENT_DATABASE_URL")
    ]
    sessions, procurement = [
        async_sessionmaker(engine, expire_on_commit=False) for engine in engines
    ]
    results = {"worker_crash": [], "response_loss": []}
    client = await Client.connect("127.0.0.1:57233")
    try:
        for kind, count in [
            ("worker_crash", args.crashes),
            ("response_loss", args.response_losses),
        ]:
            for index in range(count):
                start = time.perf_counter()
                try:
                    result = await (
                        crash_trial(sessions, client, out, index)
                        if kind == "worker_crash"
                        else lost_response_trial(sessions, procurement, index)
                    )
                except Exception as exc:  # noqa: BLE001 - preserve failures rather than abort trials
                    result = {"trial": index, "success": False, "error": safe_error(exc, values)}
                result["scenario_wall_ms"] = (time.perf_counter() - start) * 1000
                results[kind].append(result)
                append_json(out / f"{kind}.jsonl", result)
                emit({"phase": kind, **result})
            key = "recovery_ms" if kind == "worker_crash" else "reconciliation_ms"
            summary = {
                "trials": count,
                "successes": sum(x["success"] for x in results[kind]),
                "successful_recovery_latency": latency_summary(
                    [x[key] for x in results[kind] if x["success"]]
                ),
                "scope": "minimal real durable workflow"
                if kind == "worker_crash"
                else "operation-level real two-database reconciliation",
            }
            write_json(out / f"{kind}_summary.json", summary)
    finally:
        for engine in engines:
            await engine.dispose()
    write_json(out / "results.json", results)
    return all(x["success"] for samples in results.values() for x in samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crashes", type=int, default=5)
    parser.add_argument("--response-losses", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.crashes <= 10 or not 1 <= args.response_losses <= 50:
        parser.error("workload outside the documented bounded range")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
