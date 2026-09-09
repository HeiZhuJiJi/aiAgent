import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.operations.backups import (
    BackupManifest,
    create_backup,
    drop_database,
    restore_database,
    restore_minio,
)


async def _flagship_identity(database_url: str) -> tuple[UUID, UUID, dict[str, object]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT w.id, t.id, a.payload "
                        "FROM workspaces w "
                        "JOIN task_runs t ON t.workspace_id = w.id "
                        "JOIN artifacts a ON a.workspace_id = w.id AND a.task_id = t.id "
                        "WHERE w.name = 'Supplier A Flagship' "
                        "ORDER BY t.created_at DESC LIMIT 1"
                    )
                )
            ).one()
            return row[0], row[1], cast(dict[str, object], row[2])
    finally:
        await engine.dispose()


async def _verify_restored_platform(
    database_url: str, workspace_id: UUID, task_id: UUID
) -> dict[str, int | str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            counts: dict[str, int | str] = {}
            queries = {
                "workspace": "SELECT count(*) FROM workspaces WHERE id = :workspace_id",
                "task": (
                    "SELECT count(*) FROM task_runs "
                    "WHERE workspace_id = :workspace_id AND id = :task_id"
                ),
                "plan": (
                    "SELECT count(*) FROM task_plans "
                    "WHERE workspace_id = :workspace_id AND task_id = :task_id"
                ),
                "evidence": (
                    "SELECT count(*) FROM evidence e JOIN task_steps s "
                    "ON s.workspace_id = e.workspace_id AND s.id = e.step_id "
                    "WHERE s.workspace_id = :workspace_id AND s.task_id = :task_id"
                ),
                "artifact": (
                    "SELECT count(*) FROM artifacts "
                    "WHERE workspace_id = :workspace_id AND task_id = :task_id"
                ),
                "audit": (
                    "SELECT count(*) FROM audit_logs "
                    "WHERE workspace_id = :workspace_id AND task_id = :task_id"
                ),
            }
            parameters = {"workspace_id": workspace_id, "task_id": task_id}
            for name, sql in queries.items():
                counts[name] = int((await connection.scalar(text(sql), parameters)) or 0)
            state = await connection.scalar(
                text(
                    "SELECT state FROM task_runs "
                    "WHERE workspace_id = :workspace_id AND id = :task_id"
                ),
                parameters,
            )
            counts["task_state"] = str(state)
            if any(int(counts[name]) < 1 for name in queries):
                raise AssertionError(f"restored authoritative record missing: {counts}")
            if counts["task_state"] != "COMPLETED":
                raise AssertionError(f"restored task is not complete: {counts}")
            return counts
    finally:
        await engine.dispose()


async def _verify_restored_procurement(database_url: str, workspace_id: UUID) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            count = int(
                (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM admissions "
                            "WHERE workspace_id = :workspace_id"
                        ),
                        {"workspace_id": workspace_id},
                    )
                )
                or 0
            )
            if count != 1:
                raise AssertionError(f"restored admission count is {count}, expected 1")
            return count
    finally:
        await engine.dispose()


def _database_url(user: str, password: object, port: int, database: str) -> str:
    return URL.create(
        "postgresql+asyncpg",
        username=user,
        password=str(password),
        host="127.0.0.1",
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def _read_object(client: Minio, bucket: str, object_name: str) -> bytes:
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


async def main() -> None:
    values = dotenv_values(".env")
    required = ("POSTGRES_PASSWORD", "PROCUREMENT_DB_PASSWORD", "MINIO_ROOT_PASSWORD")
    if any(not values.get(name) for name in required):
        raise ValueError("local infrastructure secrets are incomplete")

    await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "scripts/verify_local.py",
            "tests/e2e/test_supplier_risk_flagship.py",
            "-k",
            "ADMIT",
        ],
        check=True,
    )
    platform_url = _database_url("platform", values["POSTGRES_PASSWORD"], 55432, "platform")
    workspace_id, task_id, artifact_payload = await _flagship_identity(platform_url)
    storage_uri = str(artifact_payload["storage_uri"])
    artifact_hash = str(artifact_payload["sha256"])
    if not storage_uri.startswith("s3://"):
        raise AssertionError("artifact does not use MinIO storage")
    source_bucket, object_name = storage_uri.removeprefix("s3://").split("/", 1)

    run_id = uuid4().hex[:10]
    backup_dir = Path("work/backup-restore") / run_id
    report_path = Path("work/verification/backup-restore.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    client = Minio(
        "127.0.0.1:59000",
        access_key="platform",
        secret_key=str(values["MINIO_ROOT_PASSWORD"]),
        secure=False,
    )
    manifest: BackupManifest = create_backup(backup_dir, client)
    if any(setting.secret and setting.value is not None for setting in manifest.settings):
        raise AssertionError("configuration export leaked a secret value")

    platform_target = "br_platform_" + run_id
    procurement_target = "br_procurement_" + run_id
    dumps = {item.database: item for item in manifest.database_dumps}
    restored_buckets: dict[str, str] = {}
    try:
        restore_database(backup_dir, dumps["platform"], platform_target)
        restore_database(backup_dir, dumps["procurement"], procurement_target)
        restored_buckets = restore_minio(backup_dir, manifest, client, "br" + run_id[:6] + "-")
        restored_counts = await _verify_restored_platform(
            _database_url("platform", values["POSTGRES_PASSWORD"], 55432, platform_target),
            workspace_id,
            task_id,
        )
        admission_count = await _verify_restored_procurement(
            _database_url(
                "procurement",
                values["PROCUREMENT_DB_PASSWORD"],
                55433,
                procurement_target,
            ),
            workspace_id,
        )
        restored_content = _read_object(client, restored_buckets[source_bucket], object_name)
        restored_hash = hashlib.sha256(restored_content).hexdigest()
        if restored_hash != artifact_hash:
            raise AssertionError("restored artifact content hash mismatch")
        report = {
            "schema_version": 1,
            "status": "PASS",
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "backup_directory": backup_dir.as_posix(),
            "database_dumps": len(manifest.database_dumps),
            "minio_objects": len(manifest.objects),
            "configuration_files": len(manifest.configuration_files),
            "secret_values_exported": 0,
            "restored_records": restored_counts,
            "restored_admissions": admission_count,
            "artifact_sha256": restored_hash,
        }
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        drop_database("postgres", "platform", platform_target)
        drop_database("procurement-db", "procurement", procurement_target)
        for bucket in restored_buckets.values():
            for item in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, item.object_name)
            client.remove_bucket(bucket)


if __name__ == "__main__":
    asyncio.run(main())
