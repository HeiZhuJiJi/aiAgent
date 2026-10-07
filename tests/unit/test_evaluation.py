import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.evaluation.contracts import (
    EvaluationObservation,
    EvaluationOperationalTotals,
)
from enterprise_agent.evaluation.execution import observations_from_junit, parse_junit
from enterprise_agent.evaluation.runner import (
    aggregate_metrics,
    apply_operational_totals,
    load_ablation_pairs,
    load_execution_manifest,
    load_golden_tasks,
)


def test_golden_suite_has_six_quality_cases_for_every_required_category() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    counts = Counter(item.category for item in tasks)
    assert len(tasks) == 72
    assert set(counts.values()) == {6}
    assert len(counts) == 12
    assert all(item.objective.endswith("typed outcome.") for item in tasks)
    assert all(item.input["workspace_fixture"] == "isolated-evaluation-workspace" for item in tasks)


def test_frozen_six_ablation_pairs_are_versioned() -> None:
    pairs = load_ablation_pairs(Path("evaluation/ablations.json"))
    assert {(item.treatment, item.control) for item in pairs} == {
        ("Planner", "No Planner"),
        ("Replan", "No Replan"),
        ("Context Manager", "Full Context"),
        ("Conditional Reviewer", "No Reviewer"),
        ("Dynamic Capability Retrieval", "All Capabilities"),
        ("Single Agent", "Parallel Research Sub-Agent"),
    }


def test_execution_manifest_binds_every_golden_task_to_acceptance_evidence() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    manifest = load_execution_manifest(Path("evaluation/execution_manifest.json"), tasks)
    assert manifest.evidence_mode == "CONTROLLED_ACCEPTANCE"
    assert len(manifest.bindings) == 72
    assert {item.case_id for item in manifest.bindings} == {item.case_id for item in tasks}


def test_baseline_reports_coverage_and_does_not_score_unrun_cases() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    observations = tuple(
        EvaluationObservation.model_validate(item)
        for item in json.loads(Path("evaluation/baseline_observations.json").read_text("utf-8"))
    )
    metrics = aggregate_metrics(tasks, observations)
    persisted = json.loads(Path("evaluation/baseline_metrics.json").read_text("utf-8"))
    assert metrics.model_dump(mode="json") == persisted
    assert metrics.total_cases == 72
    assert metrics.evaluated_cases == 3
    assert metrics.evaluated_case_rate == pytest.approx(3 / 72)
    assert metrics.capability_selection_accuracy is None
    assert metrics.citation_correctness is None
    assert metrics.total_tokens is None
    assert metrics.total_cost is None


def test_not_run_observation_cannot_smuggle_measured_results() -> None:
    with pytest.raises(ValidationError, match="NOT_RUN observations"):
        EvaluationObservation(case_id="unknown", status="NOT_RUN", task_success=True)


def test_metric_aggregation_rejects_unknown_and_duplicate_cases() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    unknown = EvaluationObservation(
        case_id="not-in-suite", status="PASSED", evidence_ref="pytest:test"
    )
    with pytest.raises(ValueError, match="unknown Golden Tasks"):
        aggregate_metrics(tasks, (unknown,))
    known = EvaluationObservation(
        case_id=tasks[0].case_id, status="PASSED", evidence_ref="pytest:test"
    )
    with pytest.raises(ValueError, match="duplicate case"):
        aggregate_metrics(tasks, (known, known))


def test_junit_parser_builds_reproducible_observation(tmp_path: Path) -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    manifest = load_execution_manifest(Path("evaluation/execution_manifest.json"), tasks)
    first = manifest.bindings[0]
    path, name = first.pytest_nodes[0].split("::", maxsplit=1)
    classname = path.removesuffix(".py").replace("/", ".")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        f'<testsuite><testcase classname="{classname}" name="{name}" time="0.125" /></testsuite>',
        encoding="utf-8",
    )
    parsed = parse_junit(junit)
    assert parsed[first.pytest_nodes[0]] == ("PASSED", 125.0)
    observations = observations_from_junit(tasks, Path("evaluation/execution_manifest.json"), junit)
    assert observations[0].status == "PASSED"
    assert observations[0].latency_ms == 125.0
    assert all(item.status == "NOT_RUN" for item in observations[1:])


def test_metric_aggregation_sums_operational_counts() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    observation = EvaluationObservation(
        case_id=tasks[0].case_id,
        status="PASSED",
        tool_call_count=3,
        replan_count=1,
        hitl_count=2,
        failure_recovery_count=1,
        evidence_ref="pytest:test",
    )
    metrics = aggregate_metrics(tasks, (observation,))
    assert metrics.total_tool_calls == 3
    assert metrics.total_replans == 1
    assert metrics.total_hitl == 2
    assert metrics.total_failure_recoveries == 1


def test_record_metrics_are_merged_with_explicit_denominators() -> None:
    tasks = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    observation = EvaluationObservation(
        case_id=tasks[0].case_id,
        status="PASSED",
        latency_ms=125.0,
        evidence_ref="pytest:test",
    )
    metrics = apply_operational_totals(
        aggregate_metrics(tasks, (observation,)),
        EvaluationOperationalTotals(
            tokens=2298,
            cost=0,
            tool_calls=3,
            replans=2,
            hitl=1,
            failure_recoveries=1,
            evidence_covered=4,
            evidence_required=5,
            claims_provenance_correct=3,
            claims_total=5,
            tool_executions=2,
            tool_succeeded=1,
            tool_denied=1,
            step_attempts=7,
            steps_attempted=5,
            retried_steps=2,
            retries=2,
            replans_applied=1,
            replans_aborted=1,
            hitl_approved=1,
            hitl_rejected=0,
            hitl_expired=0,
        ),
    )
    assert metrics.evidence_coverage == pytest.approx(0.8)
    assert metrics.claim_provenance_correctness == pytest.approx(0.6)
    assert metrics.tool_success_rate == pytest.approx(0.5)
    assert metrics.retry_rate == pytest.approx(0.4)
    assert metrics.p50_latency_ms == 125.0
    assert metrics.total_tokens == 2298
    assert metrics.total_tool_denied == 1
    assert metrics.total_replans_applied == 1
    assert metrics.total_hitl_approved == 1
