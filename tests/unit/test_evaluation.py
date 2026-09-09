import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.evaluation.contracts import EvaluationObservation
from enterprise_agent.evaluation.runner import (
    aggregate_metrics,
    load_ablation_pairs,
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
