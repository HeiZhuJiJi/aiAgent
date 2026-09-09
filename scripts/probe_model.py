"""Probe the configured real provider using public synthetic input; never print credentials."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from enterprise_agent.contracts.domain import Classification
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import Message, ModelGateway
from enterprise_agent.models.settings import load_model_settings


class ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    sum: Literal[5]


async def main() -> int:
    report: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "scope": "real provider structured output using public synthetic input",
    }
    try:
        config, key = load_model_settings()
        config = config.model_copy(update={"max_output_tokens": 1024, "timeout_sec": 30})
        async with httpx.AsyncClient(trust_env=False) as client:
            answer, usage = await ModelGateway(config, key, client).structured(
                (
                    Message(
                        role="user",
                        content="Connection test. Return status ok and the sum of 2 and 3. No explanation.",
                    ),
                ),
                ProbeAnswer,
                Classification.PUBLIC,
            )
        report.update(
            {
                "result": "PASS",
                "answer": answer.model_dump(),
                "usage": usage.model_dump(),
                "model": config.model,
            }
        )
    except DomainError as error:
        report.update({"result": "FAIL", "failure_code": error.code.value, "detail": error.detail})
    except (ValueError, OSError) as error:
        report.update({"result": "FAIL", "error_type": type(error).__name__})
    path = Path("work/verification/model-live.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
