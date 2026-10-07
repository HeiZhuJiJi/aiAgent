"""Measure real fast-path chat Turns without mock calls."""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

if TYPE_CHECKING:
    from scripts.validate_rag_cases import post
else:
    try:
        from validate_rag_cases import post
    except ModuleNotFoundError:
        from scripts.validate_rag_cases import post

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clock", action="store_true", help="probe the live clock fast path")
    parser.add_argument("--identity", action="store_true", help="verify application identity")
    parser.add_argument("--morning", action="store_true", help="verify screenshot greeting")
    parser.add_argument("--question", help="probe an ordinary explanatory chat question")
    args = parser.parse_args()
    settings = dotenv_values(ROOT / ".env")
    base_url = str(settings.get("MODEL_BASE_URL") or "").strip()
    name = str(settings.get("MODEL_NAME") or "").strip()
    key = str(settings.get("MODEL_API_KEY") or "").strip()
    if not base_url or not name or not key:
        raise RuntimeError("selected model environment configuration is incomplete")
    question = (
        "介绍一下你自己"
        if args.identity
        else "早上好"
        if args.morning
        else "中午好，现在几点？"
        if args.clock
        else "晚上好"
    )
    question = args.question or question
    run_id = uuid4()
    with httpx.Client(base_url="http://127.0.0.1:58080", timeout=90) as client:
        post(
            client,
            "/auth/register",
            json={
                "email": f"greeting-validation-{run_id}@example.test",
                "password": "controlled greeting validation credential",
            },
        )
        token = post(
            client,
            "/auth/login",
            json={
                "email": f"greeting-validation-{run_id}@example.test",
                "password": "controlled greeting validation credential",
            },
        )["access_token"]
        client.headers["Authorization"] = "Bearer " + str(token)
        workspace_id = str(
            post(client, "/workspaces", json={"name": "Greeting Quality Validation"})[
                "workspace_id"
            ]
        )
        conversation_id = str(uuid4())
        post(
            client,
            "/conversations",
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "title": "Greeting Quality Validation",
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
                "base_url": base_url,
                "model_name": name,
                "external": urlsplit(base_url).scheme == "https",
                "api_key": key,
            },
        )
        started = time.perf_counter()
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
        route_seconds = time.perf_counter() - started
        if route.get("route") != "DIRECT":
            raise RuntimeError(f"greeting routed to {route.get('route')}")
        chunks: list[str] = []
        first_token_seconds: float | None = None
        event = ""
        data: list[str] = []
        with client.stream(
            "POST",
            "/chat/stream",
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "model_config_id": config_id,
                "user_message_id": user_message_id,
                "content": question,
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    payload = json.loads("\n".join(data))
                    if event == "token" and isinstance(payload.get("content"), str):
                        if first_token_seconds is None:
                            first_token_seconds = time.perf_counter() - started
                        chunks.append(payload["content"])
                    elif event == "error":
                        raise RuntimeError(str(payload.get("message")))
                    data = []
                    event = ""
        answer = "".join(chunks)
        clock_match = re.search(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{2}):(\d{2})",
            answer,
        )
        clock_close = False
        if args.clock and clock_match is not None:
            timezone_name = str(settings.get("APP_TIMEZONE") or "Asia/Shanghai")
            year, month, day, hour, minute = (
                int(part) for part in clock_match.groups()
            )
            reported = datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=ZoneInfo(timezone_name),
            )
            clock_close = (
                abs((datetime.now(ZoneInfo(timezone_name)) - reported).total_seconds()) < 120
            )
        result = {
            "route": route["route"],
            "route_sec": round(route_seconds, 3),
            "first_token_sec": (
                round(first_token_seconds, 3) if first_token_seconds is not None else None
            ),
            "total_sec": round(time.perf_counter() - started, 3),
            "answer": answer,
            "quality_ok": (
                bool(answer.strip()) and len(answer) >= 80 and "RAG" in answer and "检索" in answer
                if args.question
                else "现在是" in answer and clock_close
                if args.clock
                else (
                    "Agent Work" in answer
                    and "Claude" not in answer
                    and "Anthropic" not in answer
                    and "ChatGPT" not in answer
                )
                if args.identity
                else "早上好" in answer
                if args.morning
                else "晚上好" in answer
            ),
        }
        print(json.dumps(result))
        return 0 if result["quality_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
