from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from enterprise_agent.contracts.domain import Contract

EvaluationCategory = Literal[
    "KNOWLEDGE_QA",
    "COMPLEX_RESEARCH",
    "SKILL",
    "MCP",
    "PLANNER",
    "REPLAN",
    "PERMISSION",
    "HITL",
    "FAILURE",
    "RECOVERY",
    "SIDE_EFFECT",
    "CONTEXT",
]


class GoldenExpectation(Contract):
    task_success: bool
    plan_valid: bool
    capability_ids: tuple[str, ...]
    citation_required: bool
    expected_decision: str | None = None
    expected_replan: bool = False
    expected_hitl: bool = False
    expected_side_effect_count: int = Field(default=0, ge=0)


class GoldenTask(Contract):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,99}$")
    category: EvaluationCategory
    title: str = Field(min_length=5, max_length=200)
    objective: str = Field(min_length=10, max_length=1000)
    input: dict[str, JsonValue]
    expected: GoldenExpectation
    tags: tuple[str, ...] = ()


class EvaluationObservation(Contract):
    case_id: str
    status: Literal["PASSED", "FAILED", "NOT_RUN"]
    task_success: bool | None = None
    plan_valid: bool | None = None
    selected_capability_ids: tuple[str, ...] | None = None
    citations_correct: int | None = Field(default=None, ge=0)
    citations_total: int | None = Field(default=None, ge=0)
    evidence_covered: int | None = Field(default=None, ge=0)
    evidence_required: int | None = Field(default=None, ge=0)
    claims_grounded: int | None = Field(default=None, ge=0)
    claims_total: int | None = Field(default=None, ge=0)
    recovery_success: bool | None = None
    duplicate_side_effects: int | None = Field(default=None, ge=0)
    side_effects_total: int | None = Field(default=None, ge=0)
    policy_violations: int | None = Field(default=None, ge=0)
    hitl_correct: bool | None = None
    replan_success: bool | None = None
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tool_call_count: int | None = Field(default=None, ge=0)
    replan_count: int | None = Field(default=None, ge=0)
    hitl_count: int | None = Field(default=None, ge=0)
    failure_recovery_count: int | None = Field(default=None, ge=0)
    evidence_ref: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_executed_observation(self) -> Self:
        measured = (
            self.task_success,
            self.plan_valid,
            self.selected_capability_ids,
            self.latency_ms,
            self.tokens,
            self.cost,
            self.tool_call_count,
            self.replan_count,
            self.hitl_count,
            self.failure_recovery_count,
        )
        if self.status == "NOT_RUN" and any(value is not None for value in measured):
            raise ValueError("NOT_RUN observations cannot contain measured execution values")
        if self.status != "NOT_RUN" and not self.evidence_ref:
            raise ValueError("executed observations require a reproducible evidence_ref")
        for numerator, denominator, label in (
            (self.citations_correct, self.citations_total, "citations"),
            (self.evidence_covered, self.evidence_required, "evidence"),
            (self.claims_grounded, self.claims_total, "claims"),
            (self.duplicate_side_effects, self.side_effects_total, "side effects"),
        ):
            if (numerator is None) != (denominator is None):
                raise ValueError(f"{label} numerator and denominator must be provided together")
            if numerator is not None and denominator is not None and numerator > denominator:
                raise ValueError(f"{label} numerator cannot exceed denominator")
        return self


class EvaluationMetrics(Contract):
    total_cases: int = Field(gt=0)
    evaluated_cases: int = Field(ge=0)
    evaluated_case_rate: float = Field(ge=0, le=1)
    task_success_rate: float | None = Field(default=None, ge=0, le=1)
    plan_valid_rate: float | None = Field(default=None, ge=0, le=1)
    capability_selection_accuracy: float | None = Field(default=None, ge=0, le=1)
    citation_correctness: float | None = Field(default=None, ge=0, le=1)
    evidence_coverage: float | None = Field(default=None, ge=0, le=1)
    claim_grounding_rate: float | None = Field(default=None, ge=0, le=1)
    claim_provenance_correctness: float | None = Field(default=None, ge=0, le=1)
    tool_success_rate: float | None = Field(default=None, ge=0, le=1)
    retry_rate: float | None = Field(default=None, ge=0, le=1)
    recovery_success_rate: float | None = Field(default=None, ge=0, le=1)
    duplicate_side_effect_rate: float | None = Field(default=None, ge=0, le=1)
    policy_violation_rate: float | None = Field(default=None, ge=0, le=1)
    hitl_accuracy: float | None = Field(default=None, ge=0, le=1)
    replan_success_rate: float | None = Field(default=None, ge=0, le=1)
    mean_latency_ms: float | None = Field(default=None, ge=0)
    p50_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    max_latency_ms: float | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    total_cost: float | None = Field(default=None, ge=0)
    total_tool_calls: int | None = Field(default=None, ge=0)
    total_tool_executions: int | None = Field(default=None, ge=0)
    total_tool_succeeded: int | None = Field(default=None, ge=0)
    total_tool_denied: int | None = Field(default=None, ge=0)
    total_step_attempts: int | None = Field(default=None, ge=0)
    total_steps_attempted: int | None = Field(default=None, ge=0)
    total_retried_steps: int | None = Field(default=None, ge=0)
    total_retries: int | None = Field(default=None, ge=0)
    total_replans: int | None = Field(default=None, ge=0)
    total_replans_applied: int | None = Field(default=None, ge=0)
    total_replans_aborted: int | None = Field(default=None, ge=0)
    total_hitl: int | None = Field(default=None, ge=0)
    total_hitl_approved: int | None = Field(default=None, ge=0)
    total_hitl_rejected: int | None = Field(default=None, ge=0)
    total_hitl_expired: int | None = Field(default=None, ge=0)
    total_failure_recoveries: int | None = Field(default=None, ge=0)


class EvaluationOperationalTotals(Contract):
    tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False)
    tool_calls: int = Field(ge=0)
    replans: int = Field(ge=0)
    hitl: int = Field(ge=0)
    failure_recoveries: int = Field(ge=0)
    evidence_covered: int | None = Field(default=None, ge=0)
    evidence_required: int | None = Field(default=None, ge=0)
    claims_provenance_correct: int | None = Field(default=None, ge=0)
    claims_total: int | None = Field(default=None, ge=0)
    tool_executions: int | None = Field(default=None, ge=0)
    tool_succeeded: int | None = Field(default=None, ge=0)
    tool_denied: int | None = Field(default=None, ge=0)
    step_attempts: int | None = Field(default=None, ge=0)
    steps_attempted: int | None = Field(default=None, ge=0)
    retried_steps: int | None = Field(default=None, ge=0)
    retries: int | None = Field(default=None, ge=0)
    replans_applied: int | None = Field(default=None, ge=0)
    replans_aborted: int | None = Field(default=None, ge=0)
    hitl_approved: int | None = Field(default=None, ge=0)
    hitl_rejected: int | None = Field(default=None, ge=0)
    hitl_expired: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_record_denominators(self) -> Self:
        for numerator, denominator, label in (
            (self.evidence_covered, self.evidence_required, "evidence"),
            (self.claims_provenance_correct, self.claims_total, "claim provenance"),
            (self.tool_succeeded, self.tool_executions, "tool executions"),
            (self.retried_steps, self.steps_attempted, "retried steps"),
        ):
            if (numerator is None) != (denominator is None):
                raise ValueError(f"{label} numerator and denominator must be provided together")
            if numerator is not None and denominator is not None and numerator > denominator:
                raise ValueError(f"{label} numerator cannot exceed denominator")
        if (
            self.step_attempts is not None
            and self.steps_attempted is not None
            and self.step_attempts < self.steps_attempted
        ):
            raise ValueError("step attempts cannot be lower than distinct attempted steps")
        return self


class EvaluationBinding(Contract):
    case_id: str
    pytest_nodes: tuple[str, ...] = Field(min_length=1)


class EvaluationExecutionManifest(Contract):
    schema_version: Literal[1] = 1
    suite_version: str = Field(min_length=1, max_length=100)
    evidence_mode: Literal["CONTROLLED_ACCEPTANCE"] = "CONTROLLED_ACCEPTANCE"
    bindings: tuple[EvaluationBinding, ...] = Field(min_length=60, max_length=80)


class AblationPair(Contract):
    ablation_id: str = Field(pattern=r"^[a-z0-9-]+$")
    treatment: str
    control: str
    feature_flag: str
    primary_metrics: tuple[str, ...]


class EvaluationRunCreate(Contract):
    workspace_id: UUID
    label: str = Field(min_length=1, max_length=200)
    observations: tuple[EvaluationObservation, ...] = Field(min_length=60, max_length=80)


class EvaluationRunView(Contract):
    run_id: UUID
    workspace_id: UUID
    label: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["COMPLETED", "FAILED"]
    metrics: EvaluationMetrics
    created_at: datetime
    completed_at: datetime
