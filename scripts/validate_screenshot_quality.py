"""Public screenshot probes using the actual selected model, without document uploads."""

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import httpx
from dotenv import dotenv_values
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.storage.model_configs import load_model_gateway_config

if TYPE_CHECKING:
    from scripts import validate_web_research
else:
    try:
        import validate_web_research
    except ModuleNotFoundError:
        from scripts import validate_web_research

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/verification/screenshot-quality"
CASES = {
    "guangzhou": "我想知道的明天广州的天气",
    "huangpu": "明天广州黄埔的天气呢",
    "president": "现在的美国总统是谁",
    "branches": "美国三权分立是哪三权，最高领导人分别是谁",
    "match": "迈阿密vs蓝十字的最新结果是什么",
    "latest_match": "迈阿密国际的最新比赛是什么，结果怎么样",
    "history_weather": "迈阿密上周的天气怎么样",
}


def assess_case(case: str, result: dict[str, Any]) -> list[str]:
    """Necessary observable checks; passing still requires source/date manual review."""
    failures = []
    answer = str(result.get("answer") or "")
    if result.get("state") != "COMPLETED":
        failures.append("task did not complete")
    lead = answer.split("\n\n", 1)[0]
    if not answer or any(word in lead for word in ("无法确认", "证据不足", "未能安全完成", "无法提供")):
        failures.append("missing substantive answer or unresolved core facts")
    if "参考来源" not in answer:
        failures.append("missing source links")
    if case in {"guangzhou", "huangpu"}:
        if not re.search(r"9[月/-]18|2026-09-18", answer) or not re.search(r"℃|摄氏|°C", answer):
            failures.append("missing requested forecast date or temperature")
        if case == "huangpu" and "黄埔" not in answer:
            failures.append("missing district identity")
    elif case == "president":
        if "总统" not in answer or len(answer.split("参考来源")[0]) < 12:
            failures.append("missing officeholder answer")
    elif case == "branches":
        if "无法确认" in answer or "证据未" in answer:
            failures.append("an institutional core role is unresolved")
        if not all(term in answer for term in ("行政", "立法", "司法", "总统", "众议院", "参议院", "首席大法官")):
            failures.append("missing one of the institutional roles")
    elif case in {"match", "latest_match"}:
        if not re.search(r"\d+\s*[-:：]\s*\d+", answer):
            failures.append("missing match score")
        if not re.search(r"20\d{2}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}", answer):
            failures.append("missing calendar event date; latest cannot be established")
        # Public live-source snapshot verified on 2026-09-17. Refresh this oracle
        # for a future evaluation date; these values are never production answers.
        if not re.search(r"2\s*[-:：]\s*0", answer) or not re.search(r"Cruz Azul|蓝十字", answer, re.IGNORECASE):
            failures.append("does not match the latest verified public result snapshot")
        local_date = re.search(r"2026\s*[年/-]\s*0?9\s*[月/-]\s*16", answer)
        china_date = "北京时间" in answer and re.search(r"2026\s*[年/-]\s*0?9\s*[月/-]\s*17", answer)
        if not (local_date or china_date):
            failures.append("match date conflicts with local-date snapshot or omits timezone")
    elif case == "history_weather":
        if not re.search(r"9\s*[月/-]\s*0?7", answer) or not re.search(r"9\s*[月/-]\s*13", answer):
            failures.append("missing full requested week")
        if not any(unit in answer for unit in ("°F", "℃", "华氏", "摄氏")) or "降水" not in answer:
            failures.append("missing weather measurements and units")
    return failures


async def selected_settings() -> dict[str, str]:
    raw_values = dotenv_values(ROOT / ".env")
    values = {key: value for key, value in raw_values.items() if value is not None}
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="platform",
            password=values["POSTGRES_PASSWORD"],
            host="127.0.0.1",
            port=55432,
            database="platform",
        )
    )
    try:
        async with async_sessionmaker(engine)() as session:
            config, key, _ = await load_model_gateway_config(
                session,
                UUID("0ec1ac35-528f-4354-81dc-ef84360c37d9"),
                UUID("6abc3b9e-136d-4147-bc73-326b31def319"),
                SecretCipher(values["SECRET_MASTER_KEY"]),
            )
            values.update(
                MODEL_BASE_URL=config.base_url,
                MODEL_NAME=config.model,
                MODEL_API_KEY=key.get_secret_value(),
            )
    finally:
        await engine.dispose()
    return values


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="baseline")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    settings = asyncio.run(selected_settings())
    # Test-runner configuration override only; the deployed runtime uses its real provider.
    cast(Any, validate_web_research).dotenv_values = lambda path: settings
    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    for case in args.cases:
        os.environ["WEB_VALIDATION_QUESTION"] = CASES[case]
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                validate_web_research.main()
            result = json.loads(captured.getvalue())
        except (RuntimeError, ValueError, TypeError, TimeoutError, httpx.HTTPError) as exc:
            result = {"error": str(exc)}
        result.update(case=case, question=CASES[case])
        result["acceptance_failures"] = assess_case(case, result)
        result["quality_ok"] = not result["acceptance_failures"]
        failed = failed or not result["quality_ok"]
        (OUT / f"{args.phase}-{case}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: result.get(k)
                    for k in ("case", "task_id", "state", "elapsed_sec", "answer", "quality_ok", "acceptance_failures", "error")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
