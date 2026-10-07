import json
import os
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:58080"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


def required_setting(settings: Mapping[str, str | None], name: str) -> str:
    value = settings.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


def post(client: httpx.Client, path: str, **kwargs: Any) -> dict[str, object]:
    response = client.post(path, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise TypeError(f"unexpected response from {path}")
    return value


def duration_seconds(attempt: dict[str, object]) -> float | None:
    started = attempt.get("started_at")
    completed = attempt.get("completed_at")
    if not isinstance(started, str) or not isinstance(completed, str):
        return None
    return round(
        (
            datetime.fromisoformat(completed)
            - datetime.fromisoformat(started)
        ).total_seconds(),
        3,
    )


def main() -> int:
    settings = dict(dotenv_values(ROOT / ".env"))
    model_base_url = required_setting(settings, "MODEL_BASE_URL")
    model_name = required_setting(settings, "MODEL_NAME")
    model_api_key = required_setting(settings, "MODEL_API_KEY")
    parsed_model_url = urlsplit(model_base_url)
    external = parsed_model_url.scheme == "https"
    run_id = uuid4()
    email = f"web-validation-{run_id}@example.test"
    password = "controlled web validation credential"
    question = os.environ.get(
        "WEB_VALIDATION_QUESTION",
        "今天广州黄埔的天气怎么样",
    )

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        post(client, "/auth/register", json={"email": email, "password": password})
        login = post(client, "/auth/login", json={"email": email, "password": password})
        token = login.get("access_token")
        if not isinstance(token, str):
            raise TypeError("login response did not contain an access token")
        client.headers["Authorization"] = "Bearer " + token
        workspace = post(client, "/workspaces", json={"name": "Live Web Validation"})
        workspace_id = str(workspace["workspace_id"])
        conversation_id = str(uuid4())
        post(
            client,
            "/conversations",
            json={
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "title": "Live Web Validation",
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
        package = ROOT / "apps" / "web" / "public" / "builtin-skills" / "web-research.skill.zip"
        quarantined = post(
            client,
            "/skills/quarantine",
            params={
                "workspace_id": workspace_id,
                "name": "web-research",
                "version": "1.0.1",
            },
            content=package.read_bytes(),
            headers={"Content-Type": "application/zip"},
        )
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
            json={
                "workspace_id": workspace_id,
                "expected_version": installed["version"],
            },
        )
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
            },
        )
        if route.get("route") != "WEB":
            raise RuntimeError(f"expected WEB route, received {route!r}")
        resolved_request = str(route.get("resolved_content") or question)
        query = str(route.get("search_query") or resolved_request)
        search_queries = route.get("search_queries")
        if not isinstance(search_queries, list):
            search_queries = []
        research_plan = route.get("research_plan")
        agent_decision = route.get("agent_decision")
        if not isinstance(research_plan, dict):
            raise TypeError("WEB route did not include a bounded research plan")
        if agent_decision is not None and not isinstance(agent_decision, dict):
            raise TypeError("WEB route included an invalid Agent decision")
        started = time.perf_counter()
        research_turn = post(
            client,
            "/research-turns",
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "content": question,
                "goal": {
                    "goal": question,
                    "constraints": [
                        "只使用当前 Skill 声明并通过治理的 Capability",
                        "使用工作流中声明的 web.search 获取最新公开信息",
                    ],
                    "deliverables": [
                        "输出自然、完整且带有可审计 Claim-Evidence 关联的回答",
                    ],
                    "workspace_id": workspace_id,
                    "time_budget_sec": 180,
                    "token_budget": 120000,
                    "cost_budget": 40,
                    "requested_skill": "web-research",
                    "user_context": json.dumps(
                        {
                            "original_request": question,
                            "resolved_request": resolved_request,
                            "query": query,
                            "search_queries": search_queries,
                            "limit": route.get("search_limit"),
                            "instruction": resolved_request,
                            "research_plan": research_plan,
                            "agent_decision": agent_decision,
                            "model_config_id": config_id,
                            "web_search_requested": True,
                        },
                        ensure_ascii=False,
                    ),
                },
            },
        )
        task = research_turn.get("task")
        if not isinstance(task, dict):
            raise TypeError("research Turn response did not include a task")
        task_id = str(task["task_id"])
        state = str(task["state"])
        deadline = time.monotonic() + 190
        while state not in TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(1)
            response = client.get(f"/workspaces/{workspace_id}/tasks/{task_id}")
            response.raise_for_status()
            state = str(response.json()["state"])

        attempts_response = client.get(
            f"/workspaces/{workspace_id}/tasks/{task_id}/attempts"
        )
        attempts_response.raise_for_status()
        attempts = attempts_response.json()
        evidence_response = client.get(
            f"/workspaces/{workspace_id}/tasks/{task_id}/evidence"
        )
        evidence_response.raise_for_status()
        evidence = evidence_response.json()
        usage_response = client.get(
            "/usage", params={"workspace_id": workspace_id, "task_id": task_id}
        )
        usage_response.raise_for_status()
        usages = usage_response.json()
        trace_response = client.get(
            f"/tasks/{task_id}/trace", params={"workspace_id": workspace_id}
        )
        trace_response.raise_for_status()
        answer = None
        if state in TERMINAL_STATES:
            result_message = post(
                client,
                f"/conversations/{conversation_id}/task-results/{task_id}",
                json={"workspace_id": workspace_id},
            )
            answer = result_message.get("content")
        summary = {
            "task_id": task_id,
            "state": state,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "model_provider": "OpenAICompatible",
            "model_name": model_name,
            "route": route,
            "answer": answer,
            "attempts": [
                {
                    "state": item.get("state"),
                    "duration_sec": duration_seconds(item),
                    "failure": item.get("failure"),
                    "trace_id": item.get("trace_id"),
                }
                for item in attempts
            ],
            "usage": [
                {
                    "kind": item.get("kind"),
                    "tokens": item.get("tokens"),
                    "cost": item.get("cost"),
                    "detail": item.get("detail"),
                }
                for item in usages
            ],
            "evidence_count": len(evidence),
            "evidence": evidence,
            "trace_node_count": len(trace_response.json().get("nodes", [])),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if state == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
