import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from xml.etree import ElementTree

from enterprise_agent.evaluation.contracts import (
    EvaluationObservation,
    EvaluationOperationalTotals,
    GoldenTask,
)
from enterprise_agent.evaluation.runner import (
    aggregate_metrics,
    apply_operational_totals,
    load_ablation_pairs,
    load_execution_manifest,
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_junit(path: Path) -> dict[str, tuple[str, float]]:
    root = ElementTree.parse(path).getroot()
    results: dict[str, tuple[str, float]] = {}
    for testcase in root.iter("testcase"):
        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        node = f"{classname.replace('.', '/')}.py::{name}"
        if testcase.find("failure") is not None or testcase.find("error") is not None:
            state = "FAILED"
        elif testcase.find("skipped") is not None:
            state = "NOT_RUN"
        else:
            state = "PASSED"
        results[node] = (state, float(testcase.attrib.get("time", "0")) * 1000)
    return results


def observations_from_junit(
    cases: tuple[GoldenTask, ...], manifest_path: Path, junit_path: Path
) -> tuple[EvaluationObservation, ...]:
    manifest = load_execution_manifest(manifest_path, cases)
    results = parse_junit(junit_path)
    cases_by_id = {case.case_id: case for case in cases}
    observations: list[EvaluationObservation] = []
    for binding in manifest.bindings:
        node_results = [results.get(node) for node in binding.pytest_nodes]
        if any(result is None for result in node_results):
            observations.append(EvaluationObservation(case_id=binding.case_id, status="NOT_RUN"))
            continue
        evidence_ref = f"{junit_path.as_posix()}#{','.join(binding.pytest_nodes)}"
        states = [result[0] for result in node_results if result is not None]
        status: Literal["PASSED", "FAILED", "NOT_RUN"] = (
            "FAILED" if "FAILED" in states else "NOT_RUN" if "NOT_RUN" in states else "PASSED"
        )
        if status == "NOT_RUN":
            observations.append(EvaluationObservation(case_id=binding.case_id, status=status))
            continue
        case = cases_by_id[binding.case_id]
        passed = status == "PASSED"
        observations.append(
            EvaluationObservation(
                case_id=binding.case_id,
                status=status,
                task_success=passed == case.expected.task_success,
                plan_valid=passed == case.expected.plan_valid,
                recovery_success=passed if case.category == "RECOVERY" else None,
                hitl_correct=passed if case.category == "HITL" else None,
                replan_success=passed if case.category == "REPLAN" else None,
                latency_ms=sum(result[1] for result in node_results if result is not None),
                failure_recovery_count=1 if case.category == "RECOVERY" else 0,
                evidence_ref=evidence_ref,
            )
        )
    return tuple(observations)


def run_controlled_acceptance(
    *,
    cases: tuple[GoldenTask, ...],
    manifest_path: Path,
    junit_path: Path,
    extra_pytest_args: tuple[str, ...] = (),
) -> tuple[int, tuple[str, ...], tuple[EvaluationObservation, ...]]:
    manifest = load_execution_manifest(manifest_path, cases)
    nodes = tuple(dict.fromkeys(node for item in manifest.bindings for node in item.pytest_nodes))
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--junitxml={junit_path}",
        *extra_pytest_args,
        *nodes,
    )
    completed = subprocess.run(command, check=False)
    observations = observations_from_junit(cases, manifest_path, junit_path)
    return completed.returncode, command, observations


def build_audit_report(
    *,
    cases: tuple[GoldenTask, ...],
    observations: tuple[EvaluationObservation, ...],
    suite_path: Path,
    manifest_path: Path,
    junit_path: Path,
    command: tuple[str, ...],
    exit_code: int,
    started_at: datetime,
    runtime_counters: dict[str, Any],
    operational_totals: EvaluationOperationalTotals | None = None,
    record_evidence: dict[str, Any] | None = None,
    persistence: dict[str, str] | None = None,
    ablation_path: Path = Path("evaluation/ablations.json"),
) -> dict[str, Any]:
    metrics = aggregate_metrics(cases, observations).model_dump(mode="json")
    if operational_totals is not None:
        metrics = apply_operational_totals(
            aggregate_metrics(cases, observations), operational_totals
        ).model_dump(mode="json")
    return {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "evidence_mode": "CONTROLLED_ACCEPTANCE",
        "production_provider_validated": False,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "suite_sha256": sha256_file(suite_path),
        "manifest_sha256": sha256_file(manifest_path),
        "junit_sha256": sha256_file(junit_path),
        "command": list(command),
        "pytest_exit_code": exit_code,
        "metrics": metrics,
        "runtime_counters": runtime_counters,
        "record_evidence": record_evidence,
        "persistence": persistence,
        "executed_pytest_nodes": sum(item.startswith("tests/") for item in command),
        "observations": [item.model_dump(mode="json") for item in observations],
        "ablations": [
            {
                "ablation_id": pair.ablation_id,
                "treatment": pair.treatment,
                "control": pair.control,
                "status": "NOT_RUN",
            }
            for pair in load_ablation_pairs(ablation_path)
        ],
        "limitations": [
            "This run is a controlled acceptance baseline, not production-provider validation.",
            "The 72 case bindings are backed by deduplicated acceptance nodes; shared nodes remain explicitly cited per case.",
            "The Compose worker is quiesced only during the controlled run to prevent it consuming test Outbox events, then restarted.",
            "Token and cost values come only from persisted usage_records created during this run.",
            "Evidence coverage uses claims as required evidence units and counts a claim covered only when a persisted claim_evidence_links row exists.",
            "Claim provenance correctness additionally verifies the immutable Claim hash, exact evidence-id link set, same-task ownership, and Evidence content hash.",
            "Tool success excludes policy-denied requests from the execution denominator; denied calls remain reported separately.",
            "The six ablation pairs remain NOT_RUN until treatment and control are executed.",
        ],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Evaluation Audit Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Evidence mode: `{report['evidence_mode']}`",
        f"- Started: `{report['started_at']}`",
        f"- Completed: `{report['completed_at']}`",
        f"- Pytest exit code: `{report['pytest_exit_code']}`",
        f"- Suite SHA-256: `{report['suite_sha256']}`",
        f"- Manifest SHA-256: `{report['manifest_sha256']}`",
        f"- JUnit SHA-256: `{report['junit_sha256']}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        lines.append(f"| `{key}` | {value if value is not None else 'NOT_MEASURED'} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case | Status | Latency ms | Evidence |",
            "|---|---|---:|---|",
        ]
    )
    for item in report["observations"]:
        lines.append(
            f"| `{item['case_id']}` | {item['status']} | "
            f"{item['latency_ms'] if item['latency_ms'] is not None else 'N/A'} | "
            f"`{item['evidence_ref'] or 'N/A'}` |"
        )
    lines.extend(["", "## Ablations", ""])
    lines.extend(
        f"- `{item['ablation_id']}`: {item['treatment']} vs {item['control']} — {item['status']}"
        for item in report["ablations"]
    )
    return "\n".join(lines) + "\n"
