import asyncio
import hashlib
import io
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

from minio import Minio
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.document_parser import ParsedPart, parse_document
from enterprise_agent.storage.knowledge import (
    ChunkInput,
    find_document_version_replay,
    persist_document_version,
)

TokenCounter = Callable[[str], int]
Embedder = Callable[[tuple[str, ...]], Awaitable[tuple[tuple[float, ...], ...]]]


def chunk_parts(
    parts: tuple[ParsedPart, ...],
    *,
    token_counter: TokenCounter,
    max_characters: int = 1500,
    overlap_characters: int = 150,
) -> tuple[tuple[str, dict[str, str | int], int], ...]:
    if max_characters <= 0 or not 0 <= overlap_characters < max_characters:
        raise ValueError("invalid chunk size or overlap")
    chunks: list[tuple[str, dict[str, str | int], int]] = []
    stride = max_characters - overlap_characters
    for part in parts:
        for segment, start in enumerate(range(0, len(part.content), stride), start=1):
            content = part.content[start : start + max_characters].strip()
            if not content:
                continue
            locator = dict(part.locator)
            locator["segment"] = segment
            tokens = token_counter(content)
            if tokens <= 0:
                raise DomainError(FailureCode.VALIDATION_FAILED, "tokenizer returned no tokens")
            chunks.append((content, locator, tokens))
            if start + max_characters >= len(part.content):
                break
    return tuple(chunks)


class KnowledgeIngestor:
    def __init__(self, client: Minio, bucket: str = "enterprise-knowledge") -> None:
        self._client = client
        self._bucket = bucket

    async def stage_blob(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        media_type: str,
        blob: bytes,
    ) -> tuple[str, str]:
        source_hash = hashlib.sha256(blob).hexdigest()
        object_name = f"{workspace_id}/{document_id}/{source_hash}"
        exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        if not exists:
            await asyncio.to_thread(self._client.make_bucket, self._bucket)
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_name,
            io.BytesIO(blob),
            len(blob),
            content_type=media_type,
            metadata={"sha256": source_hash},
        )
        return source_hash, object_name

    async def load_blob(self, object_name: str, expected_hash: str) -> bytes:
        def read() -> bytes:
            response = self._client.get_object(self._bucket, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        blob = await asyncio.to_thread(read)
        if hashlib.sha256(blob).hexdigest() != expected_hash:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "knowledge object hash mismatch")
        return blob

    async def ingest(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        document_id: UUID,
        name: str,
        media_type: str,
        blob: bytes,
        parser_version: str,
        token_counter: TokenCounter,
        embedder: Embedder,
    ) -> UUID:
        source_hash, object_name = await self.stage_blob(
            workspace_id=workspace_id,
            document_id=document_id,
            media_type=media_type,
            blob=blob,
        )
        storage_uri = f"s3://{self._bucket}/{object_name}"
        replay = await find_document_version_replay(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            name=name,
            media_type=media_type,
            source_hash=source_hash,
            storage_uri=storage_uri,
            parser_version=parser_version,
        )
        if replay is not None:
            return replay
        parsed = parse_document(blob, media_type)
        chunks = chunk_parts(parsed, token_counter=token_counter)
        embeddings = await embedder(tuple(item[0] for item in chunks))
        if len(embeddings) != len(chunks):
            raise DomainError(FailureCode.LLM_SCHEMA_ERROR, "embedding count mismatch")
        inputs = tuple(
            ChunkInput(content, cast(dict[str, JsonValue], locator), tokens, embedding)
            for (content, locator, tokens), embedding in zip(chunks, embeddings, strict=True)
        )
        return await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            name=name,
            media_type=media_type,
            source_hash=source_hash,
            storage_uri=storage_uri,
            parser_version=parser_version,
            chunks=inputs,
        )
