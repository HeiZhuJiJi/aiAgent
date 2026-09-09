import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.procurement.contracts import Admission, AdmissionInput


class ProcurementBase(DeclarativeBase):
    """Separate procurement metadata and database."""


class SupplierRow(ProcurementBase):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("workspace_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB)
    purchase_history: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    risk_flags: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    contracts: Mapped[list[dict[str, object]]] = mapped_column(JSONB)


class AdmissionRow(ProcurementBase):
    __tablename__ = "admissions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "operation_id"),
        ForeignKeyConstraint(
            ["workspace_id", "supplier_id"], ["suppliers.workspace_id", "suppliers.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID]
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    supplier_id: Mapped[UUID]
    parameter_hash: Mapped[str]
    report_sha256: Mapped[str]
    requested_by: Mapped[UUID]
    status: Mapped[str] = mapped_column(default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def as_admission(row: AdmissionRow) -> Admission:
    return Admission(
        admission_id=row.id,
        operation_id=row.operation_id,
        workspace_id=row.workspace_id,
        supplier_id=row.supplier_id,
        report_sha256=row.report_sha256,
        requested_by=row.requested_by,
        status=row.status,
        created_at=row.created_at,
    )


async def create_admission(session: AsyncSession, request: AdmissionInput) -> Admission:
    digest = hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    await session.execute(
        insert(AdmissionRow)
        .values(
            id=uuid4(),
            operation_id=request.operation_id,
            workspace_id=request.workspace_id,
            supplier_id=request.supplier_id,
            parameter_hash=digest,
            report_sha256=request.report_sha256,
            requested_by=request.requested_by,
            status="PENDING",
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "operation_id"])
    )
    row = await session.scalar(
        select(AdmissionRow).where(
            AdmissionRow.workspace_id == request.workspace_id,
            AdmissionRow.operation_id == request.operation_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "operation disappeared")
    if row.parameter_hash != digest:
        raise DomainError(FailureCode.VALIDATION_FAILED, "idempotency parameter mismatch")
    return as_admission(row)


async def lookup_admission(
    session: AsyncSession, workspace_id: UUID, operation_id: UUID
) -> Admission | None:
    row = await session.scalar(
        select(AdmissionRow).where(
            AdmissionRow.workspace_id == workspace_id, AdmissionRow.operation_id == operation_id
        )
    )
    return None if row is None else as_admission(row)
