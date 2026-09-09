import json
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Classification, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.mcp.manager import register_mcp_server, sync_mcp_server
from enterprise_agent.models.gateway import Message, ModelConfig, ModelGateway
from enterprise_agent.runtime.failure_injector import (
    FailureDirective,
    FailureInjector,
    FailurePoint,
)
from enterprise_agent.storage.tables import Workspace


class Answer(BaseModel):
    answer: str


class ModelProtocolHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(
            {
                "model": "failure-test-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _gateway(server: ThreadingHTTPServer, injector: FailureInjector) -> ModelGateway:
    return ModelGateway(
        ModelConfig(
            provider="OpenAICompatible",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model="failure-test-model",
            external=False,
            timeout_sec=2,
            version="failure-test-v1",
        ),
        SecretStr(""),
        httpx.AsyncClient(trust_env=False),
        injector,
    )


@pytest.fixture
def model_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelProtocolHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    ModelProtocolHandler.calls = 0
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("point", (FailurePoint.LLM_TIMEOUT, FailurePoint.LLM_429))
async def test_model_transport_failure_is_injected_once_then_retried(
    model_server: ThreadingHTTPServer, point: FailurePoint
) -> None:
    injector = FailureInjector((FailureDirective(point=point, occurrences=(1,)),))
    gateway = _gateway(model_server, injector)
    try:
        reply = await gateway.chat(
            (Message(role="user", content="return a valid answer"),),
            Classification.INTERNAL,
        )
    finally:
        await gateway._client.aclose()
    assert reply.content == '{"answer":"ok"}'
    assert ModelProtocolHandler.calls == 1
    assert injector.calls(point) == 2


async def test_llm_schema_corruption_exhausts_two_repairs_with_schema_error(
    model_server: ThreadingHTTPServer,
) -> None:
    injector = FailureInjector(
        (
            FailureDirective(
                point=FailurePoint.LLM_SCHEMA_CORRUPTION,
                occurrences=(1, 2, 3),
            ),
        )
    )
    gateway = _gateway(model_server, injector)
    try:
        with pytest.raises(DomainError) as raised:
            await gateway.structured(
                (Message(role="user", content="return a valid answer"),),
                Answer,
                Classification.INTERNAL,
            )
    finally:
        await gateway._client.aclose()
    assert raised.value.code is FailureCode.LLM_SCHEMA_ERROR
    assert ModelProtocolHandler.calls == 3
    assert injector.calls(FailurePoint.LLM_SCHEMA_CORRUPTION) == 3


async def test_mcp_disconnect_health_detects_and_reconnects_real_stdio_server() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, user_id, session_id = uuid4(), uuid4(), uuid4()
    identity = Identity(
        user_id=user_id,
        email="failure-mcp@example.test",
        session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    try:
        async with sessions.begin() as session:
            session.add(Workspace(id=workspace_id, name="MCP failure recovery"))
        async with sessions.begin() as session:
            registered = await register_mcp_server(
                session,
                identity=identity,
                workspace_id=workspace_id,
                name="Procurement",
                provider="procurement",
                trace_id="mcp-failure-register",
            )
        async with sessions.begin() as session:
            active = await sync_mcp_server(
                session,
                identity=identity,
                workspace_id=workspace_id,
                server_id=UUID(str(registered.server_id)),
                expected_version=1,
                trace_id="mcp-initial-connect",
            )
        assert active.status == "ACTIVE" and active.capability_count == 6

        injector = FailureInjector(
            (FailureDirective(point=FailurePoint.MCP_DISCONNECT, occurrences=(1,)),)
        )
        async with sessions.begin() as session:
            unhealthy = await sync_mcp_server(
                session,
                identity=identity,
                workspace_id=workspace_id,
                server_id=active.server_id,
                expected_version=active.version,
                trace_id="mcp-disconnect-health",
                failure_injector=injector,
            )
        assert unhealthy.status == "UNHEALTHY"
        assert unhealthy.safe_error == FailurePoint.MCP_DISCONNECT.value

        async with sessions.begin() as session:
            recovered = await sync_mcp_server(
                session,
                identity=identity,
                workspace_id=workspace_id,
                server_id=active.server_id,
                expected_version=unhealthy.version,
                trace_id="mcp-reconnect-health",
                failure_injector=injector,
            )
        assert recovered.status == "ACTIVE"
        assert recovered.capability_count == 6
        assert recovered.schema_hash == active.schema_hash
        assert recovered.safe_error is None
        assert injector.calls(FailurePoint.MCP_DISCONNECT) == 2
    finally:
        await engine.dispose()


def test_all_required_failure_points_have_deterministic_schedules() -> None:
    directives = tuple(
        FailureDirective(point=point, occurrences=(2,)) for point in FailurePoint
    )
    injector = FailureInjector(directives)
    for point in FailurePoint:
        assert injector.should_fail(point) is False
        assert injector.should_fail(point) is True
        assert injector.should_fail(point) is False

