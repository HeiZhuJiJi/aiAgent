"""Measure real authenticated Agent requests over the fixed fictional policy corpus."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from minio import Minio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Classification
from enterprise_agent.models.gateway import ModelGateway
from enterprise_agent.models.settings import load_embedding_settings
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from scripts.resume_benchmark_data import dataset
from scripts.resume_benchmark_metrics import expected_facts, latency_summary
from scripts.run_resume_benchmark import (
    ROOT,
    append_json,
    emit,
    environment,
    safe_error,
    write_json,
)


async def request(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


async def run(args):
    values = environment()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    data = dataset()
    cases = [q for q in data["queries"] if q["category"] == "chinese"][: args.limit]
    write_json(out / "dataset.json", data)
    write_json(out / "cases.json", cases)
    run_id = str(uuid4())
    config_id = str(uuid4())
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:58080", timeout=150) as api:
        credentials = {
            "email": f"resume-benchmark-{run_id}@example.test",
            "password": str(uuid4()) + "!Bench",
        }
        await request(api, "POST", "/auth/register", json=credentials)
        login = await request(api, "POST", "/auth/login", json=credentials)
        api.headers["Authorization"] = "Bearer " + login["access_token"]
        workspace = await request(
            api, "POST", "/workspaces", json={"name": "Resume benchmark " + run_id}
        )
        workspace_id = str(workspace["workspace_id"])
        await request(
            api,
            "POST",
            "/models",
            json={
                "config_id": config_id,
                "workspace_id": workspace_id,
                "kind": "CHAT",
                "provider": "OpenAICompatible",
                "base_url": values["MODEL_BASE_URL"],
                "model_name": values["MODEL_NAME"],
                "external": urlsplit(str(values["MODEL_BASE_URL"])).scheme == "https",
                "api_key": values["MODEL_API_KEY"],
            },
        )
        package = ROOT / "apps/web/public/builtin-skills/document-analysis.skill.zip"
        quarantined = await request(
            api,
            "POST",
            "/skills/quarantine",
            params={"workspace_id": workspace_id, "name": "document-analysis", "version": "1.0.0"},
            content=package.read_bytes(),
            headers={"Content-Type": "application/zip"},
        )
        installation = str(quarantined["installation_id"])
        installed = await request(
            api,
            "POST",
            "/skills/install",
            json={
                "workspace_id": workspace_id,
                "installation_id": installation,
                "expected_version": quarantined["version"],
            },
        )
        await request(
            api,
            "POST",
            f"/skills/{installation}/enable",
            json={"workspace_id": workspace_id, "expected_version": installed["version"]},
        )
        metadata = {
            "started_at": datetime.now(UTC).isoformat(),
            "workspace_id": workspace_id,
            "model_name": values["MODEL_NAME"],
            "cases": len(cases),
            "documents": len(data["documents"]),
            "dataset_sha256": hashlib.sha256((out / "dataset.json").read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "measurement": "sequential real API routing + task dispatch + durable execution + result materialization",
            "poll_seconds": 0.5,
            "task_budget_seconds": 120,
            "request_timeout_seconds": 150,
            "quality": "automatic expected-fact coverage, not independent semantic correctness",
            "privacy": "only fictional benchmark documents, no existing workspace data",
        }
        write_json(out / "environment.json", metadata)
        cfg, key = load_embedding_settings(
            ROOT / ".env",
            environ={
                "EMBEDDING_MODE": "local",
                "EMBEDDING_BASE_URL": "http://127.0.0.1:8001/v1",
                "EMBEDDING_NAME": "BAAI/bge-m3",
                "EMBEDDING_API_KEY": "",
            },
        )
        ingestor = KnowledgeIngestor(
            Minio(
                "127.0.0.1:59000",
                access_key="platform",
                secret_key=str(values["MINIO_ROOT_PASSWORD"]),
                secure=False,
            )
        )
        async with httpx.AsyncClient(timeout=120) as embedding_client:
            gateway = ModelGateway(cfg, key, client=embedding_client)

            async def embed(texts):
                return await gateway.embed(texts, Classification.PUBLIC)

            for i, document in enumerate(data["documents"]):
                async with sessions.begin() as session:
                    await ingestor.ingest(
                        session,
                        workspace_id=UUID(workspace_id),
                        document_id=uuid4(),
                        name=document["name"],
                        media_type="text/plain",
                        blob=document["content"].encode(),
                        parser_version="resume-benchmark-v1+real-bge-m3",
                        token_counter=lambda value: max(1, len(value.split())),
                        embedder=embed,
                    )
                if (i + 1) % 25 == 0:
                    emit({"phase": "agent_corpus", "indexed": i + 1})
        results = []
        for index, case in enumerate(cases):
            conversation_id, message_id = str(uuid4()), str(uuid4())
            await request(
                api,
                "POST",
                "/conversations",
                json={
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "title": "Benchmark " + case["id"],
                },
            )
            question = "仅根据工作区知识库中的公司制度回答：" + case["question"]
            item = {
                "index": index,
                "case_id": case["id"],
                "question": question,
                "expected": case["expected"],
                "started_at": datetime.now(UTC).isoformat(),
                "success": False,
            }
            start = time.perf_counter()
            task_id = None
            try:
                route = await request(
                    api,
                    "POST",
                    "/chat/route",
                    json={
                        "workspace_id": workspace_id,
                        "conversation_id": conversation_id,
                        "user_message_id": message_id,
                        "content": question,
                        "web_mode": "OFF",
                        "model_config_id": config_id,
                    },
                )
                item["routing_ms"] = (time.perf_counter() - start) * 1000
                item["route"] = route
                if route.get("route") != "RAG":
                    raise AssertionError("Expected RAG route, got " + str(route.get("route")))
                resolved = route.get("resolved_content") or question
                context = {
                    "original_request": question,
                    "resolved_request": resolved,
                    "query": resolved,
                    "retrieval_queries": route.get("research_plan", {}).get(
                        "retrieval_queries", []
                    ),
                    "limit": route.get("search_limit") or 5,
                    "instruction": resolved,
                    "research_plan": route.get("research_plan"),
                    "agent_decision": route.get("agent_decision"),
                    "model_config_id": config_id,
                    "web_search_requested": False,
                }
                turn = await request(
                    api,
                    "POST",
                    "/research-turns",
                    json={
                        "workspace_id": workspace_id,
                        "conversation_id": conversation_id,
                        "user_message_id": message_id,
                        "content": question,
                        "goal": {
                            "goal": question,
                            "constraints": ["仅使用工作区内的虚构测试资料，不使用外部知识"],
                            "deliverables": ["直接回答问题并提供可核对的知识依据"],
                            "workspace_id": workspace_id,
                            "time_budget_sec": 120,
                            "token_budget": 24000,
                            "cost_budget": 40,
                            "requested_skill": "document-analysis",
                            "user_context": json.dumps(context, ensure_ascii=False),
                        },
                    },
                )
                task_id = str(turn["task"]["task_id"])
                item["task_id"] = task_id
                deadline = time.monotonic() + 150
                task = turn["task"]
                while (
                    str(task["state"]) not in {"COMPLETED", "FAILED", "CANCELLED"}
                    and time.monotonic() < deadline
                ):
                    await asyncio.sleep(0.5)
                    task = await request(api, "GET", f"/workspaces/{workspace_id}/tasks/{task_id}")
                item["task_state"] = task["state"]
                if task["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    raise TimeoutError("Task not terminal at benchmark deadline")
                response = await request(
                    api,
                    "POST",
                    f"/conversations/{conversation_id}/task-results/{task_id}",
                    json={"workspace_id": workspace_id},
                )
                item["answer"] = response.get("content", "")
                item["end_to_end_ms"] = (time.perf_counter() - start) * 1000
                item["facts"] = expected_facts(item["answer"], case["expected"])
                item["success"] = task["state"] == "COMPLETED" and item["facts"]["all_matched"]
            except Exception as exc:  # noqa: BLE001 - failed answers must remain in the results
                item["error"] = safe_error(exc, values)
                item["end_to_end_ms"] = (time.perf_counter() - start) * 1000
            if task_id is not None:
                for field, path, params in [
                    ("evidence", f"/workspaces/{workspace_id}/tasks/{task_id}/evidence", None),
                    ("attempts", f"/workspaces/{workspace_id}/tasks/{task_id}/attempts", None),
                    ("completion", f"/workspaces/{workspace_id}/tasks/{task_id}/completion", None),
                    ("usage", "/usage", {"workspace_id": workspace_id, "task_id": task_id}),
                ]:
                    try:
                        item[field] = await request(api, "GET", path, params=params)
                    except Exception as exc:  # noqa: BLE001 - report missing telemetry explicitly
                        item[field + "_error"] = safe_error(exc, values)
                item["task_tokens"] = sum(x.get("tokens", 0) for x in item.get("usage", []))
            results.append(item)
            append_json(out / "answers.jsonl", item)
            summary = {
                "attempted": len(results),
                "planned": len(cases),
                "task_completed": sum(x.get("task_state") == "COMPLETED" for x in results),
                "all_expected_facts_matched_and_completed": sum(x["success"] for x in results),
                "all_attempts_latency": latency_summary([x["end_to_end_ms"] for x in results]),
                "successful_answers_latency": latency_summary(
                    [x["end_to_end_ms"] for x in results if x["success"]]
                ),
                "task_tokens_total": sum(x.get("task_tokens", 0) for x in results),
                "token_scope": "Task usage only; planner tokens may be recorded outside Task scope",
            }
            write_json(out / "summary.json", summary)
            emit(
                {
                    "phase": "agent_case",
                    "index": index,
                    "case_id": case["id"],
                    "state": item.get("task_state"),
                    "success": item["success"],
                    "latency_ms": item["end_to_end_ms"],
                    "error": item.get("error"),
                    "facts": item.get("facts"),
                    "task_tokens": item.get("task_tokens"),
                }
            )
    await engine.dispose()
    emit({"phase": "agent_complete", **summary})
    return len(results) == len(cases) and all(item["success"] for item in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.limit <= 12:
        parser.error("limited to the first 1..12 fixed cases")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
