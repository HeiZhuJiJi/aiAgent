from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.procurement.service import SupplierRow

DEMO_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000100")


@dataclass(frozen=True)
class DemoSupplier:
    supplier_id: UUID
    label: str
    expected_outcome: str
    expected_reason: str
    profile: dict[str, object]
    purchase_history: list[dict[str, object]]
    risk_flags: list[dict[str, object]]
    contracts: list[dict[str, object]]


def _uuid(workspace_id: UUID, key: str) -> UUID:
    return uuid5(workspace_id, f"enterprise-agent-demo:{key}")


def demo_suppliers(workspace_id: UUID = DEMO_WORKSPACE_ID) -> tuple[DemoSupplier, ...]:
    return (
        DemoSupplier(
            supplier_id=_uuid(workspace_id, "supplier-a"),
            label="Supplier A",
            expected_outcome="ADMIT",
            expected_reason="Active registration, clean risk record and 100% delivered orders.",
            profile={
                "supplier_id": str(_uuid(workspace_id, "supplier-a")),
                "workspace_id": str(workspace_id),
                "legal_name": "Aurora Precision Components Ltd.",
                "registration_number": "DEMO-A-2026",
                "status": "ACTIVE",
            },
            purchase_history=[
                {
                    "order_id": str(_uuid(workspace_id, "supplier-a-order-1")),
                    "amount": 125000.0,
                    "currency": "CNY",
                    "delivery_status": "DELIVERED_ON_TIME",
                },
                {
                    "order_id": str(_uuid(workspace_id, "supplier-a-order-2")),
                    "amount": 98000.0,
                    "currency": "CNY",
                    "delivery_status": "DELIVERED_ON_TIME",
                },
                {
                    "order_id": str(_uuid(workspace_id, "supplier-a-order-3")),
                    "amount": 143000.0,
                    "currency": "CNY",
                    "delivery_status": "DELIVERED_ON_TIME",
                },
            ],
            risk_flags=[],
            contracts=[
                {
                    "contract_id": str(_uuid(workspace_id, "supplier-a-contract-1")),
                    "title": "2025 Trial Supply Agreement",
                    "status": "ACTIVE",
                    "signed_at": datetime(2025, 1, 15, 8, 0, tzinfo=UTC).isoformat(),
                }
            ],
        ),
        DemoSupplier(
            supplier_id=_uuid(workspace_id, "supplier-b"),
            label="Supplier B",
            expected_outcome="REJECT",
            expected_reason="Suspended registration, critical fraud flag and repeated late delivery.",
            profile={
                "supplier_id": str(_uuid(workspace_id, "supplier-b")),
                "workspace_id": str(workspace_id),
                "legal_name": "Beacon Industrial Trading Ltd.",
                "registration_number": "DEMO-B-2026",
                "status": "SUSPENDED",
            },
            purchase_history=[
                {
                    "order_id": str(_uuid(workspace_id, "supplier-b-order-1")),
                    "amount": 89000.0,
                    "currency": "CNY",
                    "delivery_status": "LATE",
                },
                {
                    "order_id": str(_uuid(workspace_id, "supplier-b-order-2")),
                    "amount": 76000.0,
                    "currency": "CNY",
                    "delivery_status": "LATE",
                },
                {
                    "order_id": str(_uuid(workspace_id, "supplier-b-order-3")),
                    "amount": 81000.0,
                    "currency": "CNY",
                    "delivery_status": "REJECTED_QUALITY",
                },
            ],
            risk_flags=[
                {
                    "event_id": str(_uuid(workspace_id, "supplier-b-risk-1")),
                    "severity": "CRITICAL",
                    "description": "Confirmed invoice fraud investigation in the last 12 months.",
                    "source": "enterprise-risk-register:RF-2026-0042",
                }
            ],
            contracts=[
                {
                    "contract_id": str(_uuid(workspace_id, "supplier-b-contract-1")),
                    "title": "2025 Conditional Supply Agreement",
                    "status": "TERMINATED",
                    "signed_at": datetime(2025, 2, 12, 8, 0, tzinfo=UTC).isoformat(),
                }
            ],
        ),
        DemoSupplier(
            supplier_id=_uuid(workspace_id, "supplier-c"),
            label="Supplier C",
            expected_outcome="REPLAN",
            expected_reason="Only one order and no verified risk event response; evidence is insufficient.",
            profile={
                "supplier_id": str(_uuid(workspace_id, "supplier-c")),
                "workspace_id": str(workspace_id),
                "legal_name": "Cedar Emerging Materials Ltd.",
                "registration_number": "DEMO-C-2026",
                "status": "ACTIVE",
            },
            purchase_history=[
                {
                    "order_id": str(_uuid(workspace_id, "supplier-c-order-1")),
                    "amount": 22000.0,
                    "currency": "CNY",
                    "delivery_status": "DELIVERED_ON_TIME",
                }
            ],
            risk_flags=[
                {
                    "event_id": str(_uuid(workspace_id, "supplier-c-risk-1")),
                    "severity": "UNKNOWN",
                    "description": "External sanctions and litigation screening has not returned.",
                    "source": "screening-job:pending",
                }
            ],
            contracts=[],
        ),
    )


async def seed_demo_suppliers(
    session: AsyncSession, workspace_id: UUID = DEMO_WORKSPACE_ID
) -> tuple[DemoSupplier, ...]:
    fixtures = demo_suppliers(workspace_id)
    for fixture in fixtures:
        values = {
            "id": fixture.supplier_id,
            "workspace_id": workspace_id,
            "profile": fixture.profile,
            "purchase_history": fixture.purchase_history,
            "risk_flags": fixture.risk_flags,
            "contracts": fixture.contracts,
        }
        await session.execute(
            insert(SupplierRow).values(**values).on_conflict_do_nothing(
                index_elements=["id"]
            )
        )
        stored = await session.scalar(
            select(SupplierRow).where(
                SupplierRow.workspace_id == workspace_id,
                SupplierRow.id == fixture.supplier_id,
            )
        )
        if stored is None or any(
            getattr(stored, field) != values[field]
            for field in ("profile", "purchase_history", "risk_flags", "contracts")
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                f"demo supplier fixture changed: {fixture.label}",
            )
    return fixtures
