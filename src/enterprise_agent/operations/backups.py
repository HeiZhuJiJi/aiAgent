from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from minio import Minio
from pydantic import BaseModel, ConfigDict, Field

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SECRET_MARKERS = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "CREDENTIAL")
_COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "deploy/compose/infrastructure.yml")


class DatabaseDump(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    user: str
    database: str
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1)


class ObjectDump(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str
    object_name: str
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    content_type: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ExportedSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    secret: bool
    present: bool
    value: str | None = None


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    created_at: datetime
    database_dumps: tuple[DatabaseDump, ...]
    objects: tuple[ObjectDump, ...]
    settings: tuple[ExportedSetting, ...]
    configuration_files: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compose(*arguments: str, input_data: bytes | None = None) -> bytes:
    completed = subprocess.run(
        [*_COMPOSE, *arguments],
        cwd=Path.cwd(),
        input=input_data,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"container command failed: {detail}")
    return completed.stdout


def _read_object(client: Minio, bucket: str, object_name: str) -> bytes:
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _load_env(path: Path) -> tuple[ExportedSetting, ...]:
    settings: list[ExportedSetting] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        secret = any(marker in name.upper() for marker in _SECRET_MARKERS)
        settings.append(
            ExportedSetting(
                name=name,
                secret=secret,
                present=bool(value),
                value=None if secret else value,
            )
        )
    return tuple(sorted(settings, key=lambda item: item.name))


def create_backup(target: Path, minio: Minio) -> BackupManifest:
    if target.exists():
        raise FileExistsError(f"backup target already exists: {target}")
    database_dir = target / "postgres"
    object_dir = target / "minio" / "objects"
    config_dir = target / "configuration"
    database_dir.mkdir(parents=True)
    object_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    database_specs = (
        ("postgres", "platform", "platform"),
        ("postgres", "platform", "temporal"),
        ("postgres", "platform", "temporal_visibility"),
        ("procurement-db", "procurement", "procurement"),
    )
    dumps: list[DatabaseDump] = []
    for service, user, database in database_specs:
        data = _compose(
            "exec",
            "-T",
            service,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "-U",
            user,
            "-d",
            database,
        )
        relative = f"postgres/{service}-{database}.dump"
        (target / relative).write_bytes(data)
        dumps.append(
            DatabaseDump(
                service=service,
                user=user,
                database=database,
                file=relative,
                sha256=_sha256(data),
                size=len(data),
            )
        )

    objects: list[ObjectDump] = []
    for bucket in sorted(minio.list_buckets(), key=lambda item: item.name):
        for item in minio.list_objects(bucket.name, recursive=True):
            data = _read_object(minio, bucket.name, item.object_name)
            digest = _sha256(data)
            relative = f"minio/objects/{digest}.bin"
            path = target / relative
            if not path.exists():
                path.write_bytes(data)
            stat = minio.stat_object(bucket.name, item.object_name)
            objects.append(
                ObjectDump(
                    bucket=bucket.name,
                    object_name=item.object_name,
                    file=relative,
                    sha256=digest,
                    size=len(data),
                    content_type=stat.content_type,
                    metadata={str(k): str(v) for k, v in (stat.metadata or {}).items()},
                )
            )

    configuration_files: list[str] = []
    for source in (
        Path(".env.example"),
        Path("alembic.ini"),
        Path("deploy/compose/infrastructure.yml"),
        Path("deploy/compose/temporal-dynamic.yaml"),
        Path("deploy/compose/temporal-setup.sh"),
    ):
        destination = config_dir / source.name
        destination.write_bytes(source.read_bytes())
        configuration_files.append(destination.relative_to(target).as_posix())

    manifest = BackupManifest(
        created_at=datetime.now(UTC),
        database_dumps=tuple(dumps),
        objects=tuple(objects),
        settings=_load_env(Path(".env")),
        configuration_files=tuple(configuration_files),
    )
    (target / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_and_verify_backup(source: Path) -> BackupManifest:
    manifest = BackupManifest.model_validate_json((source / "manifest.json").read_text())
    for database_dump in manifest.database_dumps:
        data = (source / database_dump.file).read_bytes()
        if len(data) != database_dump.size or _sha256(data) != database_dump.sha256:
            raise ValueError(f"backup payload integrity failure: {database_dump.file}")
    for object_dump in manifest.objects:
        data = (source / object_dump.file).read_bytes()
        if len(data) != object_dump.size or _sha256(data) != object_dump.sha256:
            raise ValueError(f"backup payload integrity failure: {object_dump.file}")
    for relative in manifest.configuration_files:
        path = (source / relative).resolve()
        if source.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"configuration payload unavailable: {relative}")
    return manifest


def restore_database(source: Path, dump: DatabaseDump, target_database: str) -> None:
    if not _IDENTIFIER.fullmatch(target_database):
        raise ValueError("target database must be a safe lowercase PostgreSQL identifier")
    sql = f'CREATE DATABASE "{target_database}" TEMPLATE template0;\n'.encode()
    _compose(
        "exec",
        "-T",
        dump.service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        dump.user,
        "-d",
        "postgres",
        input_data=sql,
    )
    try:
        _compose(
            "exec",
            "-T",
            dump.service,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "-U",
            dump.user,
            "-d",
            target_database,
            input_data=(source / dump.file).read_bytes(),
        )
    except Exception:
        drop_database(dump.service, dump.user, target_database)
        raise


def drop_database(service: str, user: str, database: str) -> None:
    if not _IDENTIFIER.fullmatch(database):
        raise ValueError("database must be a safe lowercase PostgreSQL identifier")
    sql = (
        f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = \'{database}\';\n'
        f'DROP DATABASE IF EXISTS "{database}";\n'
    ).encode()
    _compose(
        "exec",
        "-T",
        service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        user,
        "-d",
        "postgres",
        input_data=sql,
    )


def destination_bucket(prefix: str, source: str) -> str:
    candidate = prefix + source
    if len(candidate) <= 63:
        return candidate
    suffix = hashlib.sha256(source.encode()).hexdigest()[:8]
    return candidate[: 54] + "-" + suffix


def restore_minio(source: Path, manifest: BackupManifest, minio: Minio, prefix: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in manifest.objects:
        bucket = mapping.setdefault(item.bucket, destination_bucket(prefix, item.bucket))
        if not minio.bucket_exists(bucket):
            minio.make_bucket(bucket)
        data = (source / item.file).read_bytes()
        metadata: dict[str, str | list[str] | tuple[str]] = dict(item.metadata)
        minio.put_object(
            bucket,
            item.object_name,
            io.BytesIO(data),
            len(data),
            content_type=item.content_type or "application/octet-stream",
            metadata=metadata,
        )
    return mapping
