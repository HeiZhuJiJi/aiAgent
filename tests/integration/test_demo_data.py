import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import CapabilityDescriptor, RetryPolicy, Risk
from enterprise_agent.procurement.contracts import (
    PurchaseRecord,
    RiskFlag,
    SupplierContract,
    SupplierProfile,
)
from enterprise_agent.procurement.demo import demo_suppliers, seed_demo_suppliers
from enterprise_agent.procurement.service import SupplierRow
from enterprise_agent.runtime.document_parser import parse_document
from enterprise_agent.skills.package import validate_package


def skill_capability(capability_id: str) -> CapabilityDescriptor:
    external = capability_id == "procurement.create_supplier_admission_request"
    permission = (
        "supplier.write"
        if external
        else "knowledge.read"
        if capability_id in {"knowledge.hybrid_search", "web.search", "model.structured_output", "runtime.review", "artifact.docx"}
        else "supplier.read"
    )
    return CapabilityDescriptor(
        id=capability_id,
        name=capability_id,
        version="1.0.0",
        kind="MCP" if capability_id.startswith("procurement.") else "BUILTIN",
        description=capability_id,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        tags=("supplier-risk",),
        risk_level=Risk.EXTERNAL_SIDE_EFFECT if external else Risk.READ,
        side_effect_mode="EXTERNAL" if external else "NONE",
        idempotency_mode="NATIVE_IDEMPOTENCY" if external else "READ_ONLY",
        compensation_mode="MANUAL" if external else "NONE",
        compensation_capability=None,
        reconciliation_capability=(
            "procurement.get_admission_request" if external else None
        ),
        timeout_sec=30,
        retry_policy=RetryPolicy(
            max_attempts=1 if external else 3,
            initial_delay_sec=1,
            max_delay_sec=10,
        ),
        required_permissions=frozenset({permission}),
        approval_policy="REQUIRED" if external else "NONE",
        provider="test-contract-catalog",
        health_status="ACTIVE",
        cost_hint=0,
    )


async def test_three_demo_suppliers_are_real_idempotent_procurement_rows() -> None:
    engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id = uuid4()
    async with sessions.begin() as session:
        first = await seed_demo_suppliers(session, workspace_id)
    async with sessions.begin() as session:
        second = await seed_demo_suppliers(session, workspace_id)
    assert [item.expected_outcome for item in first] == ["ADMIT", "REJECT", "REPLAN"]
    assert first == second
    for fixture in first:
        SupplierProfile.model_validate(fixture.profile)
        for item in fixture.purchase_history:
            PurchaseRecord.model_validate(item)
        for item in fixture.risk_flags:
            RiskFlag.model_validate(item)
        for item in fixture.contracts:
            SupplierContract.model_validate(item)
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(SupplierRow).where(
                SupplierRow.workspace_id == workspace_id
            )
        )
    assert count == 3
    await engine.dispose()


def test_generated_demo_documents_are_hash_verified_and_parseable() -> None:
    root = Path("demo/supplier-risk/generated")
    expected = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    for name, digest in expected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest

    docx = parse_document(
        (root / "enterprise-procurement-policy.docx").read_bytes(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    pdf = parse_document(
        (root / "enterprise-procurement-policy.pdf").read_bytes(),
        "application/pdf",
    )
    xlsx = parse_document(
        (root / "supplier-evidence.xlsx").read_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert any("human approval" in part.content for part in docx)
    assert any("human approval" in part.content for part in pdf)
    spreadsheet_text = "\n".join(part.content for part in xlsx)
    assert all(item.expected_outcome in spreadsheet_text for item in demo_suppliers())

    skill_blob = (root / "supplier-risk.skill.zip").read_bytes()
    manifest = yaml.safe_load(
        Path("skills/supplier-risk/skill.yaml").read_text(encoding="utf-8")
    )
    dependency_ids = {item["capability_id"] for item in manifest["dependencies"]}
    package = validate_package(
        skill_blob,
        tuple(skill_capability(item) for item in sorted(dependency_ids)),
    )
    assert package.manifest.name == "supplier-risk"
    assert len(package.workflow.steps) == 9
    assert {case["expected_decision"] for case in package.evaluation_cases} == {
        "ADMIT",
        "REJECT",
        "NEEDS_MORE_EVIDENCE",
    }
