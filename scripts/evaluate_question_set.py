"""Build an auditable Completion report for retained real question-set runs."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.completion.policies import derive_completion_status
from enterprise_agent.storage.completion import (
    get_current_contract_version,
    get_current_delivery_decision,
    get_effective_assessments,
    list_requirements,
    list_verification_results,
)
from enterprise_agent.storage.provenance import load_claims
from enterprise_agent.storage.tables import TaskRow


def _database_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if configured:
        return configured
    values = dotenv_values(".env")
    password = values.get("POSTGRES_PASSWORD")
    if not isinstance(password, str) or not password:
        raise RuntimeError("TEST_DATABASE_URL or POSTGRES_PASSWORD is required")
    return URL.create(
        "postgresql+asyncpg",
        username="platform",
        password=password,
        host="127.0.0.1",
        port=55432,
        database="platform",
    ).render_as_string(hide_password=False)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _route_requirements(observation: dict[str, Any]) -> list[dict[str, Any]]:
    route = observation.get("route")
    plan = route.get("research_plan", {}) if isinstance(route, dict) else {}
    contract = plan.get("answer_contract", {}) if isinstance(plan, dict) else {}
    aspects = contract.get("required_aspects", []) if isinstance(contract, dict) else []
    return [
        {
            "id": None,
            "key": f"planned-aspect:{index}",
            "description": aspect,
            "mandatory": True,
            "source": "route.research_plan.answer_contract",
        }
        for index, aspect in enumerate(aspects, 1)
        if isinstance(aspect, str) and aspect.strip()
    ]


def evaluate_observation(
    case: dict[str, Any],
    observation: dict[str, Any],
    completion: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project one retained run without treating execution state as quality."""
    evidence = observation.get("evidence", [])
    claims = observation.get("claims", [])
    evidence_refs = [
        {
            "evidence_id": item.get("evidence_id"),
            "locator": item.get("locator"),
            "source_type": item.get("source_type"),
            "content_hash": item.get("content_hash"),
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    if completion is None:
        requirements = _route_requirements(observation)
        assessments: list[dict[str, Any]] = []
        verification: list[dict[str, Any]] = []
        delivery = None
        completion_status = "UNAVAILABLE"
        quality_status = "NOT_OBSERVED"
    else:
        requirements = completion["requirements"]
        assessments = completion["assessments"]
        verification = completion["verification"]
        delivery = completion["delivery_decision"]
        completion_status = completion["completion_status"]
        decision = None if delivery is None else delivery.get("decision")
        if completion_status == "COMPLETE" and decision == "FULL_ANSWER":
            quality_status = "PASS"
        elif completion_status == "PARTIAL" and decision == "PARTIAL_ANSWER":
            quality_status = "PARTIAL"
        elif decision == "BLOCKED":
            quality_status = "BLOCKED"
        elif decision == "CLARIFICATION_REQUIRED":
            quality_status = "NEEDS_CLARIFICATION"
        elif delivery is None:
            quality_status = "NOT_OBSERVED"
        else:
            quality_status = "UNAVAILABLE"

    gaps = [
        {
            "requirement_id": assessment.get("requirement_id"),
            "gap_type": assessment.get("gap_type"),
            "status": assessment.get("status"),
        }
        for assessment in assessments
        if assessment.get("gap_type") is not None
    ]
    return {
        "question": case["question"],
        "case_id": case["id"],
        "mode": case.get("mode"),
        "task_id": observation.get("task_id"),
        "execution_status": observation.get("state"),
        "completion_status": completion_status,
        "requirements": requirements,
        "assessments": assessments,
        "gaps": gaps,
        "claims": (
            completion.get("claims", [])
            if completion is not None and completion.get("claims") is not None
            else claims if isinstance(claims, list) else []
        ),
        "evidence_refs": evidence_refs,
        "verification": verification,
        "delivery_decision": delivery,
        "quality_result": {
            "status": quality_status,
            "basis": "persisted Completion snapshot"
            if completion is not None
            else "no persisted Completion snapshot was available",
            "execution_state_is_not_used_as_quality": True,
        },
    }


async def _load_completion(
    session: AsyncSession,
    task_id: UUID,
) -> dict[str, Any] | None:
    task = await session.scalar(select(TaskRow).where(TaskRow.id == task_id))
    if task is None:
        return None
    contract = await get_current_contract_version(session, task.workspace_id, task_id)
    if contract is None:
        return {
            "requirements": [],
            "assessments": [],
            "verification": [],
            "delivery_decision": None,
            "completion_status": "UNAVAILABLE",
        }
    requirements = await list_requirements(
        session, task.workspace_id, task_id, contract_version_id=contract.id
    )
    assessments = await get_effective_assessments(session, task.workspace_id, task_id)
    verification = await list_verification_results(session, task.workspace_id, task_id)
    delivery = await get_current_delivery_decision(session, task.workspace_id, task_id)
    verified_claim_ids = {
        claim_id
        for item in verification
        for claim_id in item.claim_ids
    }
    claims = await load_claims(session, task.workspace_id, task_id)
    completion_status = derive_completion_status(
        contract,
        requirements,
        assessments,
        delivery_gates_passed=delivery is not None
        and delivery.decision.value not in {"BLOCKED", "UNAVAILABLE"},
    )
    return {
        "requirements": [_json(item) for item in requirements],
        "assessments": [_json(item) for item in assessments],
        "verification": [_json(item) for item in verification],
        "claims": [
            _json(item) for item in claims if item.claim_id in verified_claim_ids
        ],
        "delivery_decision": _json(delivery),
        "completion_status": completion_status.value,
    }


async def build_report(
    cases_path: Path,
    observations_dir: Path,
    phase: str,
    selected_cases: set[str] | None,
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    engine = create_async_engine(_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    results: list[dict[str, Any]] = []
    try:
        async with sessions() as session:
            for case in cases:
                if selected_cases is not None and case.get("id") not in selected_cases:
                    continue
                observation_path = observations_dir / f"{phase}-{case['id']}.json"
                if not observation_path.exists():
                    results.append(
                        {
                            "question": case["question"],
                            "case_id": case["id"],
                            "mode": case.get("mode"),
                            "task_id": None,
                            "execution_status": "NOT_RUN",
                            "completion_status": "UNAVAILABLE",
                            "requirements": [],
                            "assessments": [],
                            "gaps": [],
                            "claims": [],
                            "evidence_refs": [],
                            "verification": [],
                            "delivery_decision": None,
                            "quality_result": {
                                "status": "NOT_RUN",
                                "basis": "retained observation file is missing",
                                "execution_state_is_not_used_as_quality": True,
                            },
                        }
                    )
                    continue
                observation = json.loads(observation_path.read_text(encoding="utf-8"))
                task_id = observation.get("task_id")
                completion = (
                    await _load_completion(session, UUID(task_id))
                    if isinstance(task_id, str)
                    else None
                )
                results.append(evaluate_observation(case, observation, completion))
    finally:
        await engine.dispose()
    return {
        "schema_version": 1,
        "phase": phase,
        "source": str(observations_dir),
        "limitations": [
            "A quality result is derived only from persisted Completion facts and DeliveryDecision.",
            "Missing Completion facts are reported as NOT_OBSERVED; execution COMPLETED never implies quality PASS.",
            "External source authority and factual correctness remain subject to the recorded Verification gates and evidence provenance.",
        ],
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument(
        "--cases-file",
        type=Path,
        default=Path("work/verification/fixed-recovery-cases.json"),
    )
    parser.add_argument("--observations-dir", type=Path, default=Path("work/verification"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("work/verification") / f"{args.phase}-question-set.json"
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite previous report: {output}")
    report = asyncio.run(
        build_report(
            args.cases_file,
            args.observations_dir,
            args.phase,
            set(args.cases) if args.cases else None,
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output": str(output), "cases": len(report["cases"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
