import json
from pathlib import Path

from enterprise_agent.evaluation.contracts import EvaluationMetrics
from enterprise_agent.evaluation.runner import (
    load_ablation_pairs,
    load_execution_manifest,
    load_golden_tasks,
)


def test_versioned_evaluation_gate_assets_are_complete_and_truthful() -> None:
    cases = load_golden_tasks(Path("evaluation/golden_tasks.json"))
    pairs = load_ablation_pairs(Path("evaluation/ablations.json"))
    manifest = load_execution_manifest(Path("evaluation/execution_manifest.json"), cases)
    metrics = EvaluationMetrics.model_validate_json(
        Path("evaluation/baseline_metrics.json").read_text("utf-8")
    )
    observations = json.loads(
        Path("evaluation/baseline_observations.json").read_text("utf-8")
    )
    assert len(cases) == len(observations) == metrics.total_cases == 72
    assert {case.category for case in cases} == {
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
    }
    assert len(pairs) == 6
    assert len(manifest.bindings) == 72
    assert metrics.evaluated_cases == sum(
        item["status"] != "NOT_RUN" for item in observations
    )
    assert all(
        item["evidence_ref"] is None
        for item in observations
        if item["status"] == "NOT_RUN"
    )
