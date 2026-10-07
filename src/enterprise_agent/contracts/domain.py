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


class RequirementType(StrEnum):
    READ = "READ"
    ANALYSIS = "ANALYSIS"
    WRITE = "WRITE"
    VERIFICATION = "VERIFICATION"


class AssessmentStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONFLICTING = "CONFLICTING"
    STALE = "STALE"
    UNVERIFIABLE = "UNVERIFIABLE"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class GapType(StrEnum):
    MISSING = "MISSING"
    UNSUPPORTED = "UNSUPPORTED"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"
    AUTHORIZATION = "AUTHORIZATION"


class CompletionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class DeliveryDecisionType(StrEnum):
    FULL_ANSWER = "FULL_ANSWER"
    PARTIAL_ANSWER = "PARTIAL_ANSWER"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class RequirementRelationType(StrEnum):
    INTENDED_TO_SATISFY = "INTENDED_TO_SATISFY"
    PRODUCED_EVIDENCE_FOR = "PRODUCED_EVIDENCE_FOR"
    VERIFIED_AGAINST = "VERIFIED_AGAINST"


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    REPAIR = "REPAIR"
    ADDITIONAL_VERIFICATION = "ADDITIONAL_VERIFICATION"
    RECONCILE = "RECONCILE"
    REFRESH = "REFRESH"
    CLARIFICATION = "CLARIFICATION"
    AWAIT_AUTHORIZATION = "AWAIT_AUTHORIZATION"
    DELIVERY_DOWNGRADE = "DELIVERY_DOWNGRADE"


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
    document_version: int | None
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


class ClaimDraft(Contract):
    content: str = Field(min_length=1)
    importance: Literal["LOW", "MEDIUM", "HIGH"]
    risk_level: Risk
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class PersistedClaim(ClaimDraft):
    claim_id: UUID


class StructuredAnalysis(Contract):
    decision: str = Field(pattern="^(ADMIT|REJECT|NEEDS_MORE_EVIDENCE)$")
    risk_level: str = Field(pattern="^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    summary: str = Field(min_length=1)
    key_findings: tuple[str, ...]
    missing_information: tuple[str, ...]
    recommendation: str = Field(min_length=1)
    evidence_sufficient: bool
    claims: tuple[ClaimDraft, ...]


class StructuredAnalysisResult(Contract):
    decision: str = Field(pattern="^(ADMIT|REJECT|NEEDS_MORE_EVIDENCE)$")
    risk_level: str = Field(pattern="^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    summary: str = Field(min_length=1)
    key_findings: tuple[str, ...]
    missing_information: tuple[str, ...]
    recommendation: str = Field(min_length=1)
    evidence_sufficient: bool
    claims: tuple[PersistedClaim, ...]


ResearchSource = Literal["PUBLIC_WEB", "SELECTED_DOCUMENT", "WORKSPACE_KNOWLEDGE"]
ResearchSourceClass = Literal[
    "STRUCTURED_DATA",
    "OFFICIAL_OR_FIRST_PARTY",
    "INDEPENDENT_MEDIA",
    "ACADEMIC",
    "SELECTED_DOCUMENT",
    "WORKSPACE_KNOWLEDGE",
    "GENERAL_WEB",
    "USER_GENERATED",
    "PARAMETRIC_KNOWLEDGE",
]


class SourcePlan(Contract):
    """Bounded evidence-source priorities, never executable provider instructions."""

    required_sources: tuple[ResearchSource, ...] = Field(min_length=1, max_length=2)
    preferred_sources: tuple[ResearchSourceClass, ...] = Field(default=(), max_length=6)
    fallback_sources: tuple[ResearchSourceClass, ...] = Field(default=(), max_length=4)
    forbidden_sources: tuple[ResearchSourceClass, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def validate_source_priorities(self) -> Self:
        groups = (
            self.required_sources,
            self.preferred_sources,
            self.fallback_sources,
            self.forbidden_sources,
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("source plan entries must be unique within each priority")
        preference_groups = (
            set(self.preferred_sources),
            set(self.fallback_sources),
            set(self.forbidden_sources),
        )
        if any(
            left & right
            for index, left in enumerate(preference_groups)
            for right in preference_groups[index + 1 :]
        ):
            raise ValueError("source plan priorities must be disjoint")
        if "PARAMETRIC_KNOWLEDGE" not in self.forbidden_sources:
            raise ValueError("bounded research cannot use parametric knowledge as Evidence")
        return self


class AnswerContract(Contract):
    """The user-visible answer shape and minimum evidence needed to satisfy it."""

    answer_type: Literal[
        "FACT",
        "SUMMARY",
        "LIST",
        "TIMELINE",
        "COMPARISON",
        "EXPLANATION",
        "FORECAST",
        "DOCUMENT_SUMMARY",
        "DOCUMENT_QA",
    ]
    required_aspects: tuple[str, ...] = Field(min_length=1, max_length=6)
    completeness: Literal["COMPLETE", "BEST_EFFORT"]
    freshness: Literal["STABLE", "CURRENT", "HISTORICAL", "SOURCE_BOUND"]
    minimum_answer_items: int | None = Field(default=None, ge=1, le=8)
    maximum_answer_items: int | None = Field(default=None, ge=1, le=8)
    require_explicit_dates: bool
    minimum_evidence_items: int = Field(ge=1, le=5)
    minimum_independent_sources: int = Field(ge=1, le=3)
    partial_answer_policy: Literal["ALLOW_SUPPORTED_ONLY", "REQUIRE_COMPLETE"]
    organization: Literal[
        "CONCISE",
        "BULLETS",
        "TIMELINE",
        "COMPARISON",
        "STRUCTURED_SUMMARY",
    ]

    @model_validator(mode="after")
    def validate_required_aspects(self) -> Self:
        normalized = tuple(item.strip() for item in self.required_aspects)
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("answer contract aspects must contain 1-200 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("answer contract aspects must be unique")
        if self.minimum_independent_sources > self.minimum_evidence_items:
            raise ValueError("independent-source minimum cannot exceed Evidence minimum")
        if (self.minimum_answer_items is None) != (self.maximum_answer_items is None):
            raise ValueError("answer item bounds must either both be set or both be omitted")
        if self.answer_type != "DOCUMENT_SUMMARY" and self.minimum_answer_items is None:
            raise ValueError("non-summary answers require bounded answer item counts")
        if (
            self.minimum_answer_items is not None
            and self.maximum_answer_items is not None
            and self.minimum_answer_items > self.maximum_answer_items
        ):
            raise ValueError("minimum answer items cannot exceed maximum answer items")
        return self


class AgentSpeechAct(StrEnum):
    QUESTION = "QUESTION"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    CLARIFICATION = "CLARIFICATION"
    COMMAND = "COMMAND"
    SOCIAL = "SOCIAL"


class AgentExecutionLevel(StrEnum):
    INLINE_CHAT = "INLINE_CHAT"
    BOUNDED_QA = "BOUNDED_QA"
    DURABLE_TASK = "DURABLE_TASK"


class AgentInformationSource(StrEnum):
    MODEL = "MODEL"
    WEB = "WEB"
    RAG = "RAG"
    STRUCTURED_DATA = "STRUCTURED_DATA"


class AgentPlannerAction(StrEnum):
    RESPOND = "RESPOND"
    CLARIFY = "CLARIFY"
    EXECUTE = "EXECUTE"


class TemporalFrame(Contract):
    direction: Literal["STABLE", "CURRENT", "HISTORICAL", "FUTURE"]
    requires_fresh_information: bool
    expression: str | None = Field(default=None, max_length=200)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.start_at is not None and self.end_at is not None and self.start_at > self.end_at:
            raise ValueError("temporal start must not be after end")
        if self.requires_fresh_information and self.direction not in {"CURRENT", "FUTURE"}:
            raise ValueError("fresh information requires current or future temporal direction")
        return self


class IntentFrame(Contract):
    speech_act: AgentSpeechAct
    normalized_question: str = Field(min_length=1, max_length=4000)
    references: tuple[str, ...] = Field(default=(), max_length=8)
    entities: tuple[str, ...] = Field(default=(), max_length=12)
    temporal: TemporalFrame
    missing_information: tuple[str, ...] = Field(default=(), max_length=6)


class AgentSourcePlan(Contract):
    required_sources: tuple[AgentInformationSource, ...] = Field(default=(), max_length=4)
    preferred_sources: tuple[AgentInformationSource, ...] = Field(default=(), max_length=4)
    fallback_sources: tuple[AgentInformationSource, ...] = Field(default=(), max_length=4)
    forbidden_sources: tuple[AgentInformationSource, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def validate_priorities(self) -> Self:
        groups = (
            self.required_sources,
            self.preferred_sources,
            self.fallback_sources,
            self.forbidden_sources,
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("agent source plan entries must be unique within each priority")
        sets = tuple(set(group) for group in groups)
        if any(left & right for index, left in enumerate(sets) for right in sets[index + 1 :]):
            raise ValueError("agent source plan priorities must be disjoint")
        return self


class PlannedToolCall(Contract):
    tool_name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    risk: Risk = Risk.READ


class AgentToolDescriptor(Contract):
    tool_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    supported_sources: tuple[AgentInformationSource, ...] = Field(default=(), max_length=4)
    execution_levels: tuple[AgentExecutionLevel, ...] = Field(min_length=1, max_length=3)
    risk: Risk


class ExecutionDecision(Contract):
    execution_level: AgentExecutionLevel
    source_plan: AgentSourcePlan
    risk: Risk
    maximum_tool_steps: int = Field(ge=0, le=8)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class ModelBinding(Contract):
    config_id: UUID
    provider: Literal["Qwen", "DeepSeek", "OpenAICompatible"]
    model_name: str = Field(min_length=1, max_length=300)
    config_version: int = Field(gt=0)
    selected_by: Literal["USER", "DEFAULT"]


class AgentTurnDecision(Contract):
    action: AgentPlannerAction
    intent: IntentFrame
    execution: ExecutionDecision
    answer_contract: AnswerContract | None = None
    tool_plan: tuple[PlannedToolCall, ...] = Field(default=(), max_length=8)
    direct_response: str | None = Field(default=None, max_length=32_000)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action == AgentPlannerAction.RESPOND:
            if self.execution.execution_level != AgentExecutionLevel.INLINE_CHAT:
                raise ValueError("a direct response must use INLINE_CHAT")
            if not self.direct_response or self.tool_plan or self.answer_contract is not None:
                raise ValueError("a direct response cannot include tools or an answer contract")
        elif self.action == AgentPlannerAction.CLARIFY:
            if not self.intent.missing_information:
                raise ValueError("a clarification requires missing information")
            if not self.direct_response or self.tool_plan:
                raise ValueError("a clarification requires one response and no tools")
        else:
            if self.direct_response is not None:
                raise ValueError("an execution decision cannot include a direct response")
            if not self.tool_plan:
                raise ValueError("an execution decision requires at least one tool")
            if (
                self.execution.execution_level != AgentExecutionLevel.INLINE_CHAT
                and self.answer_contract is None
            ):
                raise ValueError("a grounded execution decision requires an answer contract")
        if self.execution.execution_level == AgentExecutionLevel.INLINE_CHAT:
            disallowed = {
                AgentInformationSource.WEB,
                AgentInformationSource.RAG,
                AgentInformationSource.STRUCTURED_DATA,
            }
            if set(self.execution.source_plan.required_sources) & disallowed:
                raise ValueError("INLINE_CHAT cannot require external information sources")
        if self.intent.temporal.requires_fresh_information:
            sources = {
                *self.execution.source_plan.required_sources,
                *self.execution.source_plan.preferred_sources,
            }
            if not sources & {
                AgentInformationSource.WEB,
                AgentInformationSource.STRUCTURED_DATA,
            }:
                raise ValueError("fresh information requires Web or structured data")
        return self


class AgentRecoveryAction(StrEnum):
    RETRY = "RETRY"
    REPAIR_ANSWER = "REPAIR_ANSWER"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    REPLAN = "REPLAN"
    PARTIAL_ACCEPT = "PARTIAL_ACCEPT"
    CLARIFY = "CLARIFY"
    UNAVAILABLE = "UNAVAILABLE"
    FAIL = "FAIL"


class AgentRecoveryDecision(Contract):
    action: AgentRecoveryAction
    reason: str = Field(min_length=1, max_length=1000)
    failed_node: str = Field(min_length=1, max_length=200)
    prohibited_repetitions: tuple[str, ...] = Field(default=(), max_length=12)
    remaining_attempts: int = Field(ge=0, le=3)


def validate_entity_aliases(scope: tuple[str, ...], aliases: dict[str, tuple[str, ...]]) -> None:
    """Aliases are bounded discovery hints, never proof of an entity match."""
    if len(aliases) > 6 or any(key not in scope for key in aliases):
        raise ValueError("entity aliases must refer to declared canonical scope")
    if any(not values or len(values) > 3 or any(not value.strip() or len(value) > 200 for value in values)
           for values in aliases.values()):
        raise ValueError("each entity allows 1-3 complete aliases of 1-200 characters")


class BoundedResearchPlan(Contract):
    """A non-executable, bounded plan for retrieval and grounded answer composition."""

    entity_scope: tuple[str, ...] = Field(default=(), max_length=6)
    entity_aliases: dict[str, tuple[str, ...]] = Field(default_factory=dict, max_length=6)

    web_queries: tuple[str, ...] = Field(default=(), max_length=5)
    retrieval_queries: tuple[str, ...] = Field(default=(), max_length=3)
    source_plan: SourcePlan
    result_limit: int = Field(ge=1, le=5)
    answer_contract: AnswerContract

    @model_validator(mode="after")
    def validate_queries(self) -> Self:
        validate_entity_aliases(self.entity_scope, self.entity_aliases)
        if any(not item.strip() or len(item) > 200 for item in self.entity_scope):
            raise ValueError("entity scope entries must contain 1-200 characters")
        queries = (*self.web_queries, *self.retrieval_queries)
        normalized = tuple(item.strip() for item in queries)
        if not normalized:
            raise ValueError("bounded research requires at least one query")
        if any(not item or len(item) > 1000 for item in normalized):
            raise ValueError("research queries must contain 1-1000 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("research queries must be unique")
        if self.answer_contract.minimum_evidence_items > self.result_limit:
            raise ValueError("Evidence minimum cannot exceed the bounded result limit")
        return self


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


class ContractVersion(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    supersedes_id: UUID | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    goal: str = Field(min_length=1)
    constraints: tuple[str, ...] = ()
    completion_policy: dict[str, JsonValue] = {}
    created_by: UUID
    created_at: AwareDatetime


class Requirement(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    contract_version_id: UUID
    key: str = Field(min_length=1, max_length=200)
    type: RequirementType
    mandatory: bool
    description: str = Field(min_length=1)
    completion_rule: str = Field(min_length=1)
    entity_scope: tuple[str, ...] = ()
    temporal_scope: tuple[str, ...] = ()
    freshness_policy: dict[str, JsonValue] = {}
    evidence_policy: dict[str, JsonValue] = {}
    delivery_policy: dict[str, JsonValue] = {}
    legacy: bool = False


class RequirementSupport(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    requirement_id: UUID
    plan_revision_id: UUID
    step_id: UUID
    relation_type: RequirementRelationType
    created_at: AwareDatetime


class VerificationGates(Contract):
    entity_match: bool
    temporal_match: bool
    evidence_support: bool
    authorization: bool
    operation_certainty: Literal["CERTAIN", "UNKNOWN", "CONFLICTING"]
    schema_validity: bool


class VerificationMetrics(Contract):
    completeness: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    source_quality: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    redundancy: float = Field(ge=0, le=1)


class VerificationResult(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    claim_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    gates: VerificationGates
    metrics: VerificationMetrics
    verifier_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    attempt_id: UUID | None = None
    fencing_token: int | None = Field(default=None, ge=0)


class RequirementAssessment(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    requirement_id: UUID
    status: AssessmentStatus
    gap_type: GapType | None = None
    supporting_claim_ids: tuple[UUID, ...] = ()
    supporting_evidence_ids: tuple[UUID, ...] = ()
    verification_ref: UUID | None = None
    evaluator_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessed_at: AwareDatetime
    supersedes_id: UUID | None = None
    attempt_id: UUID | None = None
    fencing_token: int | None = Field(default=None, ge=0)


class Gap(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    requirement_id: UUID | None = None
    gap_type: GapType
    recovery_action: RecoveryAction
    description: str = Field(min_length=1)
    assessment_ref: UUID | None = None
    created_at: AwareDatetime


class DeliveryDecision(Contract):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    completion_snapshot_id: UUID
    decision: DeliveryDecisionType
    included_claim_ids: tuple[UUID, ...] = ()
    excluded_claim_ids: tuple[UUID, ...] = ()
    unresolved_requirement_ids: tuple[UUID, ...] = ()
    reason_codes: tuple[str, ...] = ()
    disclosure: str = ""
    policy_version: str = Field(min_length=1)
    verification_ref: UUID | None = None
    created_at: AwareDatetime
    supersedes_id: UUID | None = None


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


class ConversationTurnState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class ConversationTurnAttemptState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class ConversationTurnOutcomeStatus(StrEnum):
    ANSWERED = "ANSWERED"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ConversationTurn(Contract):
    turn_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    state: ConversationTurnState
    task_id: UUID | None = None
    active_attempt_id: UUID | None = None
    decision_version: int = Field(gt=0)
    fencing_token: int = Field(ge=0)
    context_eligible: bool
    version: int = Field(gt=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class ConversationTurnAttempt(Contract):
    attempt_id: UUID
    workspace_id: UUID
    turn_id: UUID
    state: ConversationTurnAttemptState
    fencing_token: int = Field(gt=0)
    trace_id: str = Field(min_length=1, max_length=200)
    failure: Failure | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class ConversationTurnOutcome(Contract):
    outcome_id: UUID
    workspace_id: UUID
    turn_id: UUID
    attempt_id: UUID
    status: ConversationTurnOutcomeStatus
    assistant_message_id: UUID
    failure: Failure | None = None
    context_eligible: bool
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_failure_semantics(self) -> Self:
        failed = self.status in {
            ConversationTurnOutcomeStatus.UNAVAILABLE,
            ConversationTurnOutcomeStatus.FAILED,
        }
        if failed != (self.failure is not None):
            raise ValueError("turn outcome failure does not match status")
        return self


class ConversationTurnProjection(Contract):
    turn: ConversationTurn
    user_message: ConversationMessage
    outcome: ConversationTurnOutcome
    assistant_message: ConversationMessage


class ConversationAttachment(Contract):
    """Presentation-safe reference to the document bound to one user Turn."""

    ingestion_id: UUID
    document_version_id: UUID
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)


class ConversationTimelineMessage(ConversationMessage):
    """A presentation-only message projection bound to its authoritative Turn."""

    turn_id: UUID | None = None
    turn_state: ConversationTurnState | None = None
    turn_outcome_status: ConversationTurnOutcomeStatus | None = None
    attachment: ConversationAttachment | None = None


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
