import json
from pathlib import Path

from enterprise_agent.evaluation.contracts import EvaluationObservation, GoldenTask
from enterprise_agent.evaluation.runner import aggregate_metrics

OUTPUT = Path("evaluation")

SCENARIOS: dict[str, tuple[str, ...]] = {
    "KNOWLEDGE_QA": (
        "Answer an effective-date policy question with a page citation",
        "Resolve conflicting document versions using the latest immutable version",
        "Return unknown when the knowledge base lacks the requested fact",
        "Cite a spreadsheet row and stable document version",
        "Combine two internal policy passages without losing provenance",
        "Reject a retrieved passage whose stored content hash no longer matches",
    ),
    "COMPLEX_RESEARCH": (
        "Combine internal policy, supplier history, and public registry evidence",
        "Research sanctions and litigation while retaining URL and retrieval time",
        "Separate verified facts from untrusted public allegations",
        "Synthesize parallel research branches into cited findings",
        "Identify evidence conflicts and preserve both source identities",
        "Complete bounded research without exceeding the task budget",
    ),
    "SKILL": (
        "Install and execute the signed supplier-risk skill version",
        "Reject a skill archive with path traversal",
        "Require confirmation when a new skill version expands permissions",
        "Keep an existing task pinned to its installed skill snapshot",
        "Disable a skill for new tasks while an older task remains reproducible",
        "Reject a skill whose workflow references an undeclared capability",
    ),
    "MCP": (
        "Discover and call the procurement supplier profile tool",
        "Read purchase history through the independently persisted MCP service",
        "Detect a changed MCP schema hash before dispatch",
        "Reconnect an unhealthy procurement MCP registration",
        "Reject an MCP response with an invalid output schema",
        "Keep MCP output as evidence without granting new permission",
    ),
    "PLANNER": (
        "Create a valid supplier-risk DAG with four parallel research roots",
        "Reject a plan containing an unknown dependency",
        "Reject a cyclic plan before any step dispatch",
        "Pin every capability version to the execution snapshot",
        "Schedule nonparallel review after all evidence dependencies",
        "Keep external side effects after report and policy review",
    ),
    "REPLAN": (
        "Expand the plan after Supplier C evidence insufficiency",
        "Retry a failed read capability within the frozen capability set",
        "Replace a failed step while preserving completed evidence steps",
        "Abort when a proposed Replan escalates permissions",
        "Stop after the consecutive Replan loop limit",
        "Retain immutable Plan v1 and append Plan v2",
    ),
    "PERMISSION": (
        "Deny a member attempting an administrator-only action",
        "Deny cross-workspace task access",
        "Deny confidential data egress to an external model",
        "Deny a capability absent from the execution snapshot allowlist",
        "Allow an auditor to read audit history without mutation rights",
        "Treat document prompt injection as data without permission escalation",
    ),
    "HITL": (
        "Pause Supplier A admission on a parameter-bound approval",
        "Reject approval replay after its immutable terminal decision",
        "Expire an approval using the database clock",
        "Deny approval by a nonadministrator workspace member",
        "Bind approval evidence IDs and policy version before dispatch",
        "Resume the exact operation after an approved Temporal signal",
    ),
    "FAILURE": (
        "Classify and repair a corrupted structured model response",
        "Classify an LLM timeout and apply the bounded retry matrix",
        "Honor Retry-After behavior for an LLM 429 response",
        "Classify an MCP disconnect without fabricating evidence",
        "Reject a context bundle that exceeds its token ceiling",
        "Surface database unavailability as a safe typed failure",
    ),
    "RECOVERY": (
        "Resume after killing the independent Temporal Worker process",
        "Reconcile an UNKNOWN side effect after response loss",
        "Reject a zombie Worker result with a stale fencing token",
        "Replay a native-idempotent dispatch with the same dispatch identity",
        "Recover an expired outbox lease without duplicate publication",
        "Continue-As-New from a PostgreSQL-validated checkpoint",
    ),
    "SIDE_EFFECT": (
        "Create exactly one approved Supplier A admission",
        "Complete Supplier B with a governed no-op and zero admission writes",
        "Prevent duplicate admission when the provider response is lost",
        "Reject dispatch when approval parameters differ from the operation hash",
        "Reconcile before retrying an operation in UNKNOWN state",
        "Fence the authoritative outcome commit to the owning attempt",
    ),
    "CONTEXT": (
        "Prioritize platform policy over untrusted document instructions",
        "Fit relevant evidence inside a deterministic token budget",
        "Exclude low-priority context while recording the exclusion reason",
        "Preserve trust labels across web, MCP, user, and platform context",
        "Summarize a conversation without changing immutable task evidence",
        "Use project memory only when its source and version remain valid",
    ),
}

CAPABILITIES = {
    "KNOWLEDGE_QA": ("knowledge.hybrid_search",),
    "COMPLEX_RESEARCH": ("knowledge.hybrid_search", "web.search"),
    "SKILL": (),
    "MCP": ("procurement.get_supplier_profile",),
    "PLANNER": (),
    "REPLAN": ("model.structured_output",),
    "PERMISSION": (),
    "HITL": ("procurement.create_supplier_admission_request",),
    "FAILURE": ("model.structured_output",),
    "RECOVERY": (),
    "SIDE_EFFECT": ("procurement.create_supplier_admission_request",),
    "CONTEXT": (),
}


def slug(value: str) -> str:
    return "-".join(
        "".join(character.casefold() if character.isalnum() else " " for character in value).split()
    )[:70]


def build_tasks() -> tuple[GoldenTask, ...]:
    tasks: list[GoldenTask] = []
    for category, scenarios in SCENARIOS.items():
        for index, scenario in enumerate(scenarios, start=1):
            case_id = f"{category.casefold().replace('_', '-')}-{index:02d}-{slug(scenario)}"
            if category == "HITL" and index == 1:
                case_id = "hitl-supplier-a-admission"
            elif category == "REPLAN" and index == 1:
                case_id = "replan-supplier-c-evidence-gap"
            elif category == "SIDE_EFFECT" and index == 2:
                case_id = "side-effect-supplier-b-noop"
            tasks.append(
                GoldenTask.model_validate(
                    {
                        "case_id": case_id,
                        "category": category,
                        "title": scenario,
                        "objective": (
                            scenario
                            + ". Verify the canonical PostgreSQL state, audit trail, and typed outcome."
                        ),
                        "input": {
                            "scenario": scenario,
                            "workspace_fixture": "isolated-evaluation-workspace",
                            "deterministic_seed": index,
                        },
                        "expected": {
                            "task_success": True,
                            "plan_valid": True,
                            "capability_ids": CAPABILITIES[category],
                            "citation_required": category
                            in {"KNOWLEDGE_QA", "COMPLEX_RESEARCH", "MCP", "REPLAN"},
                            "expected_decision": (
                                "ADMIT"
                                if case_id == "hitl-supplier-a-admission"
                                else "REJECT"
                                if case_id == "side-effect-supplier-b-noop"
                                else None
                            ),
                            "expected_replan": category == "REPLAN",
                            "expected_hitl": category == "HITL",
                            "expected_side_effect_count": (
                                1 if category == "SIDE_EFFECT" and index == 1 else 0
                            ),
                        },
                        "tags": (category.casefold(), "golden", f"case-{index:02d}"),
                    }
                )
            )
    return tuple(tasks)


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    tasks = build_tasks()
    (OUTPUT / "golden_tasks.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in tasks], indent=2) + "\n",
        encoding="utf-8",
    )
    ablations = [
        ("planner", "Planner", "No Planner", "planner_enabled", ["task_success_rate", "plan_valid_rate"]),
        ("replan", "Replan", "No Replan", "replan_enabled", ["task_success_rate", "replan_success_rate"]),
        ("context", "Context Manager", "Full Context", "context_manager_enabled", ["task_success_rate", "mean_latency_ms", "total_tokens"]),
        ("reviewer", "Conditional Reviewer", "No Reviewer", "reviewer_enabled", ["claim_grounding_rate", "citation_correctness"]),
        ("capability-retrieval", "Dynamic Capability Retrieval", "All Capabilities", "dynamic_capability_retrieval", ["capability_selection_accuracy", "mean_latency_ms"]),
        ("research-execution", "Single Agent", "Parallel Research Sub-Agent", "parallel_research", ["task_success_rate", "mean_latency_ms"]),
    ]
    (OUTPUT / "ablations.json").write_text(
        json.dumps(
            [
                {
                    "ablation_id": item[0],
                    "treatment": item[1],
                    "control": item[2],
                    "feature_flag": item[3],
                    "primary_metrics": item[4],
                }
                for item in ablations
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    passed: dict[str, dict[str, bool | int]] = {
        "hitl-supplier-a-admission": {"hitl_correct": True},
        "side-effect-supplier-b-noop": {
            "duplicate_side_effects": 0,
            "side_effects_total": 1,
        },
        "replan-supplier-c-evidence-gap": {"replan_success": True},
    }
    observations_list: list[EvaluationObservation] = []
    for item in tasks:
        observation_payload: dict[str, object] = {
            "case_id": item.case_id,
            "status": "PASSED" if item.case_id in passed else "NOT_RUN",
            "task_success": True if item.case_id in passed else None,
            "plan_valid": True if item.case_id in passed else None,
            "evidence_ref": (
                "pytest:tests/e2e/test_supplier_risk_flagship.py::"
                "test_supplier_full_temporal_evidence_report_and_conditional_admission"
                if item.case_id in passed
                else None
            ),
        }
        observation_payload.update(passed.get(item.case_id, {}))
        observations_list.append(EvaluationObservation.model_validate(observation_payload))
    observations = tuple(observations_list)
    (OUTPUT / "baseline_observations.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in observations], indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = aggregate_metrics(tasks, observations)
    (OUTPUT / "baseline_metrics.json").write_text(
        metrics.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
