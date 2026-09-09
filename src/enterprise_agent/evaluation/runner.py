import json
from pathlib import Path

from enterprise_agent.evaluation.contracts import (
    AblationPair,
    EvaluationMetrics,
    EvaluationObservation,
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
    values = tuple(AblationPair.model_validate(item) for item in json.loads(path.read_text("utf-8")))
    if len(values) != 6 or len({item.ablation_id for item in values}) != 6:
        raise ValueError("the frozen evaluation suite requires six unique ablation pairs")
    return values


def _boolean_rate(values: list[bool]) -> float | None:
    return None if not values else sum(values) / len(values)


def _ratio(numerators: list[int], denominators: list[int]) -> float | None:
    denominator = sum(denominators)
    return None if denominator == 0 else sum(numerators) / denominator


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
    policy_values = [item.policy_violations for item in observed if item.policy_violations is not None]
    latencies = [item.latency_ms for item in observed if item.latency_ms is not None]
    tokens = [item.tokens for item in observed if item.tokens is not None]
    costs = [item.cost for item in observed if item.cost is not None]
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
        total_tokens=None if not tokens else sum(tokens),
        total_cost=None if not costs else sum(costs),
    )
