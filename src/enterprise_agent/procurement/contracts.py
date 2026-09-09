from datetime import datetime
from uuid import UUID

from pydantic import Field

from enterprise_agent.contracts.domain import Contract


class SupplierProfile(Contract):
    supplier_id: UUID
    workspace_id: UUID
    legal_name: str
    registration_number: str
    status: str


class PurchaseRecord(Contract):
    order_id: UUID
    amount: float = Field(ge=0)
    currency: str
    delivery_status: str


class RiskFlag(Contract):
    event_id: UUID
    severity: str
    description: str
    source: str


class SupplierContract(Contract):
    contract_id: UUID
    title: str
    status: str
    signed_at: datetime


class AdmissionInput(Contract):
    operation_id: UUID
    workspace_id: UUID
    supplier_id: UUID
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: UUID


class Admission(Contract):
    admission_id: UUID
    operation_id: UUID
    workspace_id: UUID
    supplier_id: UUID
    report_sha256: str
    requested_by: UUID
    status: str
    created_at: datetime
