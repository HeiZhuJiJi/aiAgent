"""Authorized original-HR acceptance against the screenshot-selected model."""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from scripts import validate_rag_cases
    from scripts.validate_screenshot_quality import OUT, selected_settings
else:
    try:
        import validate_rag_cases
        from validate_screenshot_quality import OUT, selected_settings
    except ModuleNotFoundError:
        from scripts import validate_rag_cases
        from scripts.validate_screenshot_quality import OUT, selected_settings


def assess(case: str, result: dict[str, Any]) -> list[str]:
    answer = result.get("answer") or ""
    errors = []
    if result.get("state") != "COMPLETED" or result.get("route") != "RAG":
        errors.append("original-document task did not complete via RAG")
    if "HR-02_leave_policy_v2.pdf" not in answer or "第" not in answer:
        errors.append("missing original policy citation with locator")
    if case == "movie":
        if "事假" not in answer or not any(term in answer for term in ("无薪", "不带薪")):
            errors.append("missing applicable personal-leave policy")
        if not any(term in answer for term in ("1个工作日", "一个工作日", "1 个工作日")):
            errors.append("missing original personal-leave notice period")
        if "年假" not in answer or "余额" not in answer:
            errors.append("missing conditional annual-leave alternative")
        if not any(term in answer for term in ("3个工作日", "三个工作日", "3 个工作日")):
            errors.append("missing original annual-leave notice period")
        if "应申请事假" in answer or "必须申请事假" in answer:
            errors.append("inappropriately mandates unpaid personal leave")
        if "审批" not in answer or "交接" not in answer:
            errors.append("missing actionable application/approval requirements")
    else:
        for term in ("年假", "事假", "病假", "审批", "紧急", "销假"):
            if term not in answer:
                errors.append("missing summary topic: " + term)
        if not any(term in answer for term in ("3个工作日", "3 个工作日", "三个工作日")):
            errors.append("missing original annual-leave notice period")
        if not any(term in answer for term in ("2个工作日", "2 个工作日", "两个工作日")):
            errors.append("missing emergency make-up period")
    if any(len(paragraph) > 450 for paragraph in answer.split("\n")):
        errors.append("unreadable single-paragraph policy dump")
    if "至少提前两个工作日" in answer:
        errors.append("incorrect generic notice period from unrelated fixture")
    return errors


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--phase", default="original_hr_authorized")
    args = parser.parse_args()
    settings = asyncio.run(selected_settings())
    cast(Any, validate_rag_cases).dotenv_values = lambda _path: settings
    cast(Any, validate_rag_cases).DOCUMENTS = (
        "hr/HR-02_leave_policy_v2.pdf",
        "templates/TPL-08_employee_leave_application.md",
    )
    cast(Any, validate_rag_cases).QUESTIONS = ("我想要请假看电影",)
    sources = [
        {"name": name, "sha256": hashlib.sha256((args.source_root / name).read_bytes()).hexdigest()}
        for name in validate_rag_cases.DOCUMENTS
    ]
    failed = False
    for case in ("movie", "attachment"):
        captured = io.StringIO()
        sys.argv = ["validate_rag_cases.py", str(args.source_root)]
        if case == "attachment":
            sys.argv.append("--summary-document")
        with contextlib.redirect_stdout(captured):
            status = validate_rag_cases.main()
        records = [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]
        result = next(record for record in records if "question" in record)
        errors = assess(case, result)
        if status:
            errors.append("underlying pipeline acceptance failed")
        if case == "attachment" and not any(record.get("attachment_detached") for record in records):
            errors.append("unrelated follow-up retained attachment scope")
        payload = {"case": case, "model_name": settings["MODEL_NAME"], "sources": sources,
                   "records": records, "acceptance_failures": errors, "quality_ok": not errors}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{args.phase}-{case}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        failed |= bool(errors)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
