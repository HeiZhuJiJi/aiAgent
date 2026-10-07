"""Run the frozen public acceptance questions, preserving every round result."""
import argparse
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

if TYPE_CHECKING:
    from scripts import validate_web_research as runner
    from scripts.validate_screenshot_quality import selected_settings
else:
    try:
        import validate_web_research as runner
        from validate_screenshot_quality import selected_settings
    except ModuleNotFoundError:
        from scripts import validate_web_research as runner
        from scripts.validate_screenshot_quality import selected_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    settings = asyncio.run(selected_settings())
    # These acceptance scripts intentionally inject a controlled settings reader.
    cast(Any, runner).dotenv_values = lambda _: settings
    cases = json.loads(Path("work/verification/fixed-recovery-cases.json").read_text(encoding="utf-8"))
    for case in cases:
        if case["mode"] != "web" or (args.cases and case["id"] not in args.cases):
            continue
        path = Path(f"work/verification/{args.phase}-{case['id']}.json")
        if path.exists():
            raise RuntimeError(f"Refusing to overwrite previous observation: {path}")
        os.environ["WEB_VALIDATION_QUESTION"] = case["question"]
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                runner.main()
            result: dict[str, Any] = json.loads(captured.getvalue())
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            result = {"error": str(exc), "output": captured.getvalue()}
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409 and exc.request.url.path == "/chat/route":
                result["route_error"] = exc.response.json()
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": case["id"], **{key: result.get(key) for key in
                         ("task_id", "state", "elapsed_sec", "answer", "error")}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
