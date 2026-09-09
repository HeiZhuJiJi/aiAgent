import hashlib
import json
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Claim, Evidence, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import (
    ClaimEvidenceLinkRow,
    ClaimRow,
    EvidenceRow,
    TaskRow,
)


def _canonical(claim: Claim) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], claim.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _restore(row: ClaimRow) -> Claim:
    claim = Claim.model_validate(row.payload)
    _, digest = _canonical(claim)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "claim hash mismatch")
    return claim


async def persist_claim(session: AsyncSession, claim: Claim) -> Claim:
    task = await session.scalar(
        select(TaskRow.id).where(
            TaskRow.workspace_id == claim.workspace_id,
            TaskRow.id == claim.task_id,
        )
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "claim task unavailable")
    evidence_rows: list[EvidenceRow] = []
    if claim.evidence_ids:
        evidence_rows = list(
            (
                await session.scalars(
                    select(EvidenceRow).where(
                        EvidenceRow.workspace_id == claim.workspace_id,
                        EvidenceRow.id.in_(claim.evidence_ids),
                    )
                )
            ).all()
        )
        if {row.id for row in evidence_rows} != set(claim.evidence_ids):
            raise DomainError(FailureCode.VALIDATION_FAILED, "claim evidence unavailable")
        for row in evidence_rows:
            evidence = Evidence.model_validate(row.payload)
            if evidence.task_id != claim.task_id:
                raise DomainError(
                    FailureCode.VALIDATION_FAILED,
                    "claim evidence belongs to another task",
                )
    payload, digest = _canonical(claim)
    await session.execute(
        insert(ClaimRow)
        .values(
            id=claim.claim_id,
            workspace_id=claim.workspace_id,
            task_id=claim.task_id,
            schema_version=claim.schema_version,
            content_hash=digest,
            payload=payload,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ClaimRow).where(
            ClaimRow.workspace_id == claim.workspace_id,
            ClaimRow.id == claim.claim_id,
        )
    )
    if row is None or row.content_hash != digest:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable claim mismatch")
    for evidence_id in claim.evidence_ids:
        await session.execute(
            insert(ClaimEvidenceLinkRow)
            .values(
                workspace_id=claim.workspace_id,
                claim_id=claim.claim_id,
                evidence_id=evidence_id,
                schema_version=claim.schema_version,
            )
            .on_conflict_do_nothing()
        )
    return _restore(row)


async def load_claims(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[Claim, ...]:
    rows = (
        await session.scalars(
            select(ClaimRow)
            .where(
                ClaimRow.workspace_id == workspace_id,
                ClaimRow.task_id == task_id,
            )
            .order_by(ClaimRow.created_at, ClaimRow.id)
        )
    ).all()
    return tuple(_restore(row) for row in rows)
