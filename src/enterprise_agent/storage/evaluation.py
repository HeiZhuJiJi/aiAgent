import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.evaluation.contracts import (
    EvaluationMetrics,
    EvaluationObservation,
    EvaluationOperationalTotals,
    EvaluationRunView,
    GoldenTask,
)
from enterprise_agent.evaluation.runner import (
    aggregate_metrics,
    apply_operational_totals,
    load_golden_tasks,
)
from enterprise_agent.storage.tables import (
    AuditRow,
    EvaluationCaseRow,
    EvaluationResultRow,
    EvaluationRunRow,
    UsageRecordRow,
)


def _canonical(value: object) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], value)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _view(row: EvaluationRunRow) -> EvaluationRunView:
    return EvaluationRunView(
        run_id=row.id,
        workspace_id=row.workspace_id,
        label=row.label,
        dataset_hash=row.dataset_hash,
        state=cast(Literal["COMPLETED", "FAILED"], row.state),
        metrics=EvaluationMetrics.model_validate(row.metrics),
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def persist_evaluation_run(
    session: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    trace_id: str,
    label: str,
    observations: tuple[EvaluationObservation, ...],
    *,
    suite_path: Path = Path("evaluation/golden_tasks.json"),
    operational_totals: EvaluationOperationalTotals | None = None,
    execution_evidence_sha256: str | None = None,
) -> EvaluationRunView:
    cases = load_golden_tasks(suite_path)
    if {item.case_id for item in observations} != {item.case_id for item in cases}:
        raise ValueError("evaluation runs must explicitly observe every Golden Task")
    metrics = aggregate_metrics(cases, observations)
    if operational_totals is not None:
        metrics = apply_operational_totals(metrics, operational_totals)
    case_rows: dict[str, EvaluationCaseRow] = {}
    case_hashes: list[str] = []
    for case in cases:
        payload, digest = _canonical(case.model_dump(mode="json"))
        case_id = uuid5(NAMESPACE_URL, f"evaluation-case:{workspace_id}:{case.case_id}")
        existing = await session.scalar(
            select(EvaluationCaseRow).where(
                EvaluationCaseRow.workspace_id == workspace_id,
                EvaluationCaseRow.case_key == case.case_id,
            )
        )
        if existing is None:
            existing = EvaluationCaseRow(
                id=case_id,
                workspace_id=workspace_id,
                case_key=case.case_id,
                category=case.category,
                content_hash=digest,
                payload=payload,
            )
            session.add(existing)
            await session.flush()
        elif (
            existing.id != case_id or existing.content_hash != digest or existing.payload != payload
        ):
            raise ValueError("persisted Golden Task differs from the versioned suite")
        case_rows[case.case_id] = existing
        case_hashes.append(digest)
    dataset_hash = hashlib.sha256("".join(case_hashes).encode()).hexdigest()
    run_id = uuid4()
    now = datetime.now(UTC)
    run = EvaluationRunRow(
        id=run_id,
        workspace_id=workspace_id,
        label=label,
        dataset_hash=dataset_hash,
        state="COMPLETED",
        metrics=cast(dict[str, object], metrics.model_dump(mode="json")),
        completed_at=now,
    )
    session.add(run)
    await session.flush()
    by_key = {item.case_id: item for item in observations}
    for case in cases:
        observation = by_key[case.case_id]
        session.add(
            EvaluationResultRow(
                id=uuid5(NAMESPACE_URL, f"evaluation-result:{run_id}:{case_rows[case.case_id].id}"),
                workspace_id=workspace_id,
                run_id=run_id,
                case_id=case_rows[case.case_id].id,
                status=observation.status,
                payload=cast(dict[str, object], observation.model_dump(mode="json")),
            )
        )
    session.add(
        UsageRecordRow(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=None,
            evaluation_run_id=run_id,
            kind="EVALUATION",
            units=metrics.evaluated_cases,
            tokens=metrics.total_tokens or 0,
            cost=metrics.total_cost or 0,
            detail={
                "total_cases": metrics.total_cases,
                "evaluated_case_rate": metrics.evaluated_case_rate,
                "execution_evidence_sha256": execution_evidence_sha256,
            },
        )
    )
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action="evaluation.completed",
            resource_type="EVALUATION_RUN",
            resource_id=run_id,
            result={
                "state": "COMPLETED",
                "evaluated_cases": metrics.evaluated_cases,
                "total_cases": metrics.total_cases,
                "dataset_hash": dataset_hash,
                "execution_evidence_sha256": execution_evidence_sha256,
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _view(run)


async def list_evaluation_runs(
    session: AsyncSession, workspace_id: UUID
) -> tuple[EvaluationRunView, ...]:
    rows = (
        await session.scalars(
            select(EvaluationRunRow)
            .where(EvaluationRunRow.workspace_id == workspace_id)
            .order_by(EvaluationRunRow.created_at.desc(), EvaluationRunRow.id)
        )
    ).all()
    return tuple(_view(row) for row in rows)


def read_golden_tasks() -> tuple[GoldenTask, ...]:
    return load_golden_tasks(Path("evaluation/golden_tasks.json"))
