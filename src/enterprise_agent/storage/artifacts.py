import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from minio import Minio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import ArtifactManifest, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import ArtifactRow, ClaimRow, TaskRow


def _canonical(manifest: ArtifactManifest) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], manifest.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _restore(row: ArtifactRow) -> ArtifactManifest:
    manifest = ArtifactManifest.model_validate(row.payload)
    _, digest = _canonical(manifest)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "artifact metadata hash mismatch")
    return manifest


class ArtifactStore:
    def __init__(self, client: Minio, bucket: str = "enterprise-artifacts") -> None:
        self._client = client
        self._bucket = bucket

    async def _ensure_bucket(self) -> None:
        exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        if not exists:
            await asyncio.to_thread(self._client.make_bucket, self._bucket)

    def _object_name(self, workspace_id: UUID, task_id: UUID, artifact_id: UUID) -> str:
        return f"{workspace_id}/{task_id}/{artifact_id}"

    async def put(
        self,
        session: AsyncSession,
        *,
        artifact_id: UUID,
        workspace_id: UUID,
        task_id: UUID,
        content: bytes,
        media_type: str,
        claim_ids: tuple[UUID, ...],
    ) -> ArtifactManifest:
        task = await session.scalar(
            select(TaskRow.id).where(
                TaskRow.workspace_id == workspace_id, TaskRow.id == task_id
            )
        )
        if task is None:
            raise DomainError(FailureCode.POLICY_DENIED, "artifact task unavailable")
        if claim_ids:
            found = set(
                (
                    await session.scalars(
                        select(ClaimRow.id).where(
                            ClaimRow.workspace_id == workspace_id,
                            ClaimRow.task_id == task_id,
                            ClaimRow.id.in_(claim_ids),
                        )
                    )
                ).all()
            )
            if found != set(claim_ids):
                raise DomainError(FailureCode.VALIDATION_FAILED, "artifact claim unavailable")
        sha256 = hashlib.sha256(content).hexdigest()
        object_name = self._object_name(workspace_id, task_id, artifact_id)
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            source_task=task_id,
            storage_uri=f"s3://{self._bucket}/{object_name}",
            sha256=sha256,
            media_type=media_type,
            claim_ids=claim_ids,
            created_at=datetime.now(UTC),
        )
        existing = await session.scalar(
            select(ArtifactRow).where(
                ArtifactRow.workspace_id == workspace_id, ArtifactRow.id == artifact_id
            )
        )
        if existing is not None:
            restored = _restore(existing)
            if (
                restored.sha256 != sha256
                or restored.claim_ids != claim_ids
                or restored.source_task != task_id
                or restored.media_type != media_type
            ):
                raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable artifact mismatch")
            stored = await self.get(session, workspace_id, artifact_id)
            if stored != content:
                raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "artifact object mismatch")
            return restored
        await self._ensure_bucket()
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_name,
            io.BytesIO(content),
            len(content),
            content_type=media_type,
            metadata={"sha256": sha256},
        )
        payload, digest = _canonical(manifest)
        await session.execute(
            insert(ArtifactRow).values(
                id=artifact_id,
                workspace_id=workspace_id,
                task_id=task_id,
                schema_version=manifest.schema_version,
                content_hash=digest,
                payload=payload,
                created_at=manifest.created_at,
            )
        )
        return manifest

    async def get(self, session: AsyncSession, workspace_id: UUID, artifact_id: UUID) -> bytes:
        manifest = await self.get_manifest(session, workspace_id, artifact_id)

        def read_object() -> bytes:
            response = self._client.get_object(
                self._bucket,
                self._object_name(workspace_id, manifest.source_task, artifact_id),
            )
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        content = await asyncio.to_thread(read_object)
        if hashlib.sha256(content).hexdigest() != manifest.sha256:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "artifact content hash mismatch")
        return content

    async def get_manifest(
        self, session: AsyncSession, workspace_id: UUID, artifact_id: UUID
    ) -> ArtifactManifest:
        row = await session.scalar(
            select(ArtifactRow).where(
                ArtifactRow.workspace_id == workspace_id, ArtifactRow.id == artifact_id
            )
        )
        if row is None:
            raise DomainError(FailureCode.POLICY_DENIED, "artifact unavailable")
        return _restore(row)

    async def list_manifests(
        self, session: AsyncSession, workspace_id: UUID
    ) -> tuple[ArtifactManifest, ...]:
        rows = (
            await session.scalars(
                select(ArtifactRow)
                .where(ArtifactRow.workspace_id == workspace_id)
                .order_by(ArtifactRow.created_at, ArtifactRow.id)
            )
        ).all()
        return tuple(_restore(row) for row in rows)
