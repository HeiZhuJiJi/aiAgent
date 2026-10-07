import argparse
import asyncio
import mimetypes
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from minio import Minio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.identity.contracts import Identity
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.storage.knowledge_jobs import (
    delete_knowledge_document,
    request_knowledge_ingestion,
    retry_knowledge_ingestion,
)
from enterprise_agent.storage.tables import (
    KnowledgeIngestionJobRow,
    UserRow,
    WorkspaceMemberRow,
)

SUPPORTED_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}
DOCUMENT_CODE = re.compile(
    r"^(FIN-\d+|HR-\d+|TECH-\d+|PM-\d+|TPL-\d+)", re.IGNORECASE
)
VERSION_NUMBER = re.compile(r"_v(\d+)(?:_|\.)", re.IGNORECASE)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a governed RAG knowledge directory")
    parser.add_argument("source", type=Path)
    parser.add_argument("workspace_id", type=UUID)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--replace-selected", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    return parser.parse_args()


def _discover(source: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    grouped: dict[str, list[Path]] = defaultdict(list)
    skipped: list[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if "evaluation" in {part.casefold() for part in relative.parts}:
            skipped.append(path)
            continue
        if path.suffix.casefold() not in SUPPORTED_MEDIA_TYPES:
            skipped.append(path)
            continue
        match = DOCUMENT_CODE.match(path.name)
        key = match.group(1).upper() if match else relative.as_posix().casefold()
        grouped[key].append(path)
    return dict(grouped), skipped


def _version(path: Path) -> int:
    match = VERSION_NUMBER.search(path.name)
    return int(match.group(1)) if match else 1


def _canonical_name(paths: list[Path]) -> str:
    current = [path for path in paths if "historical_versions" not in path.parts]
    return max(current or paths, key=lambda path: (_version(path), path.name)).name


async def _identity(session: AsyncSession, workspace_id: UUID) -> Identity:
    result = await session.execute(
        select(WorkspaceMemberRow, UserRow)
        .join(UserRow, UserRow.id == WorkspaceMemberRow.user_id)
        .where(
            WorkspaceMemberRow.workspace_id == workspace_id,
            WorkspaceMemberRow.role.in_(("WORKSPACE_OWNER", "WORKSPACE_ADMIN")),
            UserRow.enabled.is_(True),
        )
        .order_by(WorkspaceMemberRow.created_at, WorkspaceMemberRow.id)
        .limit(1)
    )
    pair = result.first()
    if pair is None:
        raise RuntimeError("workspace has no enabled owner or administrator")
    _, user = pair
    return Identity(
        user_id=user.id,
        email=user.email,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def _replace_existing(
    sessions: async_sessionmaker[AsyncSession],
    identity: Identity,
    workspace_id: UUID,
    *,
    name_prefixes: tuple[str, ...] = (),
) -> int:
    async with sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(KnowledgeIngestionJobRow)
                    .where(
                        KnowledgeIngestionJobRow.workspace_id == workspace_id,
                        KnowledgeIngestionJobRow.deleted_at.is_(None),
                    )
                    .order_by(
                        KnowledgeIngestionJobRow.document_id,
                        KnowledgeIngestionJobRow.created_at.desc(),
                        KnowledgeIngestionJobRow.id.desc(),
                    )
                )
            ).all()
        )
    latest_by_document: dict[UUID, KnowledgeIngestionJobRow] = {}
    for row in rows:
        if name_prefixes and not row.name.upper().startswith(name_prefixes):
            continue
        latest_by_document.setdefault(row.document_id, row)
    for row in latest_by_document.values():
        async with sessions.begin() as session:
            await delete_knowledge_document(
                session,
                identity=identity,
                workspace_id=workspace_id,
                document_id=row.document_id,
                expected_version=row.version,
                trace_id=f"bulk-replace-{uuid4()}",
            )
    return len(latest_by_document)


async def _wait_terminal(
    sessions: async_sessionmaker[AsyncSession], workspace_id: UUID, ingestion_id: UUID
) -> None:
    for _ in range(600):
        async with sessions() as session:
            state = await session.scalar(
                select(KnowledgeIngestionJobRow.state).where(
                    KnowledgeIngestionJobRow.workspace_id == workspace_id,
                    KnowledgeIngestionJobRow.id == ingestion_id,
                    KnowledgeIngestionJobRow.deleted_at.is_(None),
                )
            )
        if state == "SUCCEEDED":
            return
        if state == "FAILED":
            raise RuntimeError(f"knowledge ingestion failed before next version: {ingestion_id}")
        await asyncio.sleep(1)
    raise TimeoutError(f"knowledge ingestion did not finish: {ingestion_id}")


async def _retry_failed(
    sessions: async_sessionmaker[AsyncSession], identity: Identity, workspace_id: UUID
) -> int:
    async with sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(KnowledgeIngestionJobRow).where(
                        KnowledgeIngestionJobRow.workspace_id == workspace_id,
                        KnowledgeIngestionJobRow.deleted_at.is_(None),
                        KnowledgeIngestionJobRow.state == "FAILED",
                    )
                )
            ).all()
        )
    for row in rows:
        async with sessions.begin() as session:
            await retry_knowledge_ingestion(
                session,
                identity=identity,
                workspace_id=workspace_id,
                ingestion_id=row.id,
                expected_version=row.version,
                trace_id=f"bulk-retry-{uuid4()}",
                task_queue=os.environ.get(
                    "TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"
                ),
            )
    return len(rows)


async def main() -> None:
    args = _arguments()
    source = args.source.resolve()
    if not source.is_dir():
        raise RuntimeError(f"knowledge source directory does not exist: {source}")
    groups, skipped = _discover(source)
    selected = {str(value).upper() for value in args.only}
    if selected:
        groups = {key: paths for key, paths in groups.items() if key.upper() in selected}
    if not groups:
        raise RuntimeError("no supported knowledge documents were found")
    if args.replace_selected and not selected:
        raise RuntimeError("--replace-selected requires at least one --only document code")
    database_url = os.environ["DATABASE_URL"]
    secret = os.environ.get("MINIO_SECRET_KEY")
    if not secret:
        raise RuntimeError("MINIO_SECRET_KEY is required")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ingestor = KnowledgeIngestor(
        Minio(
            os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
            access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
            secret_key=secret,
            secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
        ),
        bucket=os.environ.get("KNOWLEDGE_BUCKET", "enterprise-knowledge"),
    )
    try:
        async with sessions() as session:
            identity = await _identity(session, args.workspace_id)
        if args.retry_failed:
            retried = await _retry_failed(sessions, identity, args.workspace_id)
            print(f"retried_failed={retried}")
            return
        removed = 0
        if args.replace:
            removed = await _replace_existing(sessions, identity, args.workspace_id)
        elif args.replace_selected:
            removed = await _replace_existing(
                sessions,
                identity,
                args.workspace_id,
                name_prefixes=tuple(sorted(selected)),
            )
        batch_id = uuid4()
        submitted = 0
        logical_documents = 0
        for key, paths in sorted(groups.items()):
            logical_documents += 1
            document_id = uuid5(
                NAMESPACE_URL,
                f"knowledge-import:{args.workspace_id}:{batch_id}:{key}",
            )
            canonical_name = _canonical_name(paths)
            ordered_paths = sorted(paths, key=lambda item: (_version(item), item.name))
            for index, path in enumerate(ordered_paths):
                media_type = SUPPORTED_MEDIA_TYPES.get(path.suffix.casefold())
                if media_type is None:
                    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                blob = path.read_bytes()
                source_hash, object_name = await ingestor.stage_blob(
                    workspace_id=args.workspace_id,
                    document_id=document_id,
                    media_type=media_type,
                    blob=blob,
                )
                async with sessions.begin() as session:
                    ingestion = await request_knowledge_ingestion(
                        session,
                        identity=identity,
                        workspace_id=args.workspace_id,
                        document_id=document_id,
                        name=canonical_name,
                        media_type=media_type,
                        source_hash=source_hash,
                        object_name=object_name,
                        parser_version=os.environ.get(
                            "KNOWLEDGE_PARSER_VERSION", "parser-1.0.0+bge-m3"
                        ),
                        trace_id=f"bulk-import-{batch_id}",
                        task_queue=os.environ.get(
                            "TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"
                        ),
                    )
                submitted += 1
                if index < len(ordered_paths) - 1:
                    await _wait_terminal(sessions, args.workspace_id, ingestion.ingestion_id)
        print(
            f"removed={removed} logical_documents={logical_documents} "
            f"submitted_versions={submitted} skipped={len(skipped)} batch_id={batch_id}"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
