"""Real-dependency retrieval and load benchmark; never prints environment secrets."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Classification
from enterprise_agent.models.gateway import ModelGateway
from enterprise_agent.models.settings import load_embedding_settings
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.runtime.retrieval import reciprocal_rank_fuse
from enterprise_agent.storage.knowledge import lexical_candidates, semantic_candidates
from enterprise_agent.storage.tables import Workspace
from scripts.resume_benchmark_data import dataset
from scripts.resume_benchmark_metrics import latency_summary, retrieval_metrics

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def emit(value):
    print(json.dumps(value, ensure_ascii=True, default=str), flush=True)


def append_json(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, default=str) + chr(10))


def environment():
    values = dotenv_values(ROOT / ".env")
    for name, user, port, db, key in [
        ("TEST_DATABASE_URL", "platform", 55432, "platform", "POSTGRES_PASSWORD"),
        (
            "PROCUREMENT_DATABASE_URL",
            "procurement",
            55433,
            "procurement",
            "PROCUREMENT_DB_PASSWORD",
        ),
    ]:
        os.environ[name] = URL.create(
            "postgresql+asyncpg",
            username=user,
            password=values[key],
            host="127.0.0.1",
            port=port,
            database=db,
        ).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["REDIS_URL"] = "redis://127.0.0.1:56379/0"
    os.environ["ADMISSION_NAMESPACE"] = "benchmark:" + str(uuid4())
    return values


def safe_error(exc, values):
    message = str(exc)[:1200]
    for key, value in values.items():
        if value and any(term in key.upper() for term in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
            message = message.replace(str(value), "[REDACTED]")
    return {"type": type(exc).__name__, "message": message}


async def run_retrieval(args):
    values = environment()
    out = args.output.resolve()
    if out.exists():
        raise RuntimeError("output directory already exists; choose a fresh run ID")
    out.mkdir(parents=True)
    data = dataset()
    write_json(out / "dataset.json", data)
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=12, max_overflow=4)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with httpx.AsyncClient(timeout=15) as metadata_client:
        metadata_response = await metadata_client.get("http://127.0.0.1:8001/info")
        metadata_response.raise_for_status()
        embedding_info = metadata_response.json()
    config, key = load_embedding_settings(
        ROOT / ".env",
        environ={
            "EMBEDDING_MODE": "local",
            "EMBEDDING_BASE_URL": "http://127.0.0.1:8001/v1",
            "EMBEDDING_NAME": embedding_info["model_id"],
            "EMBEDDING_API_KEY": "",
        },
    )
    config = config.model_copy(update={"base_url": "http://127.0.0.1:8001/v1", "timeout_sec": 120})
    workspace = uuid4()
    info = {
        "started_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "embedding_model": config.model,
        "embedding_dimensions": 1024,
        "embedding_device": "CPU",
        "embedding_server_info": embedding_info,
        "workspace_id": str(workspace),
        "documents": len(data["documents"]),
        "queries": len(data["queries"]),
        "candidate_limit": 50,
        "rrf_k": 60,
        "reranker": "not used",
        "dataset_sha256": hashlib.sha256((out / "dataset.json").read_bytes()).hexdigest(),
        "code_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                ROOT / "scripts/resume_benchmark_data.py",
                ROOT / "scripts/resume_benchmark_metrics.py",
                ROOT / "src/enterprise_agent/storage/knowledge.py",
                ROOT / "src/enterprise_agent/runtime/retrieval.py",
            ]
        },
        "git_head": (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            )
        ).strip(),
        "dirty_worktree": bool(
            await asyncio.to_thread(
                subprocess.check_output, ["git", "status", "--porcelain"], cwd=ROOT
            )
        ),
        "cache": "warm model and database; no query embedding cache; one explicit unmeasured warmup",
        "scope": "component retrieval, not end-to-end Agent; synthetic English policies with cross-language queries",
    }
    async with httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=20)) as client:
        gateway = ModelGateway(config, key, client=client)

        async def embed(texts):
            return await gateway.embed(tuple(texts), Classification.PUBLIC)

        info["embedding_health"] = (await client.get("http://127.0.0.1:8001/health")).status_code
        async with sessions.begin() as session:
            session.add(Workspace(id=workspace, name="Resume benchmark " + out.name))
            info["postgres_version"] = await session.scalar(text("select version()"))
        ingestor = KnowledgeIngestor(
            Minio(
                "127.0.0.1:59000",
                access_key="platform",
                secret_key=str(values["MINIO_ROOT_PASSWORD"]),
                secure=False,
            )
        )
        versions = {}
        index_started = time.perf_counter()
        for i, document in enumerate(data["documents"]):
            async with sessions.begin() as session:
                version = await ingestor.ingest(
                    session,
                    workspace_id=workspace,
                    document_id=uuid4(),
                    name=document["name"],
                    media_type="text/plain",
                    blob=document["content"].encode(),
                    parser_version="resume-benchmark-v1+real-bge-m3",
                    token_counter=lambda value: max(1, len(value.split())),
                    embedder=embed,
                )
            versions[str(version)] = document["id"]
            if (i + 1) % 20 == 0:
                emit({"phase": "index", "documents": i + 1})
        info["index_wall_seconds"] = time.perf_counter() - index_started
        info["versions"] = versions
        async with sessions() as session:
            info["chunks"] = await session.scalar(
                text("select count(*) from knowledge_chunks where workspace_id=:w"),
                {"w": workspace},
            )
        write_json(out / "environment.json", info)

        async def retrieve(query):
            start = time.perf_counter()
            embedding = (await embed((query,)))[0]
            embedded = time.perf_counter()
            async with sessions() as session:
                lexical = await lexical_candidates(session, workspace, query, limit=50)
                lexical_done = time.perf_counter()
                semantic = await semantic_candidates(session, workspace, embedding, limit=50)
            semantic_done = time.perf_counter()
            fused = reciprocal_rank_fuse(lexical, semantic, candidate_limit=50, result_limit=10)
            done = time.perf_counter()
            return {
                "rankings": {
                    "lexical": [versions[x.source_id] for x in lexical[:10]],
                    "vector": [versions[x.source_id] for x in semantic[:10]],
                    "hybrid": [versions[x.candidate.source_id] for x in fused],
                },
                "latency_ms": (done - start) * 1000,
                "embedding_ms": (embedded - start) * 1000,
                "lexical_ms": (lexical_done - embedded) * 1000,
                "semantic_ms": (semantic_done - lexical_done) * 1000,
                "fusion_ms": (done - semantic_done) * 1000,
            }

        await retrieve(data["queries"][0]["question"])
        order = list(data["queries"])
        random.Random(20260929).shuffle(order)
        observations = []
        for index, query in enumerate(order):
            item = {
                "query_id": query["id"],
                "category": query["category"],
                "gold": query["gold_document"],
            }
            try:
                item.update(await retrieve(query["question"]))
                item["success"] = True
            except Exception as exc:  # noqa: BLE001 - persist every failed benchmark observation
                item.update(
                    success=False,
                    error=safe_error(exc, values),
                    rankings={x: [] for x in ("lexical", "vector", "hybrid")},
                )
            append_json(out / "retrieval.jsonl", item)
            observations.append(item)
            if (index + 1) % 10 == 0:
                emit({"phase": "accuracy", "queries": index + 1})
        quality = {}
        for category in ("all", "code", "english", "chinese"):
            subset = [x for x in observations if category == "all" or x["category"] == category]
            quality[category] = {
                method: retrieval_metrics(
                    [x["rankings"][method] for x in subset], [x["gold"] for x in subset]
                )
                for method in ("lexical", "vector", "hybrid")
            }
        write_json(out / "retrieval_summary.json", quality)
        emit({"phase": "quality_summary", "result": quality})

        loads = []
        for concurrency in args.concurrency:
            semaphore = asyncio.Semaphore(concurrency)
            jobs = list(data["queries"]) * args.repeats
            random.Random(20260929 + concurrency).shuffle(jobs)

            async def sample(index, query, limiter=semaphore, level=concurrency):
                async with limiter:
                    start = time.perf_counter()
                    item = {"concurrency": level, "index": index, "query_id": query["id"]}
                    try:
                        result = await retrieve(query["question"])
                        item.update({k: v for k, v in result.items() if k != "rankings"})
                        item["success"] = True
                        item["gold_in_top5"] = (
                            query["gold_document"] in result["rankings"]["hybrid"][:5]
                        )
                    except Exception as exc:  # noqa: BLE001 - failures stay in the load denominator
                        item.update(
                            success=False,
                            latency_ms=(time.perf_counter() - start) * 1000,
                            error=safe_error(exc, values),
                        )
                    append_json(out / "load.jsonl", item)
                    return item

            start = time.perf_counter()
            samples = await asyncio.gather(*(sample(i, q) for i, q in enumerate(jobs)))
            wall = time.perf_counter() - start
            passed = [x for x in samples if x["success"]]
            summary = {
                "concurrency": concurrency,
                "requests": len(samples),
                "successful": len(passed),
                "wall_seconds": wall,
                "successful_requests_per_second": len(passed) / wall,
                "all_requests_latency": latency_summary([x["latency_ms"] for x in samples]),
                "success_latency": latency_summary([x["latency_ms"] for x in passed]),
                "embedding_latency": latency_summary([x["embedding_ms"] for x in passed]),
            }
            loads.append(summary)
            write_json(out / "load_summary.json", loads)
            emit({"phase": "load_summary", **summary})
    await engine.dispose()
    emit({"phase": "complete", "output": str(out)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 4, 8])
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if any(x < 1 or x > 8 for x in args.concurrency) or not 1 <= args.repeats <= 5:
        parser.error("bounded workload requires concurrency 1..8 and repeats 1..5")
    asyncio.run(run_retrieval(args))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
