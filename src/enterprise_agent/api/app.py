import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from minio import Minio
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response, StreamingResponse

from enterprise_agent.api.contracts import (
    ApprovalDecisionInput,
    ApprovalView,
    AuditView,
    ChatStreamInput,
    ConversationCreate,
    ConversationMessageCreate,
    MCPLifecycleInput,
    MCPRegisterInput,
    ModelConfigCreate,
    ModelConfigView,
    ModelProbeView,
    SkillLifecycleInput,
    TaskModifyInput,
    TaskTraceView,
    TaskView,
    UsageView,
    VersionPrecondition,
)
from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    CapabilityDescriptor,
    Classification,
    ConsistencyIncidentView,
    Conversation,
    ConversationMessage,
    Evidence,
    FailureCode,
    KnowledgeIngestionView,
    MCPServerView,
    Plan,
    SkillInstallationView,
    StepAttempt,
    TaskGoal,
    TaskStep,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.evaluation.contracts import (
    EvaluationRunCreate,
    EvaluationRunView,
    GoldenTask,
)
from enterprise_agent.governance.policy import ROLE_PERMISSIONS, Role
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.identity.contracts import (
    Identity,
    LoginInput,
    RegisterInput,
    RetentionPolicyUpdate,
    RetentionPolicyView,
    SessionView,
    UserView,
    WorkspaceCreate,
    WorkspaceDeletionRequest,
    WorkspaceDeletionView,
    WorkspaceView,
)
from enterprise_agent.identity.service import (
    authenticate,
    create_workspace,
    get_workspace,
    issue_session,
    list_workspaces,
    register_user,
    revoke_session,
)
from enterprise_agent.mcp.manager import (
    disable_mcp_server,
    list_mcp_capabilities,
    list_mcp_servers,
    register_mcp_server,
    sync_mcp_server,
)
from enterprise_agent.models.gateway import Message, ModelGateway
from enterprise_agent.models.reranker import RerankerGateway
from enterprise_agent.observability.metrics import (
    APPROVAL_TOTAL,
    HTTP_REQUEST_DURATION,
    HTTP_REQUEST_TOTAL,
    POLICY_DENIED_TOTAL,
    TASK_TOTAL,
    render_metrics,
)
from enterprise_agent.observability.tracing import current_trace_id, tracer
from enterprise_agent.operations.lifecycle import (
    get_retention_policy,
    get_workspace_deletion,
    request_workspace_deletion,
    update_retention_policy,
)
from enterprise_agent.runtime.document_parser import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_DOCUMENT_MEDIA_TYPES,
)
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.runtime.reconciler import list_consistency_incidents
from enterprise_agent.storage.approval_queries import decide_as_user, list_approvals
from enterprise_agent.storage.artifacts import ArtifactStore
from enterprise_agent.storage.attempts import list_task_attempts, list_task_evidence
from enterprise_agent.storage.audit import list_audit_events
from enterprise_agent.storage.evaluation import (
    list_evaluation_runs,
    persist_evaluation_run,
    read_golden_tasks,
)
from enterprise_agent.storage.knowledge_jobs import (
    get_knowledge_ingestion,
    list_knowledge_ingestions,
    request_knowledge_ingestion,
)
from enterprise_agent.storage.memory import (
    append_conversation_message,
    create_conversation,
    list_conversations,
    load_conversation_messages,
)
from enterprise_agent.storage.model_configs import (
    create_model_config,
    list_model_configs,
    load_model_gateway_config,
)
from enterprise_agent.storage.plans import load_plan_versions
from enterprise_agent.storage.replans import apply_replan
from enterprise_agent.storage.skills import (
    MAX_SKILL_ARCHIVE_BYTES,
    SkillArchiveStore,
    list_skill_installations,
    quarantine_skill,
    transition_skill_installation,
)
from enterprise_agent.storage.steps import list_task_steps
from enterprise_agent.storage.task_controls import control_task
from enterprise_agent.storage.tasks import create_task, get_task, list_tasks
from enterprise_agent.storage.traces import load_task_trace
from enterprise_agent.storage.usage import list_usage_records


class ErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


def create_app(
    database_url: str | None = None,
    artifact_store: ArtifactStore | None = None,
    knowledge_ingestor: KnowledgeIngestor | None = None,
    skill_archive_store: SkillArchiveStore | None = None,
    skill_capabilities: tuple[CapabilityDescriptor, ...] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        url = database_url or os.environ["DATABASE_URL"]
        engine = create_async_engine(url)
        application.state.engine = engine
        application.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
        application.state.artifact_store = artifact_store
        application.state.knowledge_ingestor = knowledge_ingestor
        application.state.skill_archive_store = skill_archive_store
        application.state.skill_capabilities = (
            builtin_descriptors()
            if skill_capabilities is None
            else skill_capabilities
        )
        if application.state.artifact_store is None and os.environ.get("MINIO_SECRET_KEY"):
            application.state.artifact_store = ArtifactStore(
                Minio(
                    os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
                    access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
                    secret_key=os.environ["MINIO_SECRET_KEY"],
                    secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
                ),
                bucket=os.environ.get("ARTIFACT_BUCKET", "enterprise-artifacts"),
            )
        if application.state.knowledge_ingestor is None and os.environ.get("MINIO_SECRET_KEY"):
            application.state.knowledge_ingestor = KnowledgeIngestor(
                Minio(
                    os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
                    access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
                    secret_key=os.environ["MINIO_SECRET_KEY"],
                    secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
                ),
                bucket=os.environ.get("KNOWLEDGE_BUCKET", "enterprise-knowledge"),
            )
        if application.state.skill_archive_store is None and os.environ.get("MINIO_SECRET_KEY"):
            application.state.skill_archive_store = SkillArchiveStore(
                Minio(
                    os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
                    access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
                    secret_key=os.environ["MINIO_SECRET_KEY"],
                    secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
                ),
                bucket=os.environ.get(
                    "SKILL_QUARANTINE_BUCKET", "enterprise-skill-quarantine"
                ),
            )
        yield
        await engine.dispose()

    app = FastAPI(
        title="Enterprise Agent Work Platform",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            403: {"model": ErrorEnvelope, "description": "Policy or identity denied"},
            409: {"model": ErrorEnvelope, "description": "Domain consistency conflict"},
            422: {"model": ErrorEnvelope, "description": "Contract validation failed"},
        },
    )

    @app.middleware("http")
    async def trace_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        route = request.url.path
        with tracer.start_as_current_span("http.request") as span:
            request.state.trace_id = current_trace_id() or uuid4().hex
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("url.path", route)
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            response.headers["x-trace-id"] = request.state.trace_id
        HTTP_REQUEST_TOTAL.labels(request.method, route, str(response.status_code)).inc()
        HTTP_REQUEST_DURATION.labels(request.method, route).observe(
            time.perf_counter() - started
        )
        return response

    def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
        payload = ErrorEnvelope(
            error=ErrorDetail(code=code, message=message, trace_id=request.state.trace_id)
        )
        return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status_code = 403 if exc.code is FailureCode.POLICY_DENIED else 409
        if exc.code is FailureCode.POLICY_DENIED:
            POLICY_DENIED_TOTAL.labels("api").inc()
        return error_response(request, status_code, exc.code.value, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, FailureCode.VALIDATION_FAILED.value, str(exc))

    async def database(request: Request) -> AsyncIterator[AsyncSession]:
        async with request.app.state.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    DatabaseSession = Annotated[AsyncSession, Depends(database)]

    async def current_identity(
        request: Request,
        session: DatabaseSession,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Identity:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise DomainError(FailureCode.POLICY_DENIED, "bearer session required")
        return await authenticate(session, token)

    CurrentIdentity = Annotated[Identity, Depends(current_identity)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(
            content=render_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/auth/register", response_model=UserView, status_code=201)
    async def register(value: RegisterInput, session: DatabaseSession) -> UserView:
        return await register_user(session, value)

    @app.post("/auth/login", response_model=SessionView)
    async def login(value: LoginInput, session: DatabaseSession) -> SessionView:
        return await issue_session(session, value)

    @app.get("/auth/me", response_model=Identity)
    async def me(identity: CurrentIdentity) -> Identity:
        return identity

    @app.post("/auth/logout", status_code=204)
    async def logout(
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> None:
        await revoke_session(session, identity)

    @app.post("/workspaces", response_model=WorkspaceView, status_code=201)
    async def workspace_create(
        value: WorkspaceCreate,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> WorkspaceView:
        return await create_workspace(session, identity, value, request.state.trace_id)

    @app.get("/workspaces", response_model=list[WorkspaceView])
    async def workspace_list(
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[WorkspaceView, ...]:
        return await list_workspaces(session, identity)

    @app.get("/workspaces/{workspace_id}", response_model=WorkspaceView)
    async def workspace_get(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> WorkspaceView:
        return await get_workspace(session, identity, workspace_id)

    @app.get(
        "/workspaces/{workspace_id}/retention",
        response_model=RetentionPolicyView,
    )
    async def workspace_retention_get(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> RetentionPolicyView:
        return await get_retention_policy(session, identity, workspace_id)

    @app.put(
        "/workspaces/{workspace_id}/retention",
        response_model=RetentionPolicyView,
    )
    async def workspace_retention_update(
        workspace_id: UUID,
        value: RetentionPolicyUpdate,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> RetentionPolicyView:
        return await update_retention_policy(
            session, identity, workspace_id, value, request.state.trace_id
        )

    @app.delete(
        "/workspaces/{workspace_id}",
        response_model=WorkspaceDeletionView,
        status_code=202,
    )
    async def workspace_delete(
        workspace_id: UUID,
        value: WorkspaceDeletionRequest,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> WorkspaceDeletionView:
        return await request_workspace_deletion(
            session, identity, workspace_id, value, request.state.trace_id
        )

    @app.get(
        "/workspace-deletions/{request_id}",
        response_model=WorkspaceDeletionView,
    )
    async def workspace_deletion_get(
        request_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> WorkspaceDeletionView:
        return await get_workspace_deletion(session, identity, request_id)

    @app.post("/conversations", response_model=Conversation, status_code=201)
    async def conversation_create(
        value: ConversationCreate,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> Conversation:
        await get_workspace(session, identity, value.workspace_id)
        return await create_conversation(
            session,
            workspace_id=value.workspace_id,
            conversation_id=value.conversation_id,
            title=value.title,
        )

    @app.get(
        "/workspaces/{workspace_id}/conversations",
        response_model=list[Conversation],
    )
    async def conversation_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[Conversation, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_conversations(session, workspace_id)

    @app.post(
        "/conversations/{conversation_id}/messages",
        response_model=ConversationMessage,
        status_code=201,
    )
    async def conversation_message_create(
        conversation_id: UUID,
        value: ConversationMessageCreate,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ConversationMessage:
        await get_workspace(session, identity, value.workspace_id)
        return await append_conversation_message(
            session,
            workspace_id=value.workspace_id,
            conversation_id=conversation_id,
            message_id=value.message_id,
            role="USER",
            content=value.content,
        )

    @app.get(
        "/conversations/{conversation_id}/messages",
        response_model=list[ConversationMessage],
    )
    async def conversation_message_list(
        conversation_id: UUID,
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[ConversationMessage, ...]:
        await get_workspace(session, identity, workspace_id)
        return await load_conversation_messages(session, workspace_id, conversation_id)

    @app.post("/chat/stream")
    async def chat_stream(
        value: ChatStreamInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> StreamingResponse:
        await get_workspace(session, identity, value.workspace_id)
        master_key = os.environ.get("SECRET_MASTER_KEY")
        if not master_key:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "secret master key is not configured",
            )
        config, key, row = await load_model_gateway_config(
            session,
            value.workspace_id,
            value.model_config_id,
            SecretCipher(master_key),
        )
        if row.kind != "CHAT":
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "chat streaming requires a CHAT model configuration",
            )
        prior = await load_conversation_messages(
            session, value.workspace_id, value.conversation_id
        )
        user_message = await append_conversation_message(
            session,
            workspace_id=value.workspace_id,
            conversation_id=value.conversation_id,
            message_id=value.user_message_id,
            role="USER",
            content=value.content,
        )
        # Persist the user's message before releasing a streaming response.
        # The assistant reply commits in its own transaction only after [DONE].
        await session.commit()
        prompt = tuple(
            Message(
                role=(
                    "assistant"
                    if item.role == "ASSISTANT"
                    else "system"
                    if item.role == "SYSTEM"
                    else "user"
                ),
                content=item.content,
            )
            for item in (*prior, user_message)
        )

        async def events() -> AsyncIterator[str]:
            chunks: list[str] = []
            try:
                async with httpx.AsyncClient() as client:
                    async for chunk in ModelGateway(config, key, client).stream(
                        prompt, Classification.INTERNAL
                    ):
                        chunks.append(chunk)
                        yield (
                            "event: token\n"
                            "data: "
                            + json.dumps({"content": chunk}, ensure_ascii=False)
                            + "\n\n"
                        )
                content = "".join(chunks)
                if not content:
                    raise DomainError(
                        FailureCode.LLM_SCHEMA_ERROR,
                        "model stream produced no content",
                    )
                async with request.app.state.sessions.begin() as stream_session:
                    await append_conversation_message(
                        stream_session,
                        workspace_id=value.workspace_id,
                        conversation_id=value.conversation_id,
                        message_id=value.assistant_message_id,
                        role="ASSISTANT",
                        content=content,
                    )
                yield (
                    "event: done\n"
                    "data: "
                    + json.dumps(
                        {
                            "assistant_message_id": str(value.assistant_message_id),
                            "model": config.model,
                        }
                    )
                    + "\n\n"
                )
            except DomainError as exc:
                yield (
                    "event: error\n"
                    "data: "
                    + json.dumps({"code": exc.code.value, "message": exc.detail})
                    + "\n\n"
                )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/tasks", response_model=TaskView, status_code=201)
    async def task_create(
        goal: TaskGoal,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        capabilities = tuple(request.app.state.skill_capabilities) + await list_mcp_capabilities(
            session, goal.workspace_id
        )
        result = await create_task(
            session,
            identity,
            goal,
            request.state.trace_id,
            capabilities,
        )
        TASK_TOTAL.inc()
        return result

    @app.get("/workspaces/{workspace_id}/tasks", response_model=list[TaskView])
    async def task_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[TaskView, ...]:
        return await list_tasks(session, identity, workspace_id)

    @app.get("/workspaces/{workspace_id}/tasks/{task_id}", response_model=TaskView)
    async def task_get(
        workspace_id: UUID,
        task_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await get_task(session, identity, workspace_id, task_id)

    @app.get("/tasks/{task_id}/trace", response_model=TaskTraceView)
    async def task_trace(
        task_id: UUID,
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskTraceView:
        await get_task(session, identity, workspace_id, task_id)
        return await load_task_trace(session, workspace_id, task_id)

    @app.get("/workspaces/{workspace_id}/tasks/{task_id}/plans", response_model=list[Plan])
    async def task_plan_list(
        workspace_id: UUID,
        task_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[Plan, ...]:
        await get_task(session, identity, workspace_id, task_id)
        return await load_plan_versions(session, workspace_id, task_id)

    @app.get(
        "/workspaces/{workspace_id}/tasks/{task_id}/steps",
        response_model=list[TaskStep],
    )
    async def task_step_list(
        workspace_id: UUID,
        task_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[TaskStep, ...]:
        await get_task(session, identity, workspace_id, task_id)
        return await list_task_steps(session, workspace_id, task_id)

    @app.get(
        "/workspaces/{workspace_id}/tasks/{task_id}/attempts",
        response_model=list[StepAttempt],
    )
    async def task_attempt_list(
        workspace_id: UUID,
        task_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[StepAttempt, ...]:
        await get_task(session, identity, workspace_id, task_id)
        return await list_task_attempts(session, workspace_id, task_id)

    @app.get(
        "/workspaces/{workspace_id}/tasks/{task_id}/evidence",
        response_model=list[Evidence],
    )
    async def task_evidence_list(
        workspace_id: UUID,
        task_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[Evidence, ...]:
        await get_task(session, identity, workspace_id, task_id)
        return await list_task_evidence(session, workspace_id, task_id)

    def configured_artifact_store(request: Request) -> ArtifactStore:
        store: ArtifactStore | None = request.app.state.artifact_store
        if store is None:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "artifact store is not configured")
        return store

    def configured_knowledge_ingestor(request: Request) -> KnowledgeIngestor:
        ingestor: KnowledgeIngestor | None = request.app.state.knowledge_ingestor
        if ingestor is None:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION, "knowledge object store is not configured"
            )
        return ingestor

    def configured_skill_store(request: Request) -> SkillArchiveStore:
        store: SkillArchiveStore | None = request.app.state.skill_archive_store
        if store is None:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION, "Skill quarantine store is not configured"
            )
        return store

    async def require_skill_admin(
        session: AsyncSession, identity: Identity, workspace_id: UUID
    ) -> WorkspaceView:
        workspace = await get_workspace(session, identity, workspace_id)
        if "skill.install" not in ROLE_PERMISSIONS[workspace.role]:
            raise DomainError(FailureCode.POLICY_DENIED, "Skill install permission required")
        return workspace

    async def require_mcp_admin(
        session: AsyncSession, identity: Identity, workspace_id: UUID
    ) -> WorkspaceView:
        workspace = await get_workspace(session, identity, workspace_id)
        if "mcp.manage" not in ROLE_PERMISSIONS[workspace.role]:
            raise DomainError(FailureCode.POLICY_DENIED, "MCP manage permission required")
        return workspace

    async def require_model_admin(
        session: AsyncSession, identity: Identity, workspace_id: UUID
    ) -> WorkspaceView:
        workspace = await get_workspace(session, identity, workspace_id)
        if workspace.role not in {Role.WORKSPACE_OWNER, Role.WORKSPACE_ADMIN}:
            raise DomainError(FailureCode.POLICY_DENIED, "model manage permission required")
        return workspace

    @app.post("/documents", response_model=KnowledgeIngestionView, status_code=202)
    @app.post("/knowledge/documents", response_model=KnowledgeIngestionView, status_code=202)
    async def knowledge_upload(
        workspace_id: UUID,
        document_id: UUID,
        name: str,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> KnowledgeIngestionView:
        workspace = await get_workspace(session, identity, workspace_id)
        if "knowledge.write" not in ROLE_PERMISSIONS[workspace.role]:
            raise DomainError(FailureCode.POLICY_DENIED, "knowledge write permission required")
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 500:
            raise DomainError(FailureCode.VALIDATION_FAILED, "document name is invalid")
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
        if media_type not in SUPPORTED_DOCUMENT_MEDIA_TYPES:
            raise DomainError(FailureCode.VALIDATION_FAILED, "unsupported document media type")
        blob = await request.body()
        if not blob or len(blob) > MAX_DOCUMENT_BYTES:
            raise DomainError(FailureCode.VALIDATION_FAILED, "document size invalid")
        source_hash, object_name = await configured_knowledge_ingestor(request).stage_blob(
            workspace_id=workspace_id,
            document_id=document_id,
            media_type=media_type,
            blob=blob,
        )
        return await request_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            document_id=document_id,
            name=clean_name,
            media_type=media_type,
            source_hash=source_hash,
            object_name=object_name,
            parser_version=os.environ.get("KNOWLEDGE_PARSER_VERSION", "parser-1.0.0+bge-m3"),
            trace_id=request.state.trace_id,
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"),
        )

    @app.get("/documents", response_model=list[KnowledgeIngestionView])
    @app.get("/knowledge/documents", response_model=list[KnowledgeIngestionView])
    async def knowledge_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[KnowledgeIngestionView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_knowledge_ingestions(session, workspace_id)

    @app.get(
        "/knowledge/documents/{ingestion_id}",
        response_model=KnowledgeIngestionView,
    )
    async def knowledge_get(
        ingestion_id: UUID,
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> KnowledgeIngestionView:
        await get_workspace(session, identity, workspace_id)
        return await get_knowledge_ingestion(session, workspace_id, ingestion_id)

    @app.post("/skills/quarantine", response_model=SkillInstallationView, status_code=201)
    async def skill_quarantine(
        workspace_id: UUID,
        name: str,
        version: str,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> SkillInstallationView:
        workspace = await require_skill_admin(session, identity, workspace_id)
        blob = await request.body()
        if not blob or len(blob) > MAX_SKILL_ARCHIVE_BYTES:
            raise DomainError(FailureCode.VALIDATION_FAILED, "skill archive size invalid")
        store = configured_skill_store(request)
        digest, object_name = await store.stage(workspace_id, blob)
        return await quarantine_skill(
            session,
            identity=identity,
            workspace_id=workspace_id,
            declared_name=name,
            declared_version=version,
            archive_sha256=digest,
            object_name=object_name,
            storage_uri=store.storage_uri(object_name),
            blob=blob,
            capabilities=(
                tuple(request.app.state.skill_capabilities)
                + await list_mcp_capabilities(session, workspace_id)
            ),
            existing_permissions=ROLE_PERMISSIONS[workspace.role],
            trace_id=request.state.trace_id,
        )

    @app.get("/skills", response_model=list[SkillInstallationView])
    async def skill_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[SkillInstallationView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_skill_installations(session, workspace_id)

    @app.post("/skills/install", response_model=SkillInstallationView)
    async def skill_install(
        value: SkillLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> SkillInstallationView:
        await require_skill_admin(session, identity, value.workspace_id)
        if value.installation_id is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "installation_id is required")
        return await transition_skill_installation(
            session,
            identity=identity,
            workspace_id=value.workspace_id,
            installation_id=value.installation_id,
            action="install",
            expected_version=value.expected_version,
            trace_id=request.state.trace_id,
        )

    async def apply_skill_lifecycle(
        action: Literal["enable", "disable"],
        installation_id: UUID,
        value: SkillLifecycleInput,
        request: Request,
        identity: Identity,
        session: AsyncSession,
    ) -> SkillInstallationView:
        await require_skill_admin(session, identity, value.workspace_id)
        if value.installation_id not in {None, installation_id}:
            raise DomainError(FailureCode.VALIDATION_FAILED, "installation identity mismatch")
        return await transition_skill_installation(
            session,
            identity=identity,
            workspace_id=value.workspace_id,
            installation_id=installation_id,
            action=action,
            expected_version=value.expected_version,
            trace_id=request.state.trace_id,
        )

    @app.post("/skills/{installation_id}/enable", response_model=SkillInstallationView)
    async def skill_enable(
        installation_id: UUID,
        value: SkillLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> SkillInstallationView:
        return await apply_skill_lifecycle(
            "enable", installation_id, value, request, identity, session
        )

    @app.post("/skills/{installation_id}/disable", response_model=SkillInstallationView)
    async def skill_disable(
        installation_id: UUID,
        value: SkillLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> SkillInstallationView:
        return await apply_skill_lifecycle(
            "disable", installation_id, value, request, identity, session
        )

    @app.post("/mcp", response_model=MCPServerView, status_code=201)
    async def mcp_register(
        value: MCPRegisterInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> MCPServerView:
        await require_mcp_admin(session, identity, value.workspace_id)
        return await register_mcp_server(
            session,
            identity=identity,
            workspace_id=value.workspace_id,
            name=value.name,
            provider=value.provider,
            trace_id=request.state.trace_id,
        )

    @app.get("/mcp", response_model=list[MCPServerView])
    async def mcp_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[MCPServerView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_mcp_servers(session, workspace_id)

    async def apply_mcp_sync(
        server_id: UUID,
        value: MCPLifecycleInput,
        request: Request,
        identity: Identity,
        session: AsyncSession,
    ) -> MCPServerView:
        await require_mcp_admin(session, identity, value.workspace_id)
        return await sync_mcp_server(
            session,
            identity=identity,
            workspace_id=value.workspace_id,
            server_id=server_id,
            expected_version=value.expected_version,
            trace_id=request.state.trace_id,
        )

    @app.post("/mcp/{server_id}/connect", response_model=MCPServerView)
    async def mcp_connect(
        server_id: UUID,
        value: MCPLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> MCPServerView:
        return await apply_mcp_sync(server_id, value, request, identity, session)

    @app.post("/mcp/{server_id}/health", response_model=MCPServerView)
    async def mcp_health(
        server_id: UUID,
        value: MCPLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> MCPServerView:
        return await apply_mcp_sync(server_id, value, request, identity, session)

    @app.post("/mcp/{server_id}/disable", response_model=MCPServerView)
    async def mcp_disable(
        server_id: UUID,
        value: MCPLifecycleInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> MCPServerView:
        await require_mcp_admin(session, identity, value.workspace_id)
        return await disable_mcp_server(
            session,
            identity=identity,
            workspace_id=value.workspace_id,
            server_id=server_id,
            expected_version=value.expected_version,
            trace_id=request.state.trace_id,
        )

    @app.get("/capabilities", response_model=list[CapabilityDescriptor])
    async def capability_list(
        workspace_id: UUID,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[CapabilityDescriptor, ...]:
        await get_workspace(session, identity, workspace_id)
        return tuple(request.app.state.skill_capabilities) + await list_mcp_capabilities(
            session, workspace_id
        )

    @app.post("/models", response_model=ModelConfigView, status_code=201)
    async def model_config_create(
        value: ModelConfigCreate,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ModelConfigView:
        await require_model_admin(session, identity, value.workspace_id)
        master_key = os.environ.get("SECRET_MASTER_KEY")
        if not master_key:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "secret master key is not configured",
            )
        return await create_model_config(
            session,
            identity=identity,
            value=value,
            cipher=SecretCipher(master_key),
            trace_id=request.state.trace_id,
        )

    @app.get("/models", response_model=list[ModelConfigView])
    async def model_config_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[ModelConfigView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_model_configs(session, workspace_id)

    @app.post("/models/{config_id}/probe", response_model=ModelProbeView)
    async def model_config_probe(
        config_id: UUID,
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ModelProbeView:
        await require_model_admin(session, identity, workspace_id)
        master_key = os.environ.get("SECRET_MASTER_KEY")
        if not master_key:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "secret master key is not configured",
            )
        config, key, row = await load_model_gateway_config(
            session, workspace_id, config_id, SecretCipher(master_key)
        )
        async with httpx.AsyncClient() as client:
            gateway = ModelGateway(config, key, client)
            if row.kind == "CHAT":
                reply = await gateway.chat(
                    (Message(role="user", content="Introduce yourself in one sentence."),),
                    Classification.PUBLIC,
                )
                content = reply.content[:4000]
                input_tokens = reply.usage.input_tokens
                output_tokens = reply.usage.output_tokens
                model = reply.model
            elif row.kind == "EMBEDDING":
                vectors = await gateway.embed(("health probe",), Classification.PUBLIC)
                content = f"embedding_dimensions={len(vectors[0])}"
                input_tokens = 0
                output_tokens = 0
                model = config.model
            else:
                scores = await RerankerGateway(gateway).score(
                    "health probe",
                    ("health probe", "unrelated"),
                    Classification.PUBLIC,
                )
                content = "reranker_scores=" + ",".join(f"{score:.6f}" for score in scores)
                input_tokens = 0
                output_tokens = 0
                model = config.model
        return ModelProbeView(
            config_id=config_id,
            ok=True,
            model=model,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @app.get("/artifacts", response_model=list[ArtifactManifest])
    async def artifact_list(
        workspace_id: UUID,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[ArtifactManifest, ...]:
        await get_workspace(session, identity, workspace_id)
        return await configured_artifact_store(request).list_manifests(session, workspace_id)

    @app.get("/artifacts/{artifact_id}", response_model=ArtifactManifest)
    async def artifact_get(
        artifact_id: UUID,
        workspace_id: UUID,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ArtifactManifest:
        await get_workspace(session, identity, workspace_id)
        return await configured_artifact_store(request).get_manifest(
            session, workspace_id, artifact_id
        )

    @app.get("/artifacts/{artifact_id}/download")
    async def artifact_download(
        artifact_id: UUID,
        workspace_id: UUID,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> Response:
        await get_workspace(session, identity, workspace_id)
        store = configured_artifact_store(request)
        manifest = await store.get_manifest(session, workspace_id, artifact_id)
        content = await store.get(session, workspace_id, artifact_id)
        return Response(
            content=content,
            media_type=manifest.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{artifact_id}"',
                "ETag": f'"{manifest.sha256}"',
            },
        )

    @app.get("/audit", response_model=list[AuditView])
    async def audit_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[AuditView, ...]:
        workspace_view = await get_workspace(session, identity, workspace_id)
        return await list_audit_events(session, workspace_id, workspace_view.role)

    @app.get("/consistency/incidents", response_model=list[ConsistencyIncidentView])
    async def consistency_incident_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[ConsistencyIncidentView, ...]:
        workspace_view = await get_workspace(session, identity, workspace_id)
        return await list_consistency_incidents(
            session, workspace_id, workspace_view.role
        )

    @app.get("/eval/cases", response_model=list[GoldenTask])
    async def evaluation_cases(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[GoldenTask, ...]:
        await get_workspace(session, identity, workspace_id)
        return read_golden_tasks()

    @app.get("/usage", response_model=list[UsageView])
    async def usage_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
        task_id: UUID | None = None,
    ) -> tuple[UsageView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_usage_records(session, workspace_id, task_id=task_id)

    @app.get("/eval/runs", response_model=list[EvaluationRunView])
    async def evaluation_runs(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> tuple[EvaluationRunView, ...]:
        await get_workspace(session, identity, workspace_id)
        return await list_evaluation_runs(session, workspace_id)

    @app.post("/eval/runs", response_model=EvaluationRunView, status_code=201)
    async def evaluation_run_create(
        value: EvaluationRunCreate,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> EvaluationRunView:
        workspace = await get_workspace(session, identity, value.workspace_id)
        if workspace.role not in {Role.WORKSPACE_OWNER, Role.WORKSPACE_ADMIN}:
            raise DomainError(FailureCode.POLICY_DENIED, "evaluation permission missing")
        try:
            return await persist_evaluation_run(
                session,
                value.workspace_id,
                identity.user_id,
                request.state.trace_id,
                value.label,
                value.observations,
            )
        except ValueError as exc:
            raise DomainError(FailureCode.VALIDATION_FAILED, str(exc)) from exc

    async def apply_task_control(
        action: str,
        workspace_id: UUID,
        task_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: Identity,
        session: AsyncSession,
    ) -> TaskView:
        return await control_task(
            session,
            identity,
            workspace_id,
            task_id,
            action,
            value.expected_version,
            request.state.trace_id,
        )

    @app.post("/tasks/{task_id}/modify", response_model=TaskView)
    async def task_modify(
        task_id: UUID,
        value: TaskModifyInput,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        current = await get_task(session, identity, value.workspace_id, task_id)
        if current.version != value.expected_version:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "task version precondition failed",
            )
        if value.decision.trigger != "USER_MODIFICATION":
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "task modify requires USER_MODIFICATION trigger",
            )
        await apply_replan(
            session,
            value.workspace_id,
            task_id,
            identity.user_id,
            request.state.trace_id,
            value.decision,
            actor_type="USER",
        )
        return await get_task(session, identity, value.workspace_id, task_id)

    @app.post("/tasks/{task_id}/pause", response_model=TaskView)
    async def task_pause_alias(
        task_id: UUID,
        workspace_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "pause", workspace_id, task_id, value, request, identity, session
        )

    @app.post("/tasks/{task_id}/resume", response_model=TaskView)
    async def task_resume_alias(
        task_id: UUID,
        workspace_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "resume", workspace_id, task_id, value, request, identity, session
        )

    @app.post("/tasks/{task_id}/cancel", response_model=TaskView)
    async def task_cancel_alias(
        task_id: UUID,
        workspace_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "cancel", workspace_id, task_id, value, request, identity, session
        )

    @app.post("/workspaces/{workspace_id}/tasks/{task_id}/pause", response_model=TaskView)
    async def task_pause(
        workspace_id: UUID,
        task_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "pause", workspace_id, task_id, value, request, identity, session
        )

    @app.post("/workspaces/{workspace_id}/tasks/{task_id}/resume", response_model=TaskView)
    async def task_resume(
        workspace_id: UUID,
        task_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "resume", workspace_id, task_id, value, request, identity, session
        )

    @app.post("/workspaces/{workspace_id}/tasks/{task_id}/cancel", response_model=TaskView)
    async def task_cancel(
        workspace_id: UUID,
        task_id: UUID,
        value: VersionPrecondition,
        request: Request,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> TaskView:
        return await apply_task_control(
            "cancel", workspace_id, task_id, value, request, identity, session
        )

    @app.get("/approvals", response_model=list[ApprovalView])
    async def approval_list(
        workspace_id: UUID,
        identity: CurrentIdentity,
        session: DatabaseSession,
        state: str | None = None,
    ) -> tuple[ApprovalView, ...]:
        return await list_approvals(session, identity, workspace_id, state)

    @app.post("/approvals/{approval_id}/approve", response_model=ApprovalView)
    async def approval_approve(
        approval_id: UUID,
        value: ApprovalDecisionInput,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ApprovalView:
        result = await decide_as_user(
            session,
            identity,
            value.workspace_id,
            approval_id,
            "APPROVED",
            value.expected_version,
        )
        APPROVAL_TOTAL.labels("APPROVED").inc()
        return result

    @app.post("/approvals/{approval_id}/reject", response_model=ApprovalView)
    async def approval_reject(
        approval_id: UUID,
        value: ApprovalDecisionInput,
        identity: CurrentIdentity,
        session: DatabaseSession,
    ) -> ApprovalView:
        result = await decide_as_user(
            session,
            identity,
            value.workspace_id,
            approval_id,
            "REJECTED",
            value.expected_version,
        )
        APPROVAL_TOTAL.labels("REJECTED").inc()
        return result

    return app


app = create_app()
