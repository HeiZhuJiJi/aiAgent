from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, SecretStr, model_validator

from enterprise_agent.contracts.domain import (
    AgentTurnDecision,
    ApprovalRequest,
    BoundedResearchPlan,
    CompletionStatus,
    Contract,
    ConversationMessage,
    DeliveryDecision,
    ReplanDecision,
    Requirement,
    RequirementAssessment,
    TaskGoal,
    TaskState,
)


class TaskView(Contract):
    task_id: UUID
    workspace_id: UUID
    state: TaskState
    version: int
    execution_snapshot_id: UUID
    goal: TaskGoal


class TaskCompletionView(Contract):
    workspace_id: UUID
    task_id: UUID
    execution_status: TaskState
    completion_status: CompletionStatus
    delivery_decision: DeliveryDecision | None
    unresolved_requirement_ids: tuple[UUID, ...]
    disclosure: str


class RequirementAssessmentView(Contract):
    requirement: Requirement
    assessment: RequirementAssessment | None


class ResearchTurnCreateInput(Contract):
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    content: str = Field(min_length=1, max_length=1_000_000)
    goal: TaskGoal

    @model_validator(mode="after")
    def validate_research_turn(self) -> Self:
        if self.goal.workspace_id != self.workspace_id:
            raise ValueError("research Turn workspace mismatch")
        if self.goal.goal.strip() != self.content.strip():
            raise ValueError("research Turn content must match the task goal")
        if self.goal.requested_skill not in {
            "web-research",
            "document-analysis",
            "grounded-research",
        }:
            raise ValueError("research Turn requires a chat research Skill")
        return self


class ResearchTurnView(Contract):
    user_message: ConversationMessage
    task: TaskView


class VersionPrecondition(Contract):
    expected_version: int = Field(gt=0)


class TaskModifyInput(VersionPrecondition):
    workspace_id: UUID
    decision: ReplanDecision


class ApprovalDecisionInput(VersionPrecondition):
    workspace_id: UUID


class SkillLifecycleInput(VersionPrecondition):
    workspace_id: UUID
    installation_id: UUID | None = None


class MCPRegisterInput(Contract):
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["procurement"]


class MCPLifecycleInput(VersionPrecondition):
    workspace_id: UUID


class ApprovalView(Contract):
    approval_id: UUID
    workspace_id: UUID
    task_id: UUID
    step_id: UUID
    operation_id: UUID
    request: ApprovalRequest
    state: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    version: int
    actor_id: UUID | None
    decided_at: datetime | None
    created_at: datetime


class ConversationCreate(Contract):
    conversation_id: UUID
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=500)


class ConversationMessageCreate(Contract):
    message_id: UUID
    workspace_id: UUID
    content: str = Field(min_length=1, max_length=1_000_000)


class ConversationDeleteInput(VersionPrecondition):
    workspace_id: UUID


class TaskResultMessageCreate(Contract):
    workspace_id: UUID


class KnowledgeIngestionRetryInput(VersionPrecondition):
    workspace_id: UUID


class KnowledgeDocumentDeleteInput(VersionPrecondition):
    workspace_id: UUID


class KnowledgeDocumentPreviewView(Contract):
    ingestion_id: UUID
    document_version_id: UUID
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    content: str
    truncated: bool


class ChatStreamInput(Contract):
    workspace_id: UUID
    conversation_id: UUID
    model_config_id: UUID
    fallback_model_config_id: UUID | None = None
    user_message_id: UUID
    content: str = Field(min_length=1, max_length=1_000_000)


class ChatRouteInput(Contract):
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID | None = None
    content: str = Field(min_length=1, max_length=1_000_000)
    web_mode: Literal["AUTO", "FORCE", "OFF"] = "AUTO"
    document_version_id: UUID | None = None
    model_config_id: UUID | None = None


class ChatRouteView(Contract):
    route: Literal["DIRECT", "WEB", "RAG", "MIXED", "DURABLE"]
    resolved_content: str = Field(min_length=1, max_length=1_000_000)
    search_query: str | None = Field(default=None, max_length=2000)
    search_queries: tuple[str, ...] = Field(default=(), max_length=5)
    search_limit: int = Field(default=0, ge=0, le=20)
    reason: Literal[
        "EXPLICIT_WEB",
        "CURRENT_INFORMATION",
        "SELECTED_DOCUMENT",
        "WORKSPACE_KNOWLEDGE",
        "MIXED_EVIDENCE",
        "DIRECT_REQUEST",
        "MISSING_CONTEXT",
        "MODEL_ROUTER",
        "MODEL_CLARIFICATION",
        "AGENT_PLANNER",
        "WEB_DISABLED",
    ]
    confidence: float = Field(ge=0, le=1)
    context_required: bool
    context_resolved: bool
    research_plan: BoundedResearchPlan | None = None
    agent_decision: AgentTurnDecision | None = None


class AuditView(Contract):
    audit_id: UUID
    workspace_id: UUID
    actor_type: Literal["USER", "SERVICE_ACCOUNT", "SYSTEM", "AGENT"]
    actor_id: UUID
    task_id: UUID | None
    step_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    capability_id: str | None
    policy_decision: str | None
    approval_id: UUID | None
    result: dict[str, JsonValue] | None
    trace_id: str
    created_at: datetime


class UsageView(Contract):
    usage_id: UUID
    workspace_id: UUID
    task_id: UUID | None
    evaluation_run_id: UUID | None
    kind: str
    units: int
    tokens: int
    cost: float
    detail: dict[str, JsonValue]
    created_at: datetime


class TraceNode(Contract):
    node_id: str
    parent_id: str | None
    kind: Literal[
        "TASK",
        "ROUTER",
        "PLAN",
        "STEP",
        "ATTEMPT",
        "EVIDENCE",
        "CAPABILITY",
        "LLM",
        "MCP",
        "REPLAN",
        "HITL",
        "ARTIFACT",
        "FAILURE",
        "EVENT",
        "FINISHED",
    ]
    label: str
    state: str | None
    occurred_at: datetime | None
    trace_id: str | None
    safe_detail: str | None = None


class TaskTraceView(Contract):
    workspace_id: UUID
    task_id: UUID
    state: TaskState
    nodes: tuple[TraceNode, ...]


class ModelConfigCreate(Contract):
    config_id: UUID
    workspace_id: UUID
    kind: Literal["CHAT", "EMBEDDING", "RERANKER"]
    provider: Literal["Qwen", "DeepSeek", "OpenAICompatible"]
    base_url: str = Field(min_length=1, max_length=1000)
    model_name: str = Field(min_length=1, max_length=300)
    external: bool
    api_key: SecretStr = SecretStr("")


class ModelConfigUpdate(VersionPrecondition):
    workspace_id: UUID
    kind: Literal["CHAT", "EMBEDDING", "RERANKER"]
    provider: Literal["Qwen", "DeepSeek", "OpenAICompatible"]
    base_url: str = Field(min_length=1, max_length=1000)
    model_name: str = Field(min_length=1, max_length=300)
    external: bool
    api_key: SecretStr | None = None


class ModelConfigStateInput(VersionPrecondition):
    workspace_id: UUID
    enabled: bool


class ModelConfigView(Contract):
    config_id: UUID
    workspace_id: UUID
    kind: Literal["CHAT", "EMBEDDING", "RERANKER"]
    provider: Literal["Qwen", "DeepSeek", "OpenAICompatible"]
    base_url: str
    model_name: str
    external: bool
    has_secret: bool
    enabled: bool
    superseded: bool = False
    version: int
    created_at: datetime


class ModelProbeView(Contract):
    config_id: UUID
    ok: bool
    model: str
    content: str
    input_tokens: int
    output_tokens: int
