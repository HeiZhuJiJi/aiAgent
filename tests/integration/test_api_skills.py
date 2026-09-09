import os
from uuid import UUID

import httpx
import pytest
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from enterprise_agent.storage.skills import SkillArchiveStore, load_enabled_skill
from enterprise_agent.storage.tables import AuditRow, SkillInstallationRow, SkillVersionRow
from tests.integration.test_api_identity import register_and_login
from tests.security.test_policy import policy
from tests.security.test_skill_package import archive


async def test_skill_quarantine_confirm_enable_disable_and_rejected_archive():
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    store = SkillArchiveStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-api-skills",
    )
    app = create_app(
        os.environ["TEST_DATABASE_URL"],
        skill_archive_store=store,
        skill_capabilities=(policy().capability,),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "skill-owner")
        outsider = await register_and_login(client, "skill-outsider")
        workspace_id = (
            await client.post("/workspaces", headers=owner, json={"name": "Skill Workspace"})
        ).json()["workspace_id"]
        params = {
            "workspace_id": workspace_id,
            "name": "supplier-risk",
            "version": "1.0.0",
        }
        package = archive()
        quarantined = await client.post(
            "/skills/quarantine",
            params=params,
            headers={**owner, "Content-Type": "application/zip"},
            content=package,
        )
        assert quarantined.status_code == 201
        pending = quarantined.json()
        assert pending["state"] == "PENDING_CONFIRMATION"
        assert pending["version"] == 2
        assert pending["permission_diff"] == []
        replay = await client.post(
            "/skills/quarantine",
            params=params,
            headers={**owner, "Content-Type": "application/zip"},
            content=package,
        )
        assert replay.json()["installation_id"] == pending["installation_id"]
        denied = await client.get(
            "/skills", params={"workspace_id": workspace_id}, headers=outsider
        )
        assert denied.status_code == 403
        installed = await client.post(
            "/skills/install",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "installation_id": pending["installation_id"],
                "expected_version": 2,
            },
        )
        assert installed.status_code == 200
        assert installed.json()["state"] == "INSTALLED"
        stale = await client.post(
            "/skills/install",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "installation_id": pending["installation_id"],
                "expected_version": 2,
            },
        )
        assert stale.status_code == 409
        enabled = await client.post(
            f"/skills/{pending['installation_id']}/enable",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 3},
        )
        assert enabled.status_code == 200
        assert enabled.json()["state"] == "ENABLED"
        async with app.state.sessions() as session:
            loaded = await load_enabled_skill(session, UUID(workspace_id), "supplier-risk")
        assert loaded.sha256 == pending["archive_sha256"]
        assert loaded.workflow.steps[0].step_key == "admit"
        disabled = await client.post(
            f"/skills/{pending['installation_id']}/disable",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 4},
        )
        assert disabled.status_code == 200
        assert disabled.json()["state"] == "DISABLED"

        rejected = await client.post(
            "/skills/quarantine",
            params={
                "workspace_id": workspace_id,
                "name": "invalid-skill",
                "version": "1.0.0",
            },
            headers={**owner, "Content-Type": "application/zip"},
            content=archive({"run.py": "unsafe"}),
        )
        assert rejected.status_code == 201
        assert rejected.json()["state"] == "REJECTED"
        assert "validation failed" in rejected.json()["safe_error"]
        stored = await store.read(
            f"{workspace_id}/quarantine/{rejected.json()['archive_sha256']}.skill.zip",
            rejected.json()["archive_sha256"],
        )
        assert stored
        async with app.state.sessions() as session:
            rows = (
                await session.scalars(
                    select(SkillInstallationRow).where(
                        SkillInstallationRow.workspace_id == UUID(workspace_id)
                    )
                )
            ).all()
            audits = (
                await session.scalars(
                    select(AuditRow).where(
                        AuditRow.workspace_id == UUID(workspace_id),
                        AuditRow.action.like("skill.%"),
                    )
                )
            ).all()
        assert {row.state for row in rows} == {"DISABLED", "REJECTED"}
        assert {row.action for row in audits} == {
            "skill.validation.succeeded",
            "skill.install",
            "skill.enable",
            "skill.disable",
            "skill.quarantine.rejected",
        }

        with pytest.raises(DBAPIError):
            async with app.state.sessions.begin() as session:
                await session.execute(
                    update(SkillVersionRow)
                    .where(SkillVersionRow.workspace_id == UUID(workspace_id))
                    .values(version="9.9.9")
                )
