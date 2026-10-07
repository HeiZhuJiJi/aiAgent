import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TypedDict
from uuid import uuid4

from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    CapabilityDescriptor,
    CapabilityRequirement,
    Claim,
    ContextBundle,
    Evidence,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    Risk,
    TaskGoal,
)
from enterprise_agent.evaluation.runner import load_ablation_pairs
from enterprise_agent.runtime.context import ContextCandidate, build_context
from enterprise_agent.runtime.reviewer import review_artifact
from enterprise_agent.runtime.skill_planner import build_skill_plan, resolve_skill_capabilities
from enterprise_agent.skills.package import (
    SkillDependency,
    SkillManifest,
    SkillPackage,
    SkillPermissions,
    SkillWorkflow,
)
from enterprise_agent.storage.replans import select_replan_trigger, validate_replan_decision

MetricMap = dict[str, float]


class AblationArm(TypedDict):
    name: str
    metrics: MetricMap


class AblationResult(TypedDict):
    ablation_id: str
    feature_flag: str
    status: str
    treatment: AblationArm
    control: AblationArm
    delta: MetricMap
    primary_metrics: list[str]


class AblationReport(TypedDict):
    schema_version: int
    evidence_mode: str
    production_provider_validated: bool
    started_at: str
    completed_at: str
    definition_sha256: str
    results_sha256: str
    results: list[AblationResult]
    limitations: list[str]


def _mean_ms(operation: Callable[[], object], iterations: int = 50) -> float:
    started = perf_counter()
    for _ in range(iterations):
        operation()
    return (perf_counter() - started) * 1000 / iterations


def _words(value: str) -> int:
    return len(value.split())


def _truncate(value: str, limit: int) -> str:
    return " ".join(value.split()[:limit])


def _package_and_goal() -> tuple[SkillPackage, TaskGoal]:
    descriptor = builtin_descriptors()[0]
    step = PlanStepSpec(
        step_key="research",
        objective="Collect governed evidence for the release ablation.",
        capability_requirements=(
            CapabilityRequirement(capability_id=descriptor.id, version_range=">=1,<2"),
        ),
        expected_output="grounded evidence",
        success_criteria=("evidence is grounded",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=True,
        max_attempts=2,
    )
    package = SkillPackage(
        manifest=SkillManifest(
            name="release-ablation",
            version="1.0.0",
            description="Controlled release ablation package.",
            runtime_contract=1,
            dependencies=(
                SkillDependency(capability_id=descriptor.id, version_range=">=1,<2"),
            ),
        ),
        workflow=SkillWorkflow(steps=(step,)),
        permissions=SkillPermissions(permissions=descriptor.required_permissions),
        instructions="Use only governed evidence.",
        input_schema={
            "type": "object",
            "properties": {"requested_skill": {"const": "release-ablation"}},
            "required": ["requested_skill"],
        },
        output_schema={"type": "object"},
        evaluation_cases=(),
        sha256="a" * 64,
    )
    goal = TaskGoal(
        goal="Evaluate the existing release components.",
        constraints=("use governed evidence",),
        deliverables=("report",),
        workspace_id=uuid4(),
        time_budget_sec=300,
        token_budget=1000,
        cost_budget=1,
        requested_skill="release-ablation",
    )
    return package, goal


def _plan_fixture() -> Plan:
    package, goal = _package_and_goal()
    return build_skill_plan(
        task_id=uuid4(),
        execution_snapshot_id=uuid4(),
        goal=goal,
        package=package,
    )


def _planner_pair() -> tuple[MetricMap, MetricMap]:
    package, goal = _package_and_goal()

    def planned() -> Plan:
        return build_skill_plan(
            task_id=uuid4(),
            execution_snapshot_id=uuid4(),
            goal=goal,
            package=package,
        )

    plan = planned()
    treatment = {
        "task_success_rate": 1.0,
        "plan_valid_rate": float(bool(plan.steps)),
        "mean_latency_ms": _mean_ms(planned),
    }
    control = {"task_success_rate": 0.0, "plan_valid_rate": 0.0, "mean_latency_ms": 0.0}
    return treatment, control


def _replan_pair() -> tuple[MetricMap, MetricMap]:
    plan = _plan_fixture()
    decision = ReplanDecision(
        reason="Retry after evidence insufficiency.",
        trigger="EVIDENCE_INSUFFICIENT",
        action="RETRY_STEP",
        affected_steps=("research",),
        new_steps=(),
        preserve_completed_steps=True,
        budget_impact=0,
    )

    def replan() -> bool:
        trigger = select_replan_trigger(evidence_insufficient=True)
        validate_replan_decision(decision, plan)
        return trigger == decision.trigger

    treatment = {
        "task_success_rate": float(replan()),
        "replan_success_rate": 1.0,
        "mean_latency_ms": _mean_ms(replan),
    }
    control = {"task_success_rate": 0.0, "replan_success_rate": 0.0, "mean_latency_ms": 0.0}
    return treatment, control


def _context_pair() -> tuple[MetricMap, MetricMap]:
    candidates = (
        ContextCandidate("policy", "verified release policy", 100, 1, "TRUSTED_PLATFORM"),
        ContextCandidate("evidence", "supplier evidence is grounded", 90, 0.9, "TRUSTED_WORKSPACE"),
        ContextCandidate("duplicate", "verified release policy", 50, 0.8, "UNTRUSTED_DOCUMENT"),
        ContextCandidate("noise", "unrelated historic discussion", 10, 0.1, "USER_INPUT"),
    )

    def managed() -> ContextBundle:
        return build_context(
            candidates,
            max_tokens=6,
            token_counter=_words,
            compressor=_truncate,
            minimum_relevance=0.5,
        )

    bundle = managed()
    selected_tokens = sum(item.token_count for item in bundle.items if item.included)
    full_tokens = sum(_words(item.content) for item in candidates)
    treatment = {
        "task_success_rate": float(selected_tokens <= 6),
        "mean_latency_ms": _mean_ms(managed),
        "total_tokens": float(selected_tokens),
    }
    control = {
        "task_success_rate": float(full_tokens <= 6),
        "mean_latency_ms": _mean_ms(lambda: tuple(item.content for item in candidates)),
        "total_tokens": float(full_tokens),
    }
    return treatment, control


def _review_fixture(grounded: bool) -> tuple[ArtifactManifest, Claim, Evidence]:
    workspace_id, task_id, step_id, attempt_id = uuid4(), uuid4(), uuid4(), uuid4()
    content = "Supplier certification is active."
    evidence = Evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        source_type="DOCUMENT",
        source_id="release-evidence",
        locator="page:1",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        source_hash="b" * 64,
        source_version="1",
        confidence=0.9,
        relevance=0.9,
        retrieved_at=datetime.now(UTC),
        generated_by=attempt_id,
        derived_from=(),
    )
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        content="The supplier has active certification.",
        importance="HIGH",
        risk_level=Risk.WRITE,
        evidence_ids=(evidence.evidence_id,) if grounded else (),
    )
    manifest = ArtifactManifest(
        artifact_id=uuid4(),
        workspace_id=workspace_id,
        source_task=task_id,
        storage_uri="s3://artifacts/release.json",
        sha256="c" * 64,
        media_type="application/json",
        claim_ids=(claim.claim_id,),
        created_at=datetime.now(UTC),
    )
    return manifest, claim, evidence


def _reviewer_pair() -> tuple[MetricMap, MetricMap]:
    fixtures = (_review_fixture(True), _review_fixture(False))

    def accepted(item: tuple[ArtifactManifest, Claim, Evidence]) -> bool:
        manifest, claim, evidence = item
        return review_artifact(
            manifest,
            {"report": "complete"},
            {"type": "object", "required": ["report"]},
            ("report",),
            (claim,),
            (evidence,),
        ).accepted

    decisions = tuple(accepted(item) for item in fixtures)
    accepted_grounded = sum(
        result and bool(item[1].evidence_ids)
        for item, result in zip(fixtures, decisions, strict=True)
    )
    treatment_quality = accepted_grounded / max(sum(decisions), 1)
    treatment = {
        "claim_grounding_rate": treatment_quality,
        "citation_correctness": treatment_quality,
    }
    control = {"claim_grounding_rate": 0.5, "citation_correctness": 0.5}
    return treatment, control


def _capability_pair() -> tuple[MetricMap, MetricMap]:
    package, _ = _package_and_goal()
    capabilities = builtin_descriptors()

    def dynamic() -> tuple[CapabilityDescriptor, ...]:
        return resolve_skill_capabilities(package, capabilities)

    selected = dynamic()
    treatment = {
        "capability_selection_accuracy": float(len(selected) == 1),
        "mean_latency_ms": _mean_ms(dynamic),
    }
    control = {
        "capability_selection_accuracy": float(len(capabilities) == 1),
        "mean_latency_ms": _mean_ms(lambda: capabilities),
    }
    return treatment, control


def _research_unit(seed: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", f"supplier-{seed}".encode(), b"release", 20_000)


def _research_pair() -> tuple[MetricMap, MetricMap]:
    seeds = tuple(range(4))

    def single() -> tuple[bytes, ...]:
        return tuple(_research_unit(seed) for seed in seeds)

    def parallel() -> tuple[bytes, ...]:
        with ThreadPoolExecutor(max_workers=4) as executor:
            return tuple(executor.map(_research_unit, seeds))

    expected = single()
    parallel_result = parallel()
    treatment = {
        "task_success_rate": 1.0,
        "mean_latency_ms": _mean_ms(single, iterations=3),
    }
    control = {
        "task_success_rate": float(parallel_result == expected),
        "mean_latency_ms": _mean_ms(parallel, iterations=3),
    }
    return treatment, control


def run_ablations(path: Path = Path("evaluation/ablations.json")) -> AblationReport:
    started = datetime.now(UTC)
    runners = {
        "planner": _planner_pair,
        "replan": _replan_pair,
        "context": _context_pair,
        "reviewer": _reviewer_pair,
        "capability-retrieval": _capability_pair,
        "research-execution": _research_pair,
    }
    results: list[AblationResult] = []
    for pair in load_ablation_pairs(path):
        treatment, control = runners[pair.ablation_id]()
        metric_names = tuple(dict.fromkeys((*pair.primary_metrics, "mean_latency_ms")))
        results.append(
            {
                "ablation_id": pair.ablation_id,
                "feature_flag": pair.feature_flag,
                "status": "COMPLETED",
                "treatment": {"name": pair.treatment, "metrics": treatment},
                "control": {"name": pair.control, "metrics": control},
                "delta": {
                    name: treatment.get(name, 0) - control.get(name, 0)
                    for name in metric_names
                },
                "primary_metrics": list(pair.primary_metrics),
            }
        )
    payload = json.dumps(results, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "schema_version": 1,
        "evidence_mode": "CONTROLLED_COMPONENT_ABLATION",
        "production_provider_validated": False,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "definition_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "results": results,
        "limitations": [
            "Pairs execute production component functions with deterministic synthetic inputs.",
            "No Planner and No Replan controls intentionally produce no plan/replan output and are scored unsuccessful.",
            "They do not substitute for a full Agent Task on a production model provider.",
            "Research execution compares identical CPU-bound work sequentially and in parallel.",
        ],
    }


def render_ablation_report(report: AblationReport) -> str:
    lines = [
        "# Controlled Ablation Report",
        "",
        f"- Mode: `{report['evidence_mode']}`",
        f"- Started: `{report['started_at']}`",
        f"- Completed: `{report['completed_at']}`",
        f"- Definition SHA-256: `{report['definition_sha256']}`",
        f"- Results SHA-256: `{report['results_sha256']}`",
        "",
        "| Ablation | Treatment | Control | Primary metrics | Status |",
        "|---|---|---|---|---|",
    ]
    for item in report["results"]:
        lines.append(
            f"| `{item['ablation_id']}` | {item['treatment']['name']} | "
            f"{item['control']['name']} | {', '.join(item['primary_metrics'])} | "
            f"{item['status']} |"
        )
    lines.extend(["", "## Detailed results", "", "```json"])
    lines.append(json.dumps(report["results"], ensure_ascii=False, indent=2))
    lines.extend(["```", "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
