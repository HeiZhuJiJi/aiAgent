import hashlib
import io
import json
import os
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from docx import Document
from minio import Minio
from pydantic import Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities.registry import CapabilityRegistry
from enterprise_agent.contracts.domain import (
    ArtifactManifest,
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    Claim,
    Classification,
    Contract,
    Evidence,
    Failure,
    FailureCode,
    RetryPolicy,
    ReviewResult,
    Risk,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import Message, ModelGateway
from enterprise_agent.models.reranker import RerankerGateway
from enterprise_agent.models.settings import (
    load_embedding_settings,
    load_model_settings,
    load_reranker_settings,
)
from enterprise_agent.runtime.budgets import BudgetDelta, BudgetKind, reserve_budget
from enterprise_agent.runtime.retrieval import hybrid_rrf_rerank
from enterprise_agent.runtime.reviewer import review_artifact
from enterprise_agent.runtime.web_provider import HttpWebProvider, load_web_provider_config
from enterprise_agent.storage.artifacts import ArtifactStore
from enterprise_agent.storage.attempts import list_task_evidence
from enterprise_agent.storage.knowledge import lexical_candidates, semantic_candidates
from enterprise_agent.storage.provenance import load_claims, persist_claim
from enterprise_agent.storage.tables import TaskRow


class ClaimDraft(Contract):
    content: str = Field(min_length=1)
    importance: Literal["LOW", "MEDIUM", "HIGH"]
    risk_level: Risk
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class StructuredAnalysis(Contract):
    decision: str = Field(pattern="^(ADMIT|REJECT|NEEDS_MORE_EVIDENCE)$")
    risk_level: str = Field(pattern="^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    summary: str = Field(min_length=1)
    key_findings: tuple[str, ...]
    missing_information: tuple[str, ...]
    recommendation: str = Field(min_length=1)
    evidence_sufficient: bool
    claims: tuple[ClaimDraft, ...]


class PersistedClaim(ClaimDraft):
    claim_id: UUID


class StructuredAnalysisResult(Contract):
    decision: str = Field(pattern="^(ADMIT|REJECT|NEEDS_MORE_EVIDENCE)$")
    risk_level: str = Field(pattern="^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    summary: str = Field(min_length=1)
    key_findings: tuple[str, ...]
    missing_information: tuple[str, ...]
    recommendation: str = Field(min_length=1)
    evidence_sufficient: bool
    claims: tuple[PersistedClaim, ...]


def _schema(model: type[Contract]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], model.model_json_schema())


def _object_schema(
    properties: dict[str, JsonValue], required: tuple[str, ...]
) -> dict[str, JsonValue]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _descriptor(
    *,
    capability_id: str,
    name: str,
    kind: str,
    input_schema: dict[str, JsonValue],
    output_schema: dict[str, JsonValue],
    provider: str,
    side_effect_mode: str = "NONE",
    risk: Risk = Risk.READ,
) -> CapabilityDescriptor:
    return CapabilityDescriptor.model_validate(
        {
            "id": capability_id,
            "name": name,
            "version": "1.0.0",
            "kind": kind,
            "description": name,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "tags": ["builtin"],
            "risk_level": risk.value,
            "side_effect_mode": side_effect_mode,
            "idempotency_mode": (
                "READ_ONLY" if side_effect_mode == "NONE" else "NATIVE_IDEMPOTENCY"
            ),
            "compensation_mode": "NONE",
            "compensation_capability": None,
            "reconciliation_capability": None,
            "timeout_sec": 120,
            "retry_policy": RetryPolicy(
                max_attempts=3,
                initial_delay_sec=0.25,
                max_delay_sec=3,
            ).model_dump(mode="json"),
            "required_permissions": ["knowledge.read"],
            "approval_policy": "NONE",
            "provider": provider,
            "health_status": "ACTIVE",
            "cost_hint": 0,
        }
    )


def builtin_descriptors() -> tuple[CapabilityDescriptor, ...]:
    search_hits = _object_schema(
        {
            "query": {"type": "string"},
            "hits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "source_id",
                        "locator",
                        "content",
                        "content_hash",
                        "score",
                    ],
                    "properties": {
                        "source_id": {"type": "string"},
                        "locator": {"type": "string"},
                        "content": {"type": "string"},
                        "content_hash": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                        "score": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        ("query", "hits"),
    )
    web_results = _object_schema(
        {
            "query": {"type": "string"},
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title", "url", "snippet", "content_hash"],
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "snippet": {"type": "string"},
                        "content_hash": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        ("query", "results"),
    )
    return (
        _descriptor(
            capability_id="knowledge.hybrid_search",
            name="PostgreSQL FTS, pgvector, RRF and BGE rerank",
            kind="RAG",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                ("query",),
            ),
            output_schema=search_hits,
            provider="postgresql-bge",
        ),
        _descriptor(
            capability_id="web.search",
            name="Configured Web search",
            kind="WEB",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                ("query",),
            ),
            output_schema=web_results,
            provider="configured-web",
        ),
        _descriptor(
            capability_id="web.fetch",
            name="Configured Web fetch",
            kind="WEB",
            input_schema=_object_schema(
                {"url": {"type": "string", "format": "uri"}},
                ("url",),
            ),
            output_schema=_object_schema(
                {
                    "url": {"type": "string"},
                    "content": {"type": "string"},
                    "content_hash": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                },
                ("url", "content", "content_hash"),
            ),
            provider="configured-web",
        ),
        _descriptor(
            capability_id="model.structured_output",
            name="Governed structured model analysis",
            kind="MODEL",
            input_schema=_object_schema(
                {"instruction": {"type": "string", "minLength": 1}},
                ("instruction",),
            ),
            output_schema=_schema(StructuredAnalysisResult),
            provider="configured-model",
            side_effect_mode="INTERNAL",
            risk=Risk.WRITE,
        ),
        _descriptor(
            capability_id="runtime.review",
            name="Deterministic Claim and Evidence reviewer",
            kind="BUILTIN",
            input_schema=_object_schema(
                {"instruction": {"type": "string", "minLength": 1}},
                ("instruction",),
            ),
            output_schema=cast(dict[str, JsonValue], ReviewResult.model_json_schema()),
            provider="enterprise-runtime",
        ),
        _descriptor(
            capability_id="artifact.docx",
            name="Immutable DOCX report",
            kind="FILE",
            input_schema=_object_schema(
                {"instruction": {"type": "string", "minLength": 1}},
                ("instruction",),
            ),
            output_schema=cast(
                dict[str, JsonValue], ArtifactManifest.model_json_schema()
            ),
            provider="minio-python-docx",
            side_effect_mode="INTERNAL",
            risk=Risk.WRITE,
        ),
        _descriptor(
            capability_id="artifact.markdown",
            name="Immutable Markdown report",
            kind="FILE",
            input_schema=_object_schema(
                {"instruction": {"type": "string", "minLength": 1}},
                ("instruction",),
            ),
            output_schema=cast(
                dict[str, JsonValue], ArtifactManifest.model_json_schema()
            ),
            provider="minio-markdown",
            side_effect_mode="INTERNAL",
            risk=Risk.WRITE,
        ),
    )


async def _reserve_llm(
    sessions: async_sessionmaker[AsyncSession],
    request: CapabilityRequest,
    *,
    units: int = 1,
    tokens: int = 0,
) -> None:
    async with sessions.begin() as session:
        decision = await reserve_budget(
            session,
            request.workspace_id,
            request.task_id,
            request.attempt_id,
            "builtin:" + str(request.command_id),
            BudgetDelta(kind=BudgetKind.LLM_CALL, units=units, tokens=tokens),
        )
    if not decision.allowed:
        raise DomainError(FailureCode.BUDGET_EXCEEDED, "built-in model budget exceeded")


def _success(request: CapabilityRequest, output: JsonValue) -> CapabilityResult:
    return CapabilityResult(
        operation_id=request.operation_id,
        outcome="SUCCEEDED",
        output=output,
    )


def load_artifact_store() -> ArtifactStore:
    secret = os.environ.get("MINIO_SECRET_KEY")
    if not secret:
        raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "artifact store is not configured")
    return ArtifactStore(
        Minio(
            os.environ.get("MINIO_ENDPOINT", "127.0.0.1:59000"),
            access_key=os.environ.get("MINIO_ACCESS_KEY", "platform"),
            secret_key=secret,
            secure=os.environ.get("MINIO_SECURE", "false").casefold() == "true",
        ),
        bucket=os.environ.get("ARTIFACT_BUCKET", "enterprise-artifacts"),
    )


async def _knowledge_handler(
    sessions: async_sessionmaker[AsyncSession], request: CapabilityRequest
) -> CapabilityResult:
    query = cast(str, request.arguments["query"])
    limit_value = request.arguments.get("limit", 10)
    if not isinstance(limit_value, int):
        raise DomainError(FailureCode.VALIDATION_FAILED, "knowledge limit must be an integer")
    limit = limit_value
    embedding_config, embedding_key = load_embedding_settings()
    reranker_config, reranker_key = load_reranker_settings()
    await _reserve_llm(sessions, request)
    async with httpx.AsyncClient(trust_env=False) as embedding_client:
        embedding = (
            await ModelGateway(
                embedding_config, embedding_key, embedding_client
            ).embed((query,), Classification.INTERNAL)
        )[0]
    async with sessions() as session:
        lexical = await lexical_candidates(session, request.workspace_id, query)
        semantic = await semantic_candidates(
            session, request.workspace_id, embedding
        )
    candidates = {
        item.chunk_id: item for item in tuple(lexical) + tuple(semantic)
    }
    ordered = tuple(candidates[key] for key in sorted(candidates))
    if not ordered:
        return _success(request, {"query": query, "hits": []})
    await _reserve_llm(sessions, request)
    async with httpx.AsyncClient(trust_env=False) as rerank_client:
        scores = await RerankerGateway(
            ModelGateway(reranker_config, reranker_key, rerank_client)
        ).score(
            query,
            tuple(item.content for item in ordered),
            Classification.INTERNAL,
        )
    by_id = {item.chunk_id: score for item, score in zip(ordered, scores, strict=True)}
    hits = hybrid_rrf_rerank(
        query,
        lexical,
        semantic,
        reranker=lambda _query, values: [by_id[item.chunk_id] for item in values],
        result_limit=limit,
    )
    return _success(
        request,
        {
            "query": query,
            "hits": [
                {
                    "source_id": hit.candidate.source_id,
                    "locator": hit.candidate.locator,
                    "content": hit.candidate.content,
                    "content_hash": hashlib.sha256(
                        hit.candidate.content.encode()
                    ).hexdigest(),
                    "score": hit.rerank_score,
                }
                for hit in hits
            ],
        },
    )


async def _web_search_handler(request: CapabilityRequest) -> CapabilityResult:
    query = cast(str, request.arguments["query"])
    limit_value = request.arguments.get("limit", 10)
    if not isinstance(limit_value, int):
        raise DomainError(FailureCode.VALIDATION_FAILED, "Web limit must be an integer")
    limit = limit_value
    async with httpx.AsyncClient(trust_env=False) as client:
        results = await HttpWebProvider(load_web_provider_config(), client).search(
            query, limit
        )
    for item in results:
        if hashlib.sha256(item.snippet.encode()).hexdigest() != item.content_hash:
            raise DomainError(FailureCode.CAPABILITY_BAD_OUTPUT, "Web result hash mismatch")
    return _success(
        request,
        {
            "query": query,
            "results": [
                item.model_dump(mode="json", exclude={"schema_version"})
                for item in results
            ],
        },
    )


async def _web_fetch_handler(request: CapabilityRequest) -> CapabilityResult:
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await HttpWebProvider(load_web_provider_config(), client).fetch(
            cast(str, request.arguments["url"])
        )
    if hashlib.sha256(result.content.encode()).hexdigest() != result.content_hash:
        raise DomainError(FailureCode.CAPABILITY_BAD_OUTPUT, "Web content hash mismatch")
    return _success(
        request,
        cast(JsonValue, result.model_dump(mode="json", exclude={"schema_version"})),
    )


async def _model_handler(
    sessions: async_sessionmaker[AsyncSession], request: CapabilityRequest
) -> CapabilityResult:
    async with sessions() as session:
        evidence = await list_task_evidence(
            session, request.workspace_id, request.task_id
        )
    if not evidence:
        raise DomainError(FailureCode.VALIDATION_FAILED, "structured analysis has no Evidence")
    config, key = load_model_settings()
    async with httpx.AsyncClient(trust_env=False) as client:
        gateway = ModelGateway(config, key, client)
        analysis, usage = await gateway.structured(
            (
                Message(
                    role="system",
                    content=(
                        "Produce a supplier risk assessment using only the supplied Evidence. "
                        "Unknown facts must remain unknown. Every Claim must cite one or more "
                        "provided evidence_ids. Return JSON only."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "instruction": request.arguments["instruction"],
                            "evidence": [
                                {
                                    "evidence_id": str(item.evidence_id),
                                    "source_type": item.source_type,
                                    "source_id": item.source_id,
                                    "locator": item.locator,
                                    "content": item.content,
                                    "content_hash": item.content_hash,
                                }
                                for item in evidence
                            ],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            ),
            StructuredAnalysis,
            Classification.INTERNAL,
            before_call=lambda: _reserve_llm(sessions, request),
        )
    await _reserve_llm(
        sessions,
        request,
        units=0,
        tokens=usage.input_tokens + usage.output_tokens,
    )
    evidence_ids = {item.evidence_id for item in evidence}
    claim_ids: list[UUID] = []
    async with sessions.begin() as session:
        for index, draft in enumerate(analysis.claims):
            if not set(draft.evidence_ids) <= evidence_ids:
                raise DomainError(
                    FailureCode.CAPABILITY_BAD_OUTPUT,
                    "model Claim cited Evidence outside the Task",
                )
            claim_id = uuid5(
                NAMESPACE_URL,
                f"builtin-claim:{request.workspace_id}:{request.command_id}:{index}",
            )
            claim = Claim(
                claim_id=claim_id,
                workspace_id=request.workspace_id,
                task_id=request.task_id,
                content=draft.content,
                importance=draft.importance,
                risk_level=draft.risk_level,
                evidence_ids=draft.evidence_ids,
            )
            await persist_claim(session, claim)
            claim_ids.append(claim_id)
    result = StructuredAnalysisResult(
        **analysis.model_dump(exclude={"claims"}),
        claims=tuple(
            PersistedClaim(**draft.model_dump(), claim_id=claim_id)
            for draft, claim_id in zip(analysis.claims, claim_ids, strict=True)
        ),
    )
    return _success(request, cast(JsonValue, result.model_dump(mode="json")))


async def _review_handler(
    sessions: async_sessionmaker[AsyncSession], request: CapabilityRequest
) -> CapabilityResult:
    async with sessions() as session:
        claims = await load_claims(session, request.workspace_id, request.task_id)
        evidence = await list_task_evidence(
            session, request.workspace_id, request.task_id
        )
    draft = {
        "claims": [item.model_dump(mode="json") for item in claims],
        "evidence_count": len(evidence),
    }
    encoded = json.dumps(
        draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    review = review_artifact(
        ArtifactManifest(
            artifact_id=uuid5(NAMESPACE_URL, f"review-draft:{request.command_id}"),
            workspace_id=request.workspace_id,
            source_task=request.task_id,
            storage_uri="pending://review-draft",
            sha256=hashlib.sha256(encoded).hexdigest(),
            media_type="application/json",
            claim_ids=tuple(item.claim_id for item in claims),
            created_at=datetime.now(UTC),
        ),
        cast(dict[str, JsonValue], draft),
        _object_schema(
            {
                "claims": {"type": "array", "minItems": 1},
                "evidence_count": {"type": "integer", "minimum": 1},
            },
            ("claims", "evidence_count"),
        ),
        ("claims",),
        claims,
        evidence,
    )
    if not review.accepted:
        return CapabilityResult(
            operation_id=request.operation_id,
            outcome="FAILED",
            output=review.model_dump(mode="json"),
            failure=Failure(
                code=FailureCode.CAPABILITY_BAD_OUTPUT,
                safe_detail="Reviewer rejected unsupported or incomplete Claims",
                trace_id="builtin:" + str(request.command_id),
            ),
        )
    return _success(request, cast(JsonValue, review.model_dump(mode="json")))


def _report_markdown(
    task_goal: str, claims: tuple[Claim, ...], evidence: tuple[Evidence, ...]
) -> str:
    lines = [
        "# Supplier Risk Report",
        "",
        "## Executive Summary",
        task_goal,
        "",
        "## Supplier Profile",
        "See cited supplier profile Evidence.",
        "",
        "## Risk Rating",
        "Derived from the governed Claims below.",
        "",
        "## Risk Matrix",
        "| Importance | Risk | Finding |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {claim.importance} | {claim.risk_level.value} | {claim.content} |"
        for claim in claims
    )
    lines.extend(["", "## Key Findings"])
    lines.extend(f"- {claim.content}" for claim in claims)
    lines.extend(["", "## Evidence"])
    lines.extend(
        f"- {item.evidence_id}: {item.source_type} {item.locator}"
        for item in evidence
    )
    lines.extend(
        [
            "",
            "## Missing Information",
            "See unresolved Claims and Reviewer findings.",
            "",
            "## Recommendation",
            "Use the reviewed decision and obtain approval before any admission action.",
            "",
            "## Citation",
        ]
    )
    lines.extend(
        f"- Claim {claim.claim_id}: "
        + ", ".join(str(item) for item in claim.evidence_ids)
        for claim in claims
    )
    return "\n".join(lines) + "\n"


def _docx(markdown: str) -> bytes:
    document = Document()
    for line in markdown.splitlines():
        if line.startswith("# "):
            document.add_heading(line[2:], level=0)
        elif line.startswith("## "):
            document.add_heading(line[3:], level=1)
        elif line.startswith("- "):
            document.add_paragraph(line[2:], style="List Bullet")
        elif line:
            document.add_paragraph(line)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


async def _artifact_handler(
    sessions: async_sessionmaker[AsyncSession],
    request: CapabilityRequest,
    *,
    media_type: str,
) -> CapabilityResult:
    async with sessions() as session:
        row = await session.get(TaskRow, request.task_id)
        claims = await load_claims(session, request.workspace_id, request.task_id)
        evidence = await list_task_evidence(
            session, request.workspace_id, request.task_id
        )
    if (
        row is None
        or row.workspace_id != request.workspace_id
        or row.goal_payload is None
        or not claims
        or not evidence
    ):
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "artifact requires Task goal, reviewed Claims and Evidence",
        )
    goal = cast(str, row.goal_payload["goal"])
    markdown = _report_markdown(goal, claims, evidence)
    content = markdown.encode() if media_type == "text/markdown" else _docx(markdown)
    artifact_id = uuid5(
        NAMESPACE_URL,
        f"builtin-artifact:{request.workspace_id}:{request.command_id}:{media_type}",
    )
    async with sessions.begin() as session:
        manifest = await load_artifact_store().put(
            session,
            artifact_id=artifact_id,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            content=content,
            media_type=media_type,
            claim_ids=tuple(item.claim_id for item in claims),
        )
    return _success(request, cast(JsonValue, manifest.model_dump(mode="json")))


def register_builtin_capabilities(
    registry: CapabilityRegistry,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    descriptors = {item.id: item for item in builtin_descriptors()}
    registry.register(
        descriptors["knowledge.hybrid_search"],
        lambda request: _knowledge_handler(sessions, request),
    )
    registry.register(descriptors["web.search"], _web_search_handler)
    registry.register(descriptors["web.fetch"], _web_fetch_handler)
    registry.register(
        descriptors["model.structured_output"],
        lambda request: _model_handler(sessions, request),
    )
    registry.register(
        descriptors["runtime.review"],
        lambda request: _review_handler(sessions, request),
    )
    registry.register(
        descriptors["artifact.docx"],
        lambda request: _artifact_handler(
            sessions,
            request,
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        ),
    )
    registry.register(
        descriptors["artifact.markdown"],
        lambda request: _artifact_handler(
            sessions, request, media_type="text/markdown"
        ),
    )
