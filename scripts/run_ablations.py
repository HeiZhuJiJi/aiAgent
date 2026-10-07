import json
from pathlib import Path

from enterprise_agent.evaluation.ablation import render_ablation_report, run_ablations


def main() -> int:
    output = Path("work/verification/evaluation")
    output.mkdir(parents=True, exist_ok=True)
    report = run_ablations()
    (output / "ablation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "ablation-report.md").write_text(
        render_ablation_report(report), encoding="utf-8"
    )
    print(f"Ablations: {len(report['results'])}/6 completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
