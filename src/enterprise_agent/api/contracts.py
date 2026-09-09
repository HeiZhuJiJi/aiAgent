from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, SecretStr

from enterprise_agent.contracts.domain import (
    ApprovalRequest,
    Contract,
    ReplanDecision,
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


class ChatStreamInput(Contract):
    workspace_id: UUID
    conversation_id: UUID
    model_config_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    content: str = Field(min_length=1, max_length=1_000_000)


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
    version: int
    created_at: datetime


class ModelProbeView(Contract):
    config_id: UUID
    ok: bool
    model: str
    content: str
    input_tokens: int
    output_tokens: int
