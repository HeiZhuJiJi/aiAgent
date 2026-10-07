import json
from pathlib import Path

from enterprise_agent.evaluation.contracts import (
    AblationPair,
    EvaluationExecutionManifest,
    EvaluationMetrics,
    EvaluationObservation,
    EvaluationOperationalTotals,
    GoldenTask,
)


def load_golden_tasks(path: Path) -> tuple[GoldenTask, ...]:
    values = tuple(GoldenTask.model_validate(item) for item in json.loads(path.read_text("utf-8")))
    if not 60 <= len(values) <= 80:
        raise ValueError("the frozen evaluation suite requires 60 to 80 Golden Tasks")
    if len({item.case_id for item in values}) != len(values):
        raise ValueError("Golden Task case_id values must be unique")
    return values


def load_ablation_pairs(path: Path) -> tuple[AblationPair, ...]:
    values = tuple(
        AblationPair.model_validate(item) for item in json.loads(path.read_text("utf-8"))
    )
    if len(values) != 6 or len({item.ablation_id for item in values}) != 6:
        raise ValueError("the frozen evaluation suite requires six unique ablation pairs")
    return values


def load_execution_manifest(
    path: Path, cases: tuple[GoldenTask, ...]
) -> EvaluationExecutionManifest:
    manifest = EvaluationExecutionManifest.model_validate(json.loads(path.read_text("utf-8")))
    case_ids = {item.case_id for item in cases}
    binding_ids = [item.case_id for item in manifest.bindings]
    if len(set(binding_ids)) != len(binding_ids):
        raise ValueError("the evaluation execution manifest contains duplicate case bindings")
    if set(binding_ids) != case_ids:
        missing = sorted(case_ids - set(binding_ids))
        unknown = sorted(set(binding_ids) - case_ids)
        raise ValueError(
            f"execution manifest coverage mismatch: missing={missing}, unknown={unknown}"
        )
    invalid = sorted(
        node
        for binding in manifest.bindings
        for node in binding.pytest_nodes
        if not node.startswith("tests/") or "::test_" not in node
    )
    if invalid:
        raise ValueError(f"execution manifest contains invalid pytest nodes: {invalid}")
    return manifest


def _boolean_rate(values: list[bool]) -> float | None:
    return None if not values else sum(values) / len(values)


def _ratio(numerators: list[int], denominators: list[int]) -> float | None:
    denominator = sum(denominators)
    return None if denominator == 0 else sum(numerators) / denominator


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(percentile * len(ordered) + 0.999999) - 1))
    return ordered[index]


def apply_operational_totals(
    metrics: EvaluationMetrics, totals: EvaluationOperationalTotals
) -> EvaluationMetrics:
    def ratio(numerator: int | None, denominator: int | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator

    return metrics.model_copy(
        update={
            "evidence_coverage": ratio(totals.evidence_covered, totals.evidence_required),
            "claim_provenance_correctness": ratio(
                totals.claims_provenance_correct, totals.claims_total
            ),
            "tool_success_rate": ratio(totals.tool_succeeded, totals.tool_executions),
            "retry_rate": ratio(totals.retried_steps, totals.steps_attempted),
            "total_tokens": totals.tokens,
            "total_cost": totals.cost,
            "total_tool_calls": totals.tool_calls,
            "total_tool_executions": totals.tool_executions,
            "total_tool_succeeded": totals.tool_succeeded,
            "total_tool_denied": totals.tool_denied,
            "total_step_attempts": totals.step_attempts,
            "total_steps_attempted": totals.steps_attempted,
            "total_retried_steps": totals.retried_steps,
            "total_retries": totals.retries,
            "total_replans": totals.replans,
            "total_replans_applied": totals.replans_applied,
            "total_replans_aborted": totals.replans_aborted,
            "total_hitl": totals.hitl,
            "total_hitl_approved": totals.hitl_approved,
            "total_hitl_rejected": totals.hitl_rejected,
            "total_hitl_expired": totals.hitl_expired,
            "total_failure_recoveries": totals.failure_recoveries,
        }
    )


def aggregate_metrics(
    cases: tuple[GoldenTask, ...], observations: tuple[EvaluationObservation, ...]
) -> EvaluationMetrics:
    by_id = {item.case_id: item for item in cases}
    if len({item.case_id for item in observations}) != len(observations):
        raise ValueError("an evaluation run cannot contain duplicate case observations")
    unknown = {item.case_id for item in observations} - set(by_id)
    if unknown:
        raise ValueError("observations reference unknown Golden Tasks")
    observed = [item for item in observations if item.status != "NOT_RUN"]

    def booleans(name: str) -> list[bool]:
        return [value for item in observed if (value := getattr(item, name)) is not None]

    capability_matches = [
        set(item.selected_capability_ids or ()) == set(by_id[item.case_id].expected.capability_ids)
        for item in observed
        if item.selected_capability_ids is not None
    ]
    citations = [
        (item.citations_correct, item.citations_total)
        for item in observed
        if item.citations_correct is not None and item.citations_total is not None
    ]
    evidence = [
        (item.evidence_covered, item.evidence_required)
        for item in observed
        if item.evidence_covered is not None and item.evidence_required is not None
    ]
    claims = [
        (item.claims_grounded, item.claims_total)
        for item in observed
        if item.claims_grounded is not None and item.claims_total is not None
    ]
    effects = [
        (item.duplicate_side_effects, item.side_effects_total)
        for item in observed
        if item.duplicate_side_effects is not None and item.side_effects_total is not None
    ]
    policy_values = [
        item.policy_violations for item in observed if item.policy_violations is not None
    ]
    latencies = [item.latency_ms for item in observed if item.latency_ms is not None]
    tokens = [item.tokens for item in observed if item.tokens is not None]
    costs = [item.cost for item in observed if item.cost is not None]
    tool_calls = [item.tool_call_count for item in observed if item.tool_call_count is not None]
    replans = [item.replan_count for item in observed if item.replan_count is not None]
    hitl = [item.hitl_count for item in observed if item.hitl_count is not None]
    recoveries = [
        item.failure_recovery_count for item in observed if item.failure_recovery_count is not None
    ]
    return EvaluationMetrics(
        total_cases=len(cases),
        evaluated_cases=len(observed),
        evaluated_case_rate=len(observed) / len(cases),
        task_success_rate=_boolean_rate(booleans("task_success")),
        plan_valid_rate=_boolean_rate(booleans("plan_valid")),
        capability_selection_accuracy=_boolean_rate(capability_matches),
        citation_correctness=_ratio(
            [int(item[0]) for item in citations], [int(item[1]) for item in citations]
        ),
        evidence_coverage=_ratio(
            [int(item[0]) for item in evidence], [int(item[1]) for item in evidence]
        ),
        claim_grounding_rate=_ratio(
            [int(item[0]) for item in claims], [int(item[1]) for item in claims]
        ),
        recovery_success_rate=_boolean_rate(booleans("recovery_success")),
        duplicate_side_effect_rate=_ratio(
            [int(item[0]) for item in effects], [int(item[1]) for item in effects]
        ),
        policy_violation_rate=(
            None if not policy_values else sum(policy_values) / len(policy_values)
        ),
        hitl_accuracy=_boolean_rate(booleans("hitl_correct")),
        replan_success_rate=_boolean_rate(booleans("replan_success")),
        mean_latency_ms=None if not latencies else sum(latencies) / len(latencies),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        max_latency_ms=None if not latencies else max(latencies),
        total_tokens=None if not tokens else sum(tokens),
        total_cost=None if not costs else sum(costs),
        total_tool_calls=None if not tool_calls else sum(tool_calls),
        total_replans=None if not replans else sum(replans),
        total_hitl=None if not hitl else sum(hitl),
        total_failure_recoveries=None if not recoveries else sum(recoveries),
    )
