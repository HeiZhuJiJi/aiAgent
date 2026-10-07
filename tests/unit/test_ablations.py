from pathlib import Path

from enterprise_agent.evaluation.ablation import run_ablations


def test_all_six_controlled_ablation_pairs_execute_without_runtime_flags() -> None:
    report = run_ablations(Path("evaluation/ablations.json"))
    results = report["results"]
    assert len(results) == 6
    assert {item["status"] for item in results} == {"COMPLETED"}
    assert {item["ablation_id"] for item in results} == {
        "planner",
        "replan",
        "context",
        "reviewer",
        "capability-retrieval",
        "research-execution",
    }
    assert all(item["primary_metrics"] for item in results)
    assert report["production_provider_validated"] is False
