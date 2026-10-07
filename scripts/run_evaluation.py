import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.evaluation.contracts import (
    EvaluationObservation,
    EvaluationOperationalTotals,
)
from enterprise_agent.evaluation.execution import (
    build_audit_report,
    render_markdown_report,
    run_controlled_acceptance,
    sha256_file,
)
from enterprise_agent.evaluation.runner import load_golden_tasks
from enterprise_agent.storage.evaluation import persist_evaluation_run

SUITE_PATH = Path("evaluation/golden_tasks.json")
MANIFEST_PATH = Path("evaluation/execution_manifest.json")
OUTPUT = Path("work/verification/evaluation")


def configure_environment() -> None:
    values = dotenv_values(".env")
    for key, user, port, database, password_key in (
        ("TEST_DATABASE_URL", "platform", 55432, "platform", "POSTGRES_PASSWORD"),
        (
            "PROCUREMENT_DATABASE_URL",
            "procurement",
            55433,
            "procurement",
            "PROCUREMENT_DB_PASSWORD",
        ),
    ):
        password = values.get(password_key)
        if not password:
            raise RuntimeError(f"{password_key} is required in .env")
        os.environ[key] = URL.create(
            "postgresql+asyncpg",
            username=user,
            password=password,
            host="127.0.0.1",
            port=port,
            database=database,
        ).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["REDIS_URL"] = "redis://127.0.0.1:56379/0"
    os.environ["ADMISSION_NAMESPACE"] = "test:admission:evaluation:" + str(uuid4())


async def capture_counters() -> dict[str, int | float]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    statement = text(
        "SELECT "
        "(SELECT count(*) FROM capability_calls) AS tool_calls, "
        "(SELECT count(*) FROM replan_decisions) AS replans, "
        "(SELECT count(*) FROM approval_requests) AS hitl, "
        "(SELECT count(*) FROM consistency_incidents) AS recovery_incidents, "
        "(SELECT coalesce(sum(tokens), 0) FROM usage_records) AS tokens, "
        "(SELECT coalesce(sum(cost), 0) FROM usage_records) AS cost"
    )
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one()
            return {
                "tool_calls": int(row["tool_calls"]),
                "replans": int(row["replans"]),
                "hitl": int(row["hitl"]),
                "recovery_incidents": int(row["recovery_incidents"]),
                "tokens": int(row["tokens"]),
                "cost": float(row["cost"]),
            }
    finally:
        await engine.dispose()


async def capture_record_metrics(
    started_at: datetime,
    completed_at: datetime,
    observations: tuple[EvaluationObservation, ...],
) -> tuple[EvaluationOperationalTotals, dict[str, object]]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    bounds = {"started_at": started_at, "completed_at": completed_at}
    summary_sql = text(
        "WITH calls AS ("
        " SELECT * FROM capability_calls WHERE created_at >= :started_at"
        " AND created_at <= :completed_at), attempts AS ("
        " SELECT * FROM step_attempts WHERE started_at >= :started_at"
        " AND started_at <= :completed_at), attempt_groups AS ("
        " SELECT step_id, count(*) AS attempts FROM attempts GROUP BY step_id), replans AS ("
        " SELECT * FROM replan_decisions WHERE created_at >= :started_at"
        " AND created_at <= :completed_at), hitl AS ("
        " SELECT * FROM approval_requests WHERE created_at >= :started_at"
        " AND created_at <= :completed_at), usage AS ("
        " SELECT * FROM usage_records WHERE created_at >= :started_at"
        " AND created_at <= :completed_at)"
        " SELECT"
        " (SELECT count(*) FROM calls) AS tool_calls,"
        " (SELECT count(*) FROM calls WHERE state IN ('SUCCEEDED','FAILED','UNKNOWN'))"
        " AS tool_executions,"
        " (SELECT count(*) FROM calls WHERE state = 'SUCCEEDED') AS tool_succeeded,"
        " (SELECT count(*) FROM calls WHERE state = 'DENIED') AS tool_denied,"
        " (SELECT count(*) FROM attempts) AS step_attempts,"
        " (SELECT count(*) FROM attempt_groups) AS steps_attempted,"
        " (SELECT count(*) FROM attempt_groups WHERE attempts > 1) AS retried_steps,"
        " (SELECT coalesce(sum(attempts - 1), 0) FROM attempt_groups) AS retries,"
        " (SELECT count(*) FROM replans) AS replans,"
        " (SELECT count(*) FROM replans WHERE status = 'APPLIED') AS replans_applied,"
        " (SELECT count(*) FROM replans WHERE status = 'ABORTED') AS replans_aborted,"
        " (SELECT count(*) FROM hitl) AS hitl,"
        " (SELECT count(*) FROM hitl WHERE state = 'APPROVED') AS hitl_approved,"
        " (SELECT count(*) FROM hitl WHERE state = 'REJECTED') AS hitl_rejected,"
        " (SELECT count(*) FROM hitl WHERE state = 'EXPIRED') AS hitl_expired,"
        " (SELECT coalesce(sum(tokens), 0) FROM usage) AS tokens,"
        " (SELECT coalesce(sum(cost), 0) FROM usage) AS cost"
    )
    distributions_sql = text(
        "SELECT 'capability_calls' AS source, state, count(*) AS count"
        " FROM capability_calls WHERE created_at >= :started_at"
        " AND created_at <= :completed_at GROUP BY state"
        " UNION ALL SELECT 'step_attempts', state, count(*)"
        " FROM step_attempts WHERE started_at >= :started_at"
        " AND started_at <= :completed_at GROUP BY state"
        " UNION ALL SELECT 'replan_decisions', status, count(*)"
        " FROM replan_decisions WHERE created_at >= :started_at"
        " AND created_at <= :completed_at GROUP BY status"
        " UNION ALL SELECT 'approval_requests', state, count(*)"
        " FROM approval_requests WHERE created_at >= :started_at"
        " AND created_at <= :completed_at GROUP BY state"
        " UNION ALL SELECT 'usage_records', kind, count(*)"
        " FROM usage_records WHERE created_at >= :started_at"
        " AND created_at <= :completed_at GROUP BY kind"
        " ORDER BY source, state"
    )
    claims_sql = text(
        "SELECT c.id AS claim_id, c.workspace_id, c.task_id, c.content_hash, c.payload,"
        " l.evidence_id, e.payload AS evidence_payload"
        " FROM claims c"
        " LEFT JOIN claim_evidence_links l"
        " ON l.workspace_id = c.workspace_id AND l.claim_id = c.id"
        " LEFT JOIN evidence e"
        " ON e.workspace_id = l.workspace_id AND e.id = l.evidence_id"
        " WHERE c.created_at >= :started_at AND c.created_at <= :completed_at"
        " ORDER BY c.id, l.evidence_id"
    )
    try:
        async with engine.connect() as connection:
            summary = (await connection.execute(summary_sql, bounds)).mappings().one()
            distributions = (await connection.execute(distributions_sql, bounds)).mappings().all()
            claim_rows = (await connection.execute(claims_sql, bounds)).mappings().all()
    finally:
        await engine.dispose()

    claims: dict[str, dict[str, object]] = {}
    for row in claim_rows:
        claim_id = str(row["claim_id"])
        item = claims.setdefault(
            claim_id,
            {
                "workspace_id": str(row["workspace_id"]),
                "task_id": str(row["task_id"]),
                "content_hash": row["content_hash"],
                "payload": row["payload"],
                "links": [],
            },
        )
        if row["evidence_id"] is not None:
            links = item["links"]
            assert isinstance(links, list)
            links.append(
                {
                    "evidence_id": str(row["evidence_id"]),
                    "payload": row["evidence_payload"],
                }
            )

    evidence_covered = 0
    provenance_correct = 0
    invalid_claim_ids: list[str] = []
    for claim_id, item in claims.items():
        payload = item["payload"]
        links = item["links"]
        assert isinstance(payload, dict)
        assert isinstance(links, list)
        actual_ids = {str(link["evidence_id"]) for link in links}
        expected_ids = {str(value) for value in payload.get("evidence_ids", [])}
        if actual_ids:
            evidence_covered += 1
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        valid = hashlib.sha256(encoded).hexdigest() == item["content_hash"]
        valid = valid and bool(actual_ids) and actual_ids == expected_ids
        for link in links:
            evidence_payload = link["payload"]
            if not isinstance(evidence_payload, dict):
                valid = False
                continue
            content = evidence_payload.get("content")
            content_hash = evidence_payload.get("content_hash")
            valid = valid and evidence_payload.get("task_id") == item["task_id"]
            valid = valid and evidence_payload.get("workspace_id") == item["workspace_id"]
            valid = valid and isinstance(content, str) and isinstance(content_hash, str)
            if isinstance(content, str) and isinstance(content_hash, str):
                valid = valid and hashlib.sha256(content.encode()).hexdigest() == content_hash
        if valid:
            provenance_correct += 1
        else:
            invalid_claim_ids.append(claim_id)

    recovery_count = sum(
        item.failure_recovery_count or 0 for item in observations if item.status == "PASSED"
    )
    totals = EvaluationOperationalTotals(
        tokens=int(summary["tokens"]),
        cost=float(summary["cost"]),
        tool_calls=int(summary["tool_calls"]),
        replans=int(summary["replans"]),
        hitl=int(summary["hitl"]),
        failure_recoveries=recovery_count,
        evidence_covered=evidence_covered,
        evidence_required=len(claims),
        claims_provenance_correct=provenance_correct,
        claims_total=len(claims),
        tool_executions=int(summary["tool_executions"]),
        tool_succeeded=int(summary["tool_succeeded"]),
        tool_denied=int(summary["tool_denied"]),
        step_attempts=int(summary["step_attempts"]),
        steps_attempted=int(summary["steps_attempted"]),
        retried_steps=int(summary["retried_steps"]),
        retries=int(summary["retries"]),
        replans_applied=int(summary["replans_applied"]),
        replans_aborted=int(summary["replans_aborted"]),
        hitl_approved=int(summary["hitl_approved"]),
        hitl_rejected=int(summary["hitl_rejected"]),
        hitl_expired=int(summary["hitl_expired"]),
    )
    record_evidence: dict[str, object] = {
        "window": {
            "started_at": started_at.astimezone(UTC).isoformat(),
            "completed_at": completed_at.astimezone(UTC).isoformat(),
        },
        "source_tables": [
            "capability_calls",
            "step_attempts",
            "replan_decisions",
            "approval_requests",
            "usage_records",
            "claims",
            "claim_evidence_links",
            "evidence",
        ],
        "state_distributions": [dict(row) for row in distributions],
        "claim_ids": sorted(claims),
        "invalid_claim_ids": invalid_claim_ids,
        "definitions": {
            "evidence_coverage": "claims with one or more persisted evidence links / claims",
            "claim_provenance_correctness": "claims passing immutable hash, exact link-set, same task/workspace, and evidence content-hash checks / claims",
            "tool_success_rate": "SUCCEEDED / (SUCCEEDED + FAILED + UNKNOWN); policy DENIED is reported but excluded",
            "retry_rate": "distinct steps with more than one attempt / distinct attempted steps",
        },
    }
    return totals, record_evidence


async def persist_results(
    observations: tuple[EvaluationObservation, ...],
    totals: EvaluationOperationalTotals,
    evidence_sha256: str,
) -> dict[str, str]:
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from apps.api.main import create_app

    app = create_app(os.environ["TEST_DATABASE_URL"])
    email = f"evaluation-{uuid4()}@example.test"
    password = "controlled evaluation runner credential"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://evaluation"
        ) as client,
    ):
        registered = await client.post(
            "/auth/register", json={"email": email, "password": password}
        )
        registered.raise_for_status()
        logged_in = await client.post("/auth/login", json={"email": email, "password": password})
        logged_in.raise_for_status()
        headers = {"Authorization": "Bearer " + logged_in.json()["access_token"]}
        identity = await client.get("/auth/me", headers=headers)
        identity.raise_for_status()
        workspace = await client.post(
            "/workspaces", headers=headers, json={"name": "Controlled Evaluation"}
        )
        workspace.raise_for_status()
        workspace_id = workspace.json()["workspace_id"]
        actor_id = identity.json()["user_id"]
        async with app.state.sessions.begin() as session:
            view = await persist_evaluation_run(
                session,
                workspace_id,
                actor_id,
                "evaluation-" + evidence_sha256[:32],
                "final-validation controlled acceptance",
                observations,
                operational_totals=totals,
                execution_evidence_sha256=evidence_sha256,
            )
        return {"workspace_id": str(workspace_id), "evaluation_run_id": str(view.run_id)}


def main() -> int:
    configure_environment()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for stale_name in (
        "junit.xml",
        "observations.json",
        "metrics.json",
        "report.json",
        "report.md",
    ):
        (OUTPUT / stale_name).unlink(missing_ok=True)
    subprocess.run([sys.executable, "scripts/init_procurement.py"], check=True)
    subprocess.run(["docker", "compose", "stop", "worker"], check=True)
    try:
        before = asyncio.run(capture_counters())
        started_at = datetime.now(UTC)
        junit_path = OUTPUT / "junit.xml"
        temp_path = Path("work/pytest-temp") / ("evaluation-" + str(uuid4()))
        cases = load_golden_tasks(SUITE_PATH)
        exit_code, command, observations = run_controlled_acceptance(
            cases=cases,
            manifest_path=MANIFEST_PATH,
            junit_path=junit_path,
            extra_pytest_args=(f"--basetemp={temp_path.resolve()}",),
        )
        after = asyncio.run(capture_counters())
        record_completed_at = datetime.now(UTC)
        operational_totals, record_evidence = asyncio.run(
            capture_record_metrics(started_at, record_completed_at, observations)
        )
    finally:
        subprocess.run(["docker", "compose", "start", "worker"], check=True)
    delta = {key: after[key] - before[key] for key in before}
    counters = {"before": before, "after": after, "delta": delta}
    persistence = asyncio.run(
        persist_results(observations, operational_totals, sha256_file(junit_path))
    )
    report = build_audit_report(
        cases=cases,
        observations=observations,
        suite_path=SUITE_PATH,
        manifest_path=MANIFEST_PATH,
        junit_path=junit_path,
        command=command,
        exit_code=exit_code,
        started_at=started_at,
        runtime_counters=counters,
        operational_totals=operational_totals,
        record_evidence=record_evidence,
        persistence=persistence,
    )
    (OUTPUT / "observations.json").write_text(
        json.dumps(report["observations"], indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "metrics.json").write_text(
        json.dumps(report["metrics"], indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "report.md").write_text(render_markdown_report(report), encoding="utf-8")
    print(
        f"Evaluation: {report['metrics']['evaluated_cases']}/{report['metrics']['total_cases']} "
        f"cases, success={report['metrics']['task_success_rate']}, report={OUTPUT / 'report.json'}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
