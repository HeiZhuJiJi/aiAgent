import asyncio
import os
import sys
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.procurement.contracts import AdmissionInput
from enterprise_agent.procurement.service import (
    AdmissionRow,
    SupplierRow,
    create_admission,
    lookup_admission,
)


async def test_concurrent_native_idempotency_and_parameter_binding():
    engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    w, s = uuid4(), uuid4()
    async with sessions.begin() as session:
        session.add(
            SupplierRow(
                id=s, workspace_id=w, profile={}, purchase_history=[], risk_flags=[], contracts=[]
            )
        )
    request = AdmissionInput(
        operation_id=uuid4(),
        workspace_id=w,
        supplier_id=s,
        report_sha256="a" * 64,
        requested_by=uuid4(),
    )

    async def submit():
        async with sessions.begin() as session:
            return await create_admission(session, request)

    results = await asyncio.gather(*(submit() for _ in range(8)))
    assert len({r.admission_id for r in results}) == 1
    async with sessions() as session:
        reconciled = await lookup_admission(session, w, request.operation_id)
        assert reconciled.admission_id == results[0].admission_id
        assert await lookup_admission(session, uuid4(), request.operation_id) is None
    import pytest

    from enterprise_agent.domain.states import DomainError

    with pytest.raises(DomainError, match="parameter mismatch"):
        async with sessions.begin() as session:
            await create_admission(session, request.model_copy(update={"report_sha256": "b" * 64}))
    await engine.dispose()


async def test_real_mcp_process_discovery_create_lookup():
    engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    w, s = uuid4(), uuid4()
    async with sessions.begin() as session:
        session.add(
            SupplierRow(
                id=s,
                workspace_id=w,
                profile={
                    "supplier_id": str(s),
                    "workspace_id": str(w),
                    "legal_name": "Supplier A",
                    "registration_number": "TEST-A",
                    "status": "ACTIVE",
                },
                purchase_history=[],
                risk_flags=[],
                contracts=[],
            )
        )
    params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_servers/procurement/server.py"],
        env={"PROCUREMENT_DATABASE_URL": os.environ["PROCUREMENT_DATABASE_URL"]},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {
            "get_supplier_profile",
            "get_supplier_purchase_history",
            "get_supplier_risk_flags",
            "get_supplier_contracts",
            "create_supplier_admission_request",
            "get_admission_request",
        }
        request = AdmissionInput(
            operation_id=uuid4(),
            workspace_id=w,
            supplier_id=s,
            report_sha256="a" * 64,
            requested_by=uuid4(),
        )
        result = await client.call_tool(
            "create_supplier_admission_request", {"request": request.model_dump(mode="json")}
        )
        assert not result.isError
        lookup = await client.call_tool(
            "get_admission_request",
            {"workspace_id": str(w), "operation_id": str(request.operation_id)},
        )
        assert not lookup.isError
        replay = await client.call_tool(
            "create_supplier_admission_request", {"request": request.model_dump(mode="json")}
        )
        assert not replay.isError
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AdmissionRow).where(AdmissionRow.workspace_id == w)
            )
            == 1
        )
    await engine.dispose()
