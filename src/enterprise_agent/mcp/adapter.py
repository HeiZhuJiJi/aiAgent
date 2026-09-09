import json
import os
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities.registry import CapabilityRegistry
from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    FailureCode,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.failure_injector import FailureInjector, FailurePoint
from enterprise_agent.storage.tables import MCPCapabilityRow, MCPServerRow


class MCPStdioCapability:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        workspace_id: UUID,
        server_id: UUID,
        discovery_hash: str,
        capability_id: str,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self._sessions = sessions
        self._workspace_id = workspace_id
        self._server_id = server_id
        self._discovery_hash = discovery_hash
        self._capability_id = capability_id
        self._failure_injector = failure_injector or FailureInjector()

    async def _require_current(self) -> None:
        async with self._sessions() as session:
            current = await session.scalar(
                select(MCPCapabilityRow)
                .join(
                    MCPServerRow,
                    (MCPServerRow.workspace_id == MCPCapabilityRow.workspace_id)
                    & (MCPServerRow.id == MCPCapabilityRow.server_id),
                )
                .where(
                    MCPCapabilityRow.workspace_id == self._workspace_id,
                    MCPCapabilityRow.server_id == self._server_id,
                    MCPCapabilityRow.discovery_hash == self._discovery_hash,
                    MCPCapabilityRow.tool_name == self._capability_id,
                    MCPServerRow.status == "ACTIVE",
                    MCPServerRow.provider == "procurement",
                    MCPServerRow.transport == "STDIO",
                    MCPServerRow.schema_hash == self._discovery_hash,
                )
            )
        if current is None:
            raise DomainError(
                FailureCode.CAPABILITY_UNAVAILABLE,
                "MCP capability is not current and healthy",
            )

    async def __call__(self, request: CapabilityRequest) -> CapabilityResult:
        if request.workspace_id != self._workspace_id:
            raise DomainError(FailureCode.POLICY_DENIED, "MCP workspace binding mismatch")
        if request.capability_id != self._capability_id:
            raise DomainError(FailureCode.POLICY_DENIED, "MCP capability binding mismatch")
        await self._require_current()
        if self._failure_injector.should_fail(FailurePoint.MCP_TIMEOUT):
            raise DomainError(FailureCode.CAPABILITY_TIMEOUT, "MCP call timed out")
        if self._failure_injector.should_fail(FailurePoint.MCP_DISCONNECT):
            raise DomainError(FailureCode.MCP_DISCONNECTED, "MCP transport disconnected")
        database_url = os.environ.get("PROCUREMENT_DATABASE_URL")
        if not database_url:
            raise DomainError(
                FailureCode.CAPABILITY_UNAVAILABLE,
                "procurement database is unavailable",
            )
        root = Path(__file__).resolve().parents[3]
        entrypoint = (root / "mcp_servers" / "procurement" / "server.py").resolve()
        if not entrypoint.is_file() or root not in entrypoint.parents:
            raise DomainError(
                FailureCode.CAPABILITY_UNAVAILABLE,
                "procurement MCP entrypoint unavailable",
            )
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(entrypoint)],
            env={"PROCUREMENT_DATABASE_URL": database_url},
        )
        tool_name = self._capability_id.removeprefix("procurement.")
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            response = await client.call_tool(tool_name, request.arguments)
        if response.isError:
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "MCP tool returned an error")
        output: JsonValue
        if response.structuredContent is not None:
            output = cast(JsonValue, response.structuredContent)
        else:
            text_items = [item.text for item in response.content if isinstance(item, TextContent)]
            if len(text_items) != 1:
                raise DomainError(
                    FailureCode.CAPABILITY_BAD_OUTPUT,
                    "MCP tool response has no unique structured result",
                )
            try:
                output = cast(JsonValue, json.loads(text_items[0]))
            except json.JSONDecodeError as exc:
                raise DomainError(
                    FailureCode.CAPABILITY_BAD_OUTPUT,
                    "MCP tool response is not JSON",
                ) from exc
        await self._require_current()
        return CapabilityResult(
            operation_id=request.operation_id,
            outcome="SUCCEEDED",
            output=output,
            failure=None,
        )


async def load_mcp_registry(
    sessions: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    failure_injector: FailureInjector | None = None,
) -> CapabilityRegistry:
    async with sessions() as session:
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
    registry = CapabilityRegistry()
    for row in rows:
        descriptor = CapabilityDescriptor.model_validate(row.descriptor_payload)
        registry.register(
            descriptor,
            MCPStdioCapability(
                sessions,
                workspace_id=workspace_id,
                server_id=row.server_id,
                discovery_hash=row.discovery_hash,
                capability_id=descriptor.id,
                failure_injector=failure_injector,
            ),
        )
    return registry
