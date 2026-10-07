"""Run scoped, real-model RAG acceptance with explicitly authorized HR files."""

import argparse
import json
import mimetypes
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:58080"
DOCUMENTS = (
    "hr/HR-02_leave_policy_v2.pdf",
    "hr/HR-05_onboarding_probation_policy_v1.md",
    "templates/TPL-08_employee_leave_application.md",
)
QUESTIONS = (
    "我想请假三天，流程是什么？",
    "请假流程是什么？",
    "入职流程是什么？",
    "请假流程",
)
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def post(client: httpx.Client, path: str, **kwargs: Any) -> dict[str, Any]:
    response = client.post(path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{path} returned {response.status_code}: {response.text[:1000]}")
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise TypeError(f"unexpected response from {path}")
    return value


def get(client: httpx.Client, path: str, **kwargs: Any) -> dict[str, Any]:
    response = client.get(path, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise TypeError(f"unexpected response from {path}")
    return value


def wait_ingestion(client: httpx.Client, workspace_id: str, ingestion_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        view = get(
            client,
            f"/knowledge/documents/{ingestion_id}",
            params={"workspace_id": workspace_id},
        )
        if view["state"] == "SUCCEEDED":
            return view
        if view["state"] == "FAILED":
            raise RuntimeError(f"ingestion failed for {view['name']}: {view['safe_error']}")
        time.sleep(1)
    raise TimeoutError(f"ingestion deadline reached: {ingestion_id}")


def wait_task(client: httpx.Client, workspace_id: str, task_id: str) -> str:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        state = str(get(client, f"/workspaces/{workspace_id}/tasks/{task_id}")["state"])
        if state in TERMINAL:
            return state
        time.sleep(1)
    return "WAITING_AT_DEADLINE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--question-index", type=int, choices=range(len(QUESTIONS)))
    parser.add_argument("--summary-document", action="store_true")
    parser.add_argument("--summary-question", default="帮我总结一下这份请假制度文档")
    parser.add_argument("--summary-topics", nargs="+", default=["年假", "病假", "事假", "审批"])
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    selected = tuple(source_root / relative for relative in DOCUMENTS)
    if any(not path.is_file() for path in selected):
        raise FileNotFoundError("one of the three scoped HR validation files is missing")
    settings = dotenv_values(ROOT / ".env")
    model_base_url = str(settings.get("MODEL_BASE_URL") or "").strip()
    model_name = str(settings.get("MODEL_NAME") or "").strip()
    model_api_key = str(settings.get("MODEL_API_KEY") or "").strip()
    if not all((model_base_url, model_name, model_api_key)):
        raise RuntimeError("selected model environment configuration is incomplete")
    external = urlsplit(model_base_url).scheme == "https"
    run_id = uuid4()
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        email = f"rag-validation-{run_id}@example.test"
        password = "controlled rag validation credential"
        post(client, "/auth/register", json={"email": email, "password": password})
        token = post(client, "/auth/login", json={"email": email, "password": password})[
            "access_token"
        ]
        client.headers["Authorization"] = "Bearer " + str(token)
        workspace_id = str(
            post(client, "/workspaces", json={"name": "Scoped HR RAG Acceptance"})["workspace_id"]
        )
        conversation_id = str(uuid4())
        post(
            client,
            "/conversations",
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "title": "Scoped HR RAG Acceptance",
            },
        )
        config_id = str(uuid4())
        post(
            client,
            "/models",
            json={
                "config_id": config_id,
                "workspace_id": workspace_id,
                "kind": "CHAT",
                "provider": "OpenAICompatible",
                "base_url": model_base_url,
                "model_name": model_name,
                "external": external,
                "api_key": model_api_key,
            },
        )
        package = ROOT / "apps/web/public/builtin-skills/document-analysis.skill.zip"
        quarantined = post(
            client,
            "/skills/quarantine",
            params={"workspace_id": workspace_id, "name": "document-analysis", "version": "1.0.0"},
            content=package.read_bytes(),
            headers={"Content-Type": "application/zip"},
        )
        print(json.dumps({"skill_state": quarantined["state"]}), flush=True)
        installation_id = str(quarantined["installation_id"])
        installed = post(
            client,
            "/skills/install",
            json={
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "expected_version": quarantined["version"],
            },
        )
        post(
            client,
            f"/skills/{installation_id}/enable",
            json={"workspace_id": workspace_id, "expected_version": installed["version"]},
        )
        ingestions = []
        for path in selected:
            media_type = mimetypes.guess_type(path.name)[0] or "text/plain"
            if path.suffix.casefold() == ".md":
                media_type = "text/markdown"
            view = post(
                client,
                "/knowledge/documents",
                params={
                    "workspace_id": workspace_id,
                    "document_id": str(uuid4()),
                    "name": path.name,
                },
                content=path.read_bytes(),
                headers={"Content-Type": media_type},
            )
            ingestions.append(view)
        imported = [
            wait_ingestion(client, workspace_id, str(view["ingestion_id"])) for view in ingestions
        ]
        print(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "model_name": model_name,
                    "imported": [
                        {"name": view["name"], "state": view["state"]} for view in imported
                    ],
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        all_passed = True
        questions = (
            (args.summary_question,)
            if args.summary_document
            else (QUESTIONS[args.question_index],)
            if args.question_index is not None
            else QUESTIONS
        )
        selected_document_version_id = (
            str(imported[0]["document_version_id"]) if args.summary_document else None
        )
        for question in questions:
            user_message_id = str(uuid4())
            route = post(
                client,
                "/chat/route",
                json={
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "user_message_id": user_message_id,
                    "content": question,
                    "web_mode": "AUTO",
                    "model_config_id": config_id,
                    **(
                        {"document_version_id": selected_document_version_id}
                        if selected_document_version_id is not None
                        else {}
                    ),
                },
            )
            if route.get("route") != "RAG":
                print(json.dumps({"question": question, "route": route}, ensure_ascii=True))
                all_passed = False
                continue
            resolved = str(route.get("resolved_content") or question)
            user_context = {
                "original_request": question,
                "resolved_request": resolved,
                "query": resolved,
                "retrieval_queries": route.get("research_plan", {}).get("retrieval_queries", []),
                "limit": route.get("search_limit") or 5,
                "instruction": resolved,
                "research_plan": route.get("research_plan"),
                "agent_decision": route.get("agent_decision"),
                "model_config_id": config_id,
                "web_search_requested": False,
                **(
                    {"document_version_id": selected_document_version_id}
                    if selected_document_version_id is not None
                    else {}
                ),
            }
            started = time.perf_counter()
            turn = post(
                client,
                "/research-turns",
                json={
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "user_message_id": user_message_id,
                    "content": question,
                    "goal": {
                        "goal": question,
                        "constraints": ["仅使用工作区内已获授权的知识库资料"],
                        "deliverables": ["直接回答问题并提供可核对的知识依据"],
                        "workspace_id": workspace_id,
                        "time_budget_sec": 90,
                        "token_budget": 24000,
                        "cost_budget": 40,
                        "requested_skill": "document-analysis",
                        "user_context": json.dumps(user_context, ensure_ascii=False),
                    },
                },
            )
            task_id = str(turn["task"]["task_id"])
            state = wait_task(client, workspace_id, task_id)
            attempts_response = client.get(f"/workspaces/{workspace_id}/tasks/{task_id}/attempts")
            attempts_response.raise_for_status()
            attempts = attempts_response.json()
            answer = None
            if state == "COMPLETED":
                answer = post(
                    client,
                    f"/conversations/{conversation_id}/task-results/{task_id}",
                    json={"workspace_id": workspace_id},
                ).get("content")
            result = {
                "question": question,
                "route": route["route"],
                "state": state,
                "task_id": task_id,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "answer": answer,
                "attempts": [
                    {
                        "state": item.get("state"),
                        "duration_sec": round(
                            (
                                datetime.fromisoformat(item["completed_at"])
                                - datetime.fromisoformat(item["started_at"])
                            ).total_seconds(),
                            3,
                        )
                        if item.get("completed_at") and item.get("started_at")
                        else None,
                    }
                    for item in attempts
                ],
            }
            quality_ok = (
                state == "COMPLETED"
                and isinstance(answer, str)
                and 20 <= len(answer) <= 1500
                and "原文摘录" not in answer
                and (
                    all(term in answer for term in args.summary_topics)
                    if args.summary_document
                    else (
                        any(term in answer for term in ("请假", "事假", "年假"))
                        and "审批" in answer and "交接" in answer
                        if "请假" in question else "入职" in answer
                    )
                )
            )
            result["quality_ok"] = quality_ok
            print(json.dumps(result, ensure_ascii=True), flush=True)
            all_passed &= quality_ok
        if args.summary_document:
            followup = post(
                client,
                "/chat/route",
                json={
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "user_message_id": str(uuid4()),
                    "content": "现在的日本首相是谁？",
                    "web_mode": "AUTO",
                    "model_config_id": config_id,
                },
            )
            detached = (
                followup.get("route") == "WEB"
                and "PUBLIC_WEB"
                in followup.get("research_plan", {})
                .get("source_plan", {})
                .get("required_sources", [])
            )
            print(
                json.dumps(
                    {"followup_route": followup.get("route"), "attachment_detached": detached},
                    ensure_ascii=True,
                ),
                flush=True,
            )
            all_passed &= detached
        return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
