import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    FailureCode,
    MCPServerView,
    RetryPolicy,
    Risk,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.runtime.failure_injector import (
    FailureInjector,
    FailurePoint,
    InjectedFailure,
)
from enterprise_agent.storage.tables import AuditRow, MCPCapabilityRow, MCPServerRow

MCPStatus = Literal["REGISTERED", "ACTIVE", "UNHEALTHY", "DISABLED"]
PROCUREMENT_TOOLS = frozenset(
    {
        "get_supplier_profile",
        "get_supplier_purchase_history",
        "get_supplier_risk_flags",
        "get_supplier_contracts",
        "create_supplier_admission_request",
        "get_admission_request",
    }
)


def _server_view(row: MCPServerRow, capability_count: int) -> MCPServerView:
    return MCPServerView(
        server_id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        provider=row.provider,
        transport="STDIO",
        status=cast(MCPStatus, row.status),
        version=row.version,
        schema_hash=row.schema_hash,
        capability_count=capability_count,
        last_health_at=row.last_health_at,
        safe_error=row.safe_error,
        created_at=row.created_at,
    )


async def _capability_count(session: AsyncSession, row: MCPServerRow) -> int:
    if row.schema_hash is None:
        return 0
    return int(
        await session.scalar(
            select(func.count())
            .select_from(MCPCapabilityRow)
            .where(
                MCPCapabilityRow.workspace_id == row.workspace_id,
                MCPCapabilityRow.server_id == row.id,
                MCPCapabilityRow.discovery_hash == row.schema_hash,
            )
        )
        or 0
    )


async def register_mcp_server(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    name: str,
    provider: Literal["procurement"],
    trace_id: str,
) -> MCPServerView:
    normalized_name = name.strip()
    server_id = uuid5(NAMESPACE_URL, f"mcp-server:{workspace_id}:{normalized_name}")
    provider_row = await session.scalar(
        select(MCPServerRow).where(
            MCPServerRow.workspace_id == workspace_id,
            MCPServerRow.provider == provider,
        )
    )
    if provider_row is not None and provider_row.id != server_id:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "MCP provider is already registered in this workspace",
        )
    row = await session.scalar(
        select(MCPServerRow)
        .where(MCPServerRow.workspace_id == workspace_id, MCPServerRow.id == server_id)
        .with_for_update()
    )
    command_config = {
        "profile": provider,
        "transport": "stdio",
        "entrypoint": "mcp_servers/procurement/server.py",
        "environment_refs": ["PROCUREMENT_DATABASE_URL"],
    }
    if row is not None:
        if (
            row.name != normalized_name
            or row.provider != provider
            or row.command_config != command_config
        ):
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "MCP identity changed")
        return _server_view(row, await _capability_count(session, row))
    row = MCPServerRow(
        id=server_id,
        workspace_id=workspace_id,
        name=normalized_name,
        provider=provider,
        transport="STDIO",
        command_config=command_config,
        status="REGISTERED",
    )
    session.add(row)
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="mcp.register",
            resource_type="MCP_SERVER",
            resource_id=server_id,
            result={"provider": provider, "transport": "STDIO"},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _server_view(row, 0)


async def _discover_procurement(
    failure_injector: FailureInjector | None = None,
) -> tuple[tuple[str, str, dict[str, JsonValue], dict[str, JsonValue]], ...]:
    injector = failure_injector or FailureInjector()
    injector.raise_if_scheduled(FailurePoint.MCP_TIMEOUT)
    injector.raise_if_scheduled(FailurePoint.MCP_DISCONNECT)
    database_url = os.environ.get("PROCUREMENT_DATABASE_URL")
    if not database_url:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "procurement database is unavailable")
    root = Path(__file__).resolve().parents[3]
    entrypoint = (root / "mcp_servers" / "procurement" / "server.py").resolve()
    if not entrypoint.is_file() or root not in entrypoint.parents:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "procurement MCP entrypoint unavailable")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(entrypoint)],
        env={"PROCUREMENT_DATABASE_URL": database_url},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        tools = (await client.list_tools()).tools
    if {tool.name for tool in tools} != PROCUREMENT_TOOLS:
        raise DomainError(FailureCode.MCP_SCHEMA_CHANGED, "procurement MCP tool set changed")
    discovered = []
    for tool in tools:
        input_schema = cast(
            dict[str, JsonValue],
            json.loads(json.dumps(tool.inputSchema, allow_nan=False)),
        )
        raw_output = tool.outputSchema or {"type": "object"}
        output_schema = cast(
            dict[str, JsonValue],
            json.loads(json.dumps(raw_output, allow_nan=False)),
        )
        discovered.append((tool.name, tool.description or tool.name, input_schema, output_schema))
    return tuple(sorted(discovered))


def _descriptor(
    server: MCPServerRow,
    tool_name: str,
    description: str,
    input_schema: dict[str, JsonValue],
    output_schema: dict[str, JsonValue],
) -> CapabilityDescriptor:
    capability_id = f"procurement.{tool_name}"
    external_effect = tool_name == "create_supplier_admission_request"
    return CapabilityDescriptor(
        id=capability_id,
        name=tool_name.replace("_", " ").title(),
        version="1.0.0",
        kind="MCP",
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        tags=("procurement", "mcp"),
        risk_level=Risk.EXTERNAL_SIDE_EFFECT if external_effect else Risk.READ,
        side_effect_mode="EXTERNAL" if external_effect else "NONE",
        idempotency_mode="NATIVE_IDEMPOTENCY" if external_effect else "READ_ONLY",
        compensation_mode="MANUAL" if external_effect else "NONE",
        compensation_capability=None,
        reconciliation_capability=(
            "procurement.get_admission_request" if external_effect else None
        ),
        timeout_sec=30,
        retry_policy=RetryPolicy(
            max_attempts=1 if external_effect else 3,
            initial_delay_sec=1,
            max_delay_sec=10,
        ),
        required_permissions=frozenset(
            {"supplier.write"} if external_effect else {"supplier.read"}
        ),
        approval_policy="REQUIRED" if external_effect else "NONE",
        provider=str(server.id),
        health_status="ACTIVE",
        cost_hint=0,
    )


async def sync_mcp_server(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    server_id: UUID,
    expected_version: int,
    trace_id: str,
    failure_injector: FailureInjector | None = None,
) -> MCPServerView:
    row = await session.scalar(
        select(MCPServerRow)
        .where(MCPServerRow.workspace_id == workspace_id, MCPServerRow.id == server_id)
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "MCP server unavailable")
    if row.version != expected_version:
        raise DomainError(FailureCode.STALE_ATTEMPT, "MCP server version changed")
    if row.status == "DISABLED":
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "disabled MCP cannot connect")
    try:
        discovered = await _discover_procurement(failure_injector)
        descriptors = tuple(
            _descriptor(row, tool_name, description, input_schema, output_schema)
            for tool_name, description, input_schema, output_schema in discovered
        )
        canonical = json.dumps(
            [descriptor.model_dump(mode="json") for descriptor in descriptors],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        discovery_hash = hashlib.sha256(canonical).hexdigest()
        if row.schema_hash is not None and row.schema_hash != discovery_hash:
            row.status = "UNHEALTHY"
            row.version += 1
            row.safe_error = FailureCode.MCP_SCHEMA_CHANGED.value
            await session.flush()
            return _server_view(row, await _capability_count(session, row))
        for descriptor in descriptors:
            capability_id = uuid5(
                NAMESPACE_URL,
                f"mcp-capability:{workspace_id}:{server_id}:{discovery_hash}:{descriptor.id}",
            )
            existing = await session.scalar(
                select(MCPCapabilityRow).where(
                    MCPCapabilityRow.workspace_id == workspace_id,
                    MCPCapabilityRow.id == capability_id,
                )
            )
            payload = descriptor.model_dump(mode="json")
            if existing is None:
                session.add(
                    MCPCapabilityRow(
                        id=capability_id,
                        workspace_id=workspace_id,
                        server_id=server_id,
                        discovery_hash=discovery_hash,
                        tool_name=descriptor.id,
                        descriptor_payload=payload,
                    )
                )
            elif existing.descriptor_payload != payload:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION, "MCP capability identity changed"
                )
        now = await session.scalar(select(func.clock_timestamp()))
        if now is None:
            raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
        await session.flush()
        row.schema_hash = discovery_hash
        row.status = "ACTIVE"
        row.version += 1
        row.last_health_at = now
        row.safe_error = None
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=identity.user_id,
                action="mcp.schema.synced",
                resource_type="MCP_SERVER",
                resource_id=server_id,
                result={
                    "schema_hash": discovery_hash,
                    "capability_count": len(descriptors),
                },
                trace_id=trace_id,
            )
        )
        await session.flush()
        return _server_view(row, len(descriptors))
    except DomainError:
        raise
    except InjectedFailure as exc:
        row.status = "UNHEALTHY"
        row.version += 1
        row.safe_error = exc.point.value
        await session.flush()
        return _server_view(row, await _capability_count(session, row))
    except Exception as exc:  # noqa: BLE001 - MCP subprocess is an external boundary
        row.status = "UNHEALTHY"
        row.version += 1
        row.safe_error = type(exc).__name__[:500]
        await session.flush()
        return _server_view(row, await _capability_count(session, row))


async def disable_mcp_server(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    server_id: UUID,
    expected_version: int,
    trace_id: str,
) -> MCPServerView:
    row = await session.scalar(
        select(MCPServerRow)
        .where(MCPServerRow.workspace_id == workspace_id, MCPServerRow.id == server_id)
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "MCP server unavailable")
    if row.version != expected_version:
        raise DomainError(FailureCode.STALE_ATTEMPT, "MCP server version changed")
    if row.status == "DISABLED":
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "MCP server already disabled")
    row.status = "DISABLED"
    row.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="mcp.disable",
            resource_type="MCP_SERVER",
            resource_id=server_id,
            result={"version": row.version},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _server_view(row, await _capability_count(session, row))


async def list_mcp_servers(
    session: AsyncSession, workspace_id: UUID
) -> tuple[MCPServerView, ...]:
    rows = (
        await session.scalars(
            select(MCPServerRow)
            .where(MCPServerRow.workspace_id == workspace_id)
            .order_by(MCPServerRow.name, MCPServerRow.id)
        )
    ).all()
    views: list[MCPServerView] = []
    for row in rows:
        views.append(_server_view(row, await _capability_count(session, row)))
    return tuple(views)


async def list_mcp_capabilities(
    session: AsyncSession, workspace_id: UUID
) -> tuple[CapabilityDescriptor, ...]:
    rows = (
        await session.scalars(
            select(MCPCapabilityRow)
            .join(
                MCPServerRow,
                (MCPServerRow.workspace_id == MCPCapabilityRow.workspace_id)
                & (MCPServerRow.id == MCPCapabilityRow.server_id),
            )
            .where(
                MCPCapabilityRow.workspace_id == workspace_id,
                MCPServerRow.status == "ACTIVE",
                MCPServerRow.schema_hash == MCPCapabilityRow.discovery_hash,
            )
            .order_by(MCPCapabilityRow.tool_name, MCPCapabilityRow.id)
        )
    ).all()
    return tuple(CapabilityDescriptor.model_validate(row.descriptor_payload) for row in rows)
