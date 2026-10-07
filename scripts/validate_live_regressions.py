"""Real model probes for the subsequent user-reported weather and DOCX failures."""
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

if TYPE_CHECKING:
    from scripts import validate_rag_cases, validate_web_research
    from scripts.validate_screenshot_quality import selected_settings
else:
    try:
        import validate_rag_cases
        import validate_web_research
        from validate_screenshot_quality import selected_settings
    except ModuleNotFoundError:
        from scripts import validate_rag_cases, validate_web_research
        from scripts.validate_screenshot_quality import selected_settings

OUT = Path("work/verification/live-regressions")
QUESTIONS = {"miami_future": "下周迈阿密的天气怎么样", "dongguan_history": "上周五的东莞的天气怎么样"}


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="fixed")
    parser.add_argument("--cases", nargs="+", default=[*QUESTIONS, "docx"])
    args = parser.parse_args()
    settings = asyncio.run(selected_settings())
    cast(Any, validate_web_research).dotenv_values = lambda _: settings
    cast(Any, validate_rag_cases).dotenv_values = lambda _: settings
    failed = False
    OUT.mkdir(parents=True, exist_ok=True)
    for case in args.cases:
        captured = io.StringIO()
        result: dict[str, Any]
        if case == "docx":
            cast(Any, validate_rag_cases).DOCUMENTS = ("RAG项目说明.docx",)
            sys.argv = ["validate_rag_cases.py", "C:/Users/10190/Desktop/123", "--summary-document", "--summary-question", "帮我总结一下这个文件", "--summary-topics", "RAG", "检索", "Ollama"]
            with contextlib.redirect_stdout(captured):
                code = validate_rag_cases.main()
            records = [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]
            result = next(record for record in records if "question" in record)
            result["records"] = records
            errors = ["document pipeline failed"] if code else []
            if "RAG项目说明.docx" not in (result.get("answer") or ""):
                errors.append("missing original DOCX source citation")
        else:
            os.environ["WEB_VALIDATION_QUESTION"] = QUESTIONS[case]
            with contextlib.redirect_stdout(captured):
                validate_web_research.main()
            result = json.loads(captured.getvalue())
            errors = []
        answer = result.get("answer") or ""
        if result.get("state") != "COMPLETED":
            errors.append("task did not complete")
        if len(answer) < 50 or any(term in answer[:120] for term in ("证据不足", "未能安全完成", "无法提供", "不能可靠")):
            errors.append("missing useful substantive response")
        if case in QUESTIONS:
            if "https://" not in answer:
                errors.append("missing source URL")
            if not any(unit in answer for unit in ("°F", "℃", "°C", "华氏", "摄氏")):
                errors.append("missing weather values and units")
        if case == "miami_future" and not any(day in answer for day in ("9月21", "9/21", "09-21")):
            errors.append("missing forecast date in requested week")
        if case == "dongguan_history" and not any(day in answer for day in ("9月11", "9/11", "09-11")):
            errors.append("wrong historical date")
        if case == "dongguan_history" and result.get("state") == "COMPLETED":
            from enterprise_agent.capabilities.builtins import _grounded_fact_tokens
            from enterprise_agent.runtime.reviewer import _normalized_fact_text
            rows = []
            for item in result.get("evidence", []):
                content = item.get("content", "")
                match = re.search(r"(?m)^9/11/2026\s*$", content)
                if match:
                    remainder = content[match.start():]
                    next_row = re.search(r"(?m)^9/(?!11/)\d{1,2}/2026\s*$", remainder)
                    rows.append(remainder[:next_row.start()] if next_row else remainder[:350])
            if rows:
                supported = _normalized_fact_text("\n".join(rows))
                answer_body = answer.split("参考来源：", 1)[0]
                # Unit conversion is acceptable only when the original source value is
                # also disclosed and the displayed conversion is arithmetically correct.
                disclosed_conversions = set()
                for fahrenheit, celsius in re.findall(r"(-?\d+(?:\.\d+)?)\s*°F[（(]约?(-?\d+(?:\.\d+)?)\s*°C[）)]", answer_body):
                    if _normalized_fact_text(fahrenheit + "°F") in supported and abs((float(fahrenheit) - 32) * 5 / 9 - float(celsius)) <= 0.55:
                        disclosed_conversions.add(_normalized_fact_text(celsius + "°C"))
                unsupported = [token for token in _grounded_fact_tokens(answer_body) if token not in supported and token not in disclosed_conversions]
                if unsupported:
                    errors.append("values absent from the requested daily record: " + ", ".join(unsupported))

        result.update(case=case, quality_ok=not errors, acceptance_failures=errors)
        # Do not nest a result inside its own captured records.
        if case == "docx":
            result["records"] = [record for record in records if record is not result]
        (OUT / f"{args.phase}-{case}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: result.get(key) for key in ("case", "task_id", "state", "elapsed_sec", "answer", "quality_ok", "acceptance_failures")}, ensure_ascii=False), flush=True)
        failed |= bool(errors)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
