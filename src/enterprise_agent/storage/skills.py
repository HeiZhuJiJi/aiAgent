import asyncio
import hashlib
import io
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from minio import Minio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    FailureCode,
    SkillInstallationView,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.skills.package import SkillPackage, validate_package
from enterprise_agent.storage.tables import (
    AuditRow,
    SkillInstallationRow,
    SkillRow,
    SkillVersionRow,
)

MAX_SKILL_ARCHIVE_BYTES = 10 * 1024 * 1024
SkillState = Literal[
    "QUARANTINED",
    "REJECTED",
    "PENDING_CONFIRMATION",
    "INSTALLED",
    "ENABLED",
    "DISABLED",
]


class SkillArchiveStore:
    def __init__(self, client: Minio, bucket: str = "enterprise-skill-quarantine") -> None:
        self._client = client
        self._bucket = bucket

    def storage_uri(self, object_name: str) -> str:
        return f"s3://{self._bucket}/{object_name}"

    async def stage(self, workspace_id: UUID, blob: bytes) -> tuple[str, str]:
        if not blob or len(blob) > MAX_SKILL_ARCHIVE_BYTES:
            raise DomainError(FailureCode.VALIDATION_FAILED, "skill archive size invalid")
        digest = hashlib.sha256(blob).hexdigest()
        object_name = f"{workspace_id}/quarantine/{digest}.skill.zip"
        exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        if not exists:
            await asyncio.to_thread(self._client.make_bucket, self._bucket)
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_name,
            io.BytesIO(blob),
            len(blob),
            content_type="application/zip",
            metadata={"sha256": digest, "quarantine": "true"},
        )
        return digest, object_name

    async def read(self, object_name: str, expected_hash: str) -> bytes:
        def load() -> bytes:
            response = self._client.get_object(self._bucket, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        blob = await asyncio.to_thread(load)
        if hashlib.sha256(blob).hexdigest() != expected_hash:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "skill archive hash mismatch")
        return blob


def installation_view(row: SkillInstallationRow) -> SkillInstallationView:
    return SkillInstallationView(
        installation_id=row.id,
        workspace_id=row.workspace_id,
        skill_id=row.skill_id,
        skill_version_id=row.skill_version_id,
        name=row.declared_name,
        skill_version=row.declared_version,
        archive_sha256=row.archive_sha256,
        state=cast(SkillState, row.state),
        version=row.version,
        permission_diff=tuple(row.permission_diff),
        safe_error=row.safe_error,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        enabled_at=row.enabled_at,
    )


async def quarantine_skill(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    declared_name: str,
    declared_version: str,
    archive_sha256: str,
    object_name: str,
    storage_uri: str,
    blob: bytes,
    capabilities: tuple[CapabilityDescriptor, ...],
    existing_permissions: frozenset[str],
    trace_id: str,
) -> SkillInstallationView:
    installation_id = uuid5(
        NAMESPACE_URL,
        f"skill-installation:{workspace_id}:{declared_name}:{declared_version}",
    )
    existing = await session.scalar(
        select(SkillInstallationRow)
        .where(
            SkillInstallationRow.workspace_id == workspace_id,
            SkillInstallationRow.id == installation_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.archive_sha256 != archive_sha256
            or existing.object_name != object_name
            or existing.declared_name != declared_name
            or existing.declared_version != declared_version
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION, "Skill version archive changed"
            )
        return installation_view(existing)
    row = SkillInstallationRow(
        id=installation_id,
        workspace_id=workspace_id,
        declared_name=declared_name,
        declared_version=declared_version,
        archive_sha256=archive_sha256,
        object_name=object_name,
        state="QUARANTINED",
        permission_diff=[],
    )
    session.add(row)
    await session.flush()
    try:
        package = validate_package(blob, capabilities)
        if (
            package.manifest.name != declared_name
            or package.manifest.version != declared_version
        ):
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "declared Skill identity does not match the package",
            )
    except DomainError as exc:
        row.state = "REJECTED"
        row.version += 1
        row.safe_error = exc.detail[:500]
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=identity.user_id,
                action="skill.quarantine.rejected",
                resource_type="SKILL_INSTALLATION",
                resource_id=installation_id,
                result={"archive_sha256": archive_sha256, "reason": row.safe_error},
                trace_id=trace_id,
            )
        )
        await session.flush()
        return installation_view(row)
    skill_id = uuid5(NAMESPACE_URL, f"skill:{workspace_id}:{declared_name}")
    skill = await session.scalar(
        select(SkillRow).where(
            SkillRow.workspace_id == workspace_id, SkillRow.id == skill_id
        )
    )
    if skill is None:
        skill = SkillRow(
            id=skill_id,
            workspace_id=workspace_id,
            name=package.manifest.name,
            description=package.manifest.description,
        )
        session.add(skill)
        await session.flush()
    elif skill.name != package.manifest.name:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "Skill identity changed")
    skill_version_id = uuid5(
        NAMESPACE_URL,
        f"skill-version:{workspace_id}:{declared_name}:{declared_version}",
    )
    stored_version = await session.scalar(
        select(SkillVersionRow).where(
            SkillVersionRow.workspace_id == workspace_id,
            SkillVersionRow.id == skill_version_id,
        )
    )
    payload = package.model_dump(mode="json")
    if stored_version is None:
        session.add(
            SkillVersionRow(
                id=skill_version_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
                version=declared_version,
                archive_sha256=archive_sha256,
                storage_uri=storage_uri,
                package_payload=payload,
            )
        )
        await session.flush()
    elif (
        stored_version.archive_sha256 != archive_sha256
        or stored_version.storage_uri != storage_uri
        or stored_version.package_payload != payload
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "Skill version changed")
    row.skill_id = skill_id
    row.skill_version_id = skill_version_id
    row.permission_diff = sorted(package.permissions.permissions - existing_permissions)
    row.state = "PENDING_CONFIRMATION"
    row.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="skill.validation.succeeded",
            resource_type="SKILL_INSTALLATION",
            resource_id=installation_id,
            result={
                "archive_sha256": archive_sha256,
                "permission_diff": row.permission_diff,
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return installation_view(row)


async def list_skill_installations(
    session: AsyncSession, workspace_id: UUID
) -> tuple[SkillInstallationView, ...]:
    rows = (
        await session.scalars(
            select(SkillInstallationRow)
            .where(SkillInstallationRow.workspace_id == workspace_id)
            .order_by(SkillInstallationRow.created_at, SkillInstallationRow.id)
        )
    ).all()
    return tuple(installation_view(row) for row in rows)


async def transition_skill_installation(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    installation_id: UUID,
    action: Literal["install", "enable", "disable"],
    expected_version: int,
    trace_id: str,
) -> SkillInstallationView:
    row = await session.scalar(
        select(SkillInstallationRow)
        .where(
            SkillInstallationRow.workspace_id == workspace_id,
            SkillInstallationRow.id == installation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "Skill installation unavailable")
    if row.version != expected_version:
        raise DomainError(FailureCode.STALE_ATTEMPT, "Skill installation version changed")
    transitions = {
        ("PENDING_CONFIRMATION", "install"): "INSTALLED",
        ("INSTALLED", "enable"): "ENABLED",
        ("DISABLED", "enable"): "ENABLED",
        ("ENABLED", "disable"): "DISABLED",
    }
    target = transitions.get((row.state, action))
    if target is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "invalid Skill lifecycle transition")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    row.state = target
    row.version += 1
    if action == "install":
        row.confirmed_by = identity.user_id
        row.confirmed_at = now
    elif action == "enable":
        row.enabled_at = now
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action=f"skill.{action}",
            resource_type="SKILL_INSTALLATION",
            resource_id=row.id,
            result={"state": row.state, "version": row.version},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return installation_view(row)


async def load_enabled_skill(
    session: AsyncSession, workspace_id: UUID, name: str
) -> SkillPackage:
    row = await session.scalar(
        select(SkillVersionRow)
        .join(
            SkillInstallationRow,
            (
                SkillInstallationRow.workspace_id == SkillVersionRow.workspace_id
            )
            & (SkillInstallationRow.skill_version_id == SkillVersionRow.id),
        )
        .join(
            SkillRow,
            (SkillRow.workspace_id == SkillVersionRow.workspace_id)
            & (SkillRow.id == SkillVersionRow.skill_id),
        )
        .where(
            SkillVersionRow.workspace_id == workspace_id,
            SkillRow.name == name,
            SkillInstallationRow.state == "ENABLED",
        )
        .order_by(SkillVersionRow.created_at.desc(), SkillVersionRow.id)
    )
    if row is None:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "enabled Skill unavailable")
    return SkillPackage.model_validate(row.package_payload)
