import json
import os
from pathlib import Path

import httpx
from sqlalchemy import func, select

from apps.api.main import create_app
from enterprise_agent.storage.tables import (
    AuditRow,
    EvaluationCaseRow,
    EvaluationResultRow,
    EvaluationRunRow,
    UsageRecordRow,
)
from tests.integration.test_api_identity import register_and_login


async def test_evaluation_api_persists_versioned_suite_results_usage_and_audit() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    observations = json.loads(
        Path("evaluation/baseline_observations.json").read_text("utf-8")
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "evaluation-owner")
        outsider = await register_and_login(client, "evaluation-outsider")
        workspace_id = (
            await client.post(
                "/workspaces", headers=owner, json={"name": "Evaluation Workspace"}
            )
        ).json()["workspace_id"]

        cases = await client.get(
            "/eval/cases", headers=owner, params={"workspace_id": workspace_id}
        )
        assert cases.status_code == 200
        assert len(cases.json()) == 72
        denied = await client.get(
            "/eval/cases", headers=outsider, params={"workspace_id": workspace_id}
        )
        assert denied.status_code == 403

        created = await client.post(
            "/eval/runs",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "label": "verified flagship baseline",
                "observations": observations,
            },
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["state"] == "COMPLETED"
        assert len(payload["dataset_hash"]) == 64
        assert payload["metrics"]["total_cases"] == 72
        assert payload["metrics"]["evaluated_cases"] == 3
        assert payload["metrics"]["evaluated_case_rate"] == 3 / 72

        listed = await client.get(
            "/eval/runs", headers=owner, params={"workspace_id": workspace_id}
        )
        assert listed.status_code == 200
        assert [item["run_id"] for item in listed.json()] == [payload["run_id"]]
        usage_response = await client.get(
            "/usage", headers=owner, params={"workspace_id": workspace_id}
        )
        assert usage_response.status_code == 200
        assert len(usage_response.json()) == 1
        assert usage_response.json()[0]["kind"] == "EVALUATION"
        assert usage_response.json()[0]["units"] == 3

        async with app.state.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(EvaluationCaseRow).where(
                        EvaluationCaseRow.workspace_id == workspace_id
                    )
                )
                == 72
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(EvaluationResultRow).where(
                        EvaluationResultRow.workspace_id == workspace_id
                    )
                )
                == 72
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(EvaluationRunRow).where(
                        EvaluationRunRow.workspace_id == workspace_id
                    )
                )
                == 1
            )
            usage = await session.scalar(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.evaluation_run_id == payload["run_id"],
                )
            )
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "evaluation.completed",
                )
            )
        assert usage is not None and usage.units == 3
        assert audit is not None and audit.result["dataset_hash"] == payload["dataset_hash"]
