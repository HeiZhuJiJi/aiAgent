from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1


class ConsistencyIncidentView(Contract):
    incident_id: UUID
    workspace_id: UUID
    task_id: UUID | None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    incident_type: str
    detail: dict[str, JsonValue]
    created_at: datetime


class TaskState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_HITL = "WAITING_HITL"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    WAITING_HITL = "WAITING_HITL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    SUPERSEDED = "SUPERSEDED"


class AttemptState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class Risk(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class FailureCode(StrEnum):
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_SERVER_ERROR = "LLM_SERVER_ERROR"
    LLM_SCHEMA_ERROR = "LLM_SCHEMA_ERROR"
    CAPABILITY_TIMEOUT = "CAPABILITY_TIMEOUT"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    CAPABILITY_BAD_OUTPUT = "CAPABILITY_BAD_OUTPUT"
    CAPABILITY_UNKNOWN_OUTCOME = "CAPABILITY_UNKNOWN_OUTCOME"
    MCP_DISCONNECTED = "MCP_DISCONNECTED"
    MCP_SCHEMA_CHANGED = "MCP_SCHEMA_CHANGED"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    WORKER_CRASH = "WORKER_CRASH"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    STALE_ATTEMPT = "STALE_ATTEMPT"
    CONSISTENCY_VIOLATION = "CONSISTENCY_VIOLATION"


class Failure(Contract):
    code: FailureCode
    safe_detail: str
    trace_id: str


class KnowledgeIngestionView(Contract):
    ingestion_id: UUID
    workspace_id: UUID
    document_id: UUID
    name: str
    media_type: str
    source_hash: str
    parser_version: str
    state: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
    version: int
    document_version_id: UUID | None
    safe_error: str | None
    created_at: datetime
    completed_at: datetime | None


class SkillInstallationView(Contract):
    installation_id: UUID
    workspace_id: UUID
    skill_id: UUID | None
    skill_version_id: UUID | None
    name: str
    skill_version: str
    archive_sha256: str
    state: Literal[
        "QUARANTINED",
        "REJECTED",
        "PENDING_CONFIRMATION",
        "INSTALLED",
        "ENABLED",
        "DISABLED",
    ]
    version: int
    permission_diff: tuple[str, ...]
    safe_error: str | None
    created_at: datetime
    confirmed_at: datetime | None
    enabled_at: datetime | None


class MCPServerView(Contract):
    server_id: UUID
    workspace_id: UUID
    name: str
    provider: str
    transport: Literal["STDIO"]
    status: Literal["REGISTERED", "ACTIVE", "UNHEALTHY", "DISABLED"]
    version: int
    schema_hash: str | None
    capability_count: int
    last_health_at: datetime | None
    safe_error: str | None
    created_at: datetime


class TaskGoal(Contract):
    goal: str = Field(min_length=1)
    constraints: tuple[str, ...]
    deliverables: tuple[str, ...] = Field(min_length=1)
    workspace_id: UUID
    time_budget_sec: int = Field(gt=0)
    token_budget: int = Field(gt=0)
    cost_budget: float = Field(ge=0, allow_inf_nan=False)
    requested_skill: str | None = None
    user_context: str = ""


class CapabilityRequirement(Contract):
    capability_id: str
    version_range: str


class PlanStepSpec(Contract):
    step_key: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    dependencies: tuple[str, ...] = ()
    capability_requirements: tuple[CapabilityRequirement, ...] = Field(min_length=1)
    expected_output: str
    success_criteria: tuple[str, ...] = Field(min_length=1)
    risk_hint: Risk
    execution_mode: Literal["CAPABILITY", "RESEARCH_SUBAGENT", "REVIEW"]
    parallelizable: bool
    max_attempts: int = Field(ge=1, le=10)


class Plan(Contract):
    plan_id: UUID
    task_id: UUID
    workspace_id: UUID
    execution_snapshot_id: UUID
    version: int = Field(ge=1)
    goal: str
    constraints: tuple[str, ...]
    deliverables: tuple[str, ...]
    steps: tuple[PlanStepSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dag(self) -> Self:
        keys = {s.step_key for s in self.steps}
        if len(keys) != len(self.steps):
            raise ValueError("duplicate step key")
        completed: set[str] = set()
        pending = list(self.steps)
        for step in pending:
            if len(set(step.dependencies)) != len(step.dependencies):
                raise ValueError("duplicate dependency")
            if not set(step.dependencies) <= keys:
                raise ValueError("unknown dependency")
        while pending:
            ready = [s for s in pending if set(s.dependencies) <= completed]
            if not ready:
                raise ValueError("cyclic dependency")
            completed.update(s.step_key for s in ready)
            pending = [s for s in pending if s.step_key not in completed]
        return self


class VersionRef(Contract):
    identifier: str
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionSnapshot(Contract):
    snapshot_id: UUID
    workspace_id: UUID
    runtime_version: str
    workflow_definition_version: str
    planner_prompt_version: VersionRef
    router_prompt_version: VersionRef
    reviewer_prompt_version: VersionRef
    model_config_versions: tuple[VersionRef, ...]
    skill_versions: tuple[VersionRef, ...]
    capability_versions: tuple[VersionRef, ...]
    policy_version: VersionRef
    knowledge_document_refs: tuple[VersionRef, ...]
    context_policy_version: VersionRef
    schema_versions: tuple[VersionRef, ...]
    feature_flags: frozenset[str]
    created_at: AwareDatetime


class TaskRun(Contract):
    task_id: UUID
    workspace_id: UUID
    execution_snapshot_id: UUID
    goal: TaskGoal
    state: TaskState
    version: int = Field(ge=1)
    created_at: AwareDatetime
    schedule_block_reason: str | None = None

    @model_validator(mode="after")
    def same_workspace(self) -> Self:
        if self.workspace_id != self.goal.workspace_id:
            raise ValueError("goal workspace mismatch")
        return self


class TaskStep(Contract):
    step_id: UUID
    workspace_id: UUID
    task_id: UUID
    plan_id: UUID
    spec: PlanStepSpec
    state: StepState
    version: int = Field(ge=1)
    fencing_token: int = Field(ge=0)


class StepAttempt(Contract):
    attempt_id: UUID
    workspace_id: UUID
    step_id: UUID
    worker_id: str
    state: AttemptState
    fencing_token: int = Field(gt=0)
    lease_expires_at: AwareDatetime
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    failure: Failure | None = None
    trace_id: str


class RetryPolicy(Contract):
    max_attempts: int = Field(ge=1, le=10)
    initial_delay_sec: float = Field(gt=0)
    max_delay_sec: float = Field(gt=0)


class CapabilityDescriptor(Contract):
    id: str
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    kind: Literal["BUILTIN", "FILE", "RAG", "WEB", "MCP", "SERVICE", "MODEL"]
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    tags: tuple[str, ...]
    risk_level: Risk
    side_effect_mode: Literal["NONE", "INTERNAL", "EXTERNAL"]
    idempotency_mode: Literal["READ_ONLY", "NATIVE_IDEMPOTENCY", "RECONCILABLE", "NON_RETRYABLE"]
    compensation_mode: Literal["NONE", "AUTO", "MANUAL", "NOT_REVERSIBLE"]
    compensation_capability: str | None
    reconciliation_capability: str | None
    timeout_sec: float = Field(gt=0)
    retry_policy: RetryPolicy
    required_permissions: frozenset[str]
    approval_policy: Literal["NONE", "REQUIRED"]
    provider: str
    health_status: Literal["ACTIVE", "DEPRECATED", "DISABLED", "UNHEALTHY", "UNSUPPORTED"]
    cost_hint: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def safe_effect_semantics(self) -> Self:
        if self.side_effect_mode != "NONE" and self.idempotency_mode == "READ_ONLY":
            raise ValueError("side effect cannot be READ_ONLY")
        if self.compensation_mode == "AUTO" and not self.compensation_capability:
            raise ValueError("automatic compensation requires capability")
        if self.idempotency_mode == "RECONCILABLE" and not self.reconciliation_capability:
            raise ValueError("reconcilable capability requires lookup")
        if self.side_effect_mode == "EXTERNAL" and (
            self.risk_level != Risk.EXTERNAL_SIDE_EFFECT or self.approval_policy != "REQUIRED"
        ):
            raise ValueError("external effect requires external risk and HITL")
        return self


class CapabilityRequest(Contract):
    command_id: UUID
    operation_id: UUID
    workspace_id: UUID
    task_id: UUID
    step_id: UUID
    attempt_id: UUID
    fencing_token: int = Field(gt=0)
    capability_id: str
    capability_version: str
    arguments: dict[str, JsonValue]


class CapabilityResult(Contract):
    operation_id: UUID
    outcome: Literal["SUCCEEDED", "FAILED", "UNKNOWN"]
    output: JsonValue
    failure: Failure | None = None


class CapabilityCall(Contract):
    call_id: UUID
    request: CapabilityRequest
    result: CapabilityResult | None = None
    created_at: AwareDatetime


class Observation(Contract):
    observation_id: UUID
    workspace_id: UUID
    task_id: UUID
    content: str
    trust: Literal["UNTRUSTED_DOCUMENT", "UNTRUSTED_WEB", "UNTRUSTED_MCP_OUTPUT"]
    evidence_ids: tuple[UUID, ...]


class Evidence(Contract):
    evidence_id: UUID
    workspace_id: UUID
    task_id: UUID
    step_id: UUID
    source_type: Literal["DOCUMENT", "WEB", "MCP", "DERIVED"]
    source_id: str
    locator: str
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str
    confidence: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    retrieved_at: AwareDatetime
    generated_by: UUID
    derived_from: tuple[UUID, ...]


class Claim(Contract):
    claim_id: UUID
    workspace_id: UUID
    task_id: UUID
    content: str
    importance: Literal["LOW", "MEDIUM", "HIGH"]
    risk_level: Risk
    evidence_ids: tuple[UUID, ...]


class ApprovalRequest(Contract):
    approval_id: UUID
    workspace_id: UUID
    task_id: UUID
    operation_id: UUID
    capability_id: str
    capability_version: str
    parameters: dict[str, JsonValue]
    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str
    reason: str
    risk: Risk
    evidence_ids: tuple[UUID, ...]
    expires_at: AwareDatetime


class ApprovalResult(Contract):
    approval_id: UUID
    decision: Literal["APPROVED", "REJECTED"]
    actor_id: UUID
    decided_at: AwareDatetime


class ReplanDecision(Contract):
    reason: str
    trigger: Literal[
        "TERMINAL_FAILURE",
        "EVIDENCE_INSUFFICIENT",
        "CAPABILITY_UNAVAILABLE",
        "VALIDATION_FAILURE",
        "USER_MODIFICATION",
        "BUDGET_DANGER",
    ]
    action: Literal[
        "ADD_STEP", "REPLACE_STEP", "EXPAND_STEP", "RETRY_STEP", "SKIP_STEP", "ABORT_TASK"
    ]
    affected_steps: tuple[str, ...]
    new_steps: tuple[PlanStepSpec, ...]
    preserve_completed_steps: bool
    budget_impact: float = Field(ge=0, allow_inf_nan=False)


class ContextItem(Contract):
    source: str
    content: str
    priority: int
    token_count: int = Field(ge=0)
    included: bool
    reason: str
    trust: Literal[
        "TRUSTED_PLATFORM",
        "TRUSTED_WORKSPACE",
        "USER_INPUT",
        "UNTRUSTED_DOCUMENT",
        "UNTRUSTED_WEB",
        "UNTRUSTED_MCP_OUTPUT",
    ]


class ContextBundle(Contract):
    items: tuple[ContextItem, ...]
    max_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def enforce_budget(self) -> Self:
        if sum(i.token_count for i in self.items if i.included) > self.max_tokens:
            raise ValueError("context budget exceeded")
        return self


class ConversationSummary(Contract):
    summary_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    version: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=32_000)
    source: str = Field(min_length=1, max_length=500)
    created_at: AwareDatetime


class Conversation(Contract):
    conversation_id: UUID
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=500)
    version: int = Field(gt=0)
    created_at: AwareDatetime


class ConversationMessage(Contract):
    message_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    role: Literal["SYSTEM", "USER", "ASSISTANT"]
    content: str = Field(min_length=1, max_length=1_000_000)
    created_at: AwareDatetime


class ProjectMemoryType(StrEnum):
    FACT = "FACT"
    DECISION = "DECISION"
    PREFERENCE = "PREFERENCE"


class ProjectMemory(Contract):
    memory_id: UUID
    workspace_id: UUID
    content: str = Field(min_length=1, max_length=32_000)
    type: ProjectMemoryType
    source: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    created_at: AwareDatetime
    supersedes: UUID | None = None


class Checkpoint(Contract):
    checkpoint_id: UUID
    workspace_id: UUID
    task_id: UUID
    sequence: int = Field(gt=0)
    active_plan_id: UUID | None
    completed_steps: tuple[UUID, ...]
    running_step: UUID | None
    attempt: UUID | None
    evidence_refs: tuple[UUID, ...]
    artifact_refs: tuple[UUID, ...]
    budget_usage: dict[str, JsonValue]
    context_summary: str = Field(max_length=32_000)
    operation_refs: tuple[UUID, ...]
    execution_snapshot_id: UUID
    resume_token: str = Field(min_length=16, max_length=200)
    created_at: AwareDatetime


class ArtifactManifest(Contract):
    artifact_id: UUID
    workspace_id: UUID
    source_task: UUID
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    claim_ids: tuple[UUID, ...]
    created_at: AwareDatetime


class ReviewResult(Contract):
    accepted: bool
    missing_deliverables: tuple[str, ...]
    unsupported_claims: tuple[UUID, ...]
    constraint_violations: tuple[str, ...]
    citation_errors: tuple[str, ...]

    @model_validator(mode="after")
    def acceptance_matches_findings(self) -> Self:
        if self.accepted and any(
            (
                self.missing_deliverables,
                self.unsupported_claims,
                self.constraint_violations,
                self.citation_errors,
            )
        ):
            raise ValueError("review cannot accept unresolved findings")
        return self


class CancellationResult(Contract):
    task_id: UUID
    workspace_id: UUID
    outcome: Literal[
        "CLEAN", "PARTIALLY_COMPENSATED", "REQUIRES_MANUAL_ACTION", "IRREVERSIBLE_EFFECTS_REMAIN"
    ]
    compensated_operations: tuple[UUID, ...]
    remaining_operations: tuple[UUID, ...]
    recorded_at: datetime
