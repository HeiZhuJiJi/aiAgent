import json
from pathlib import Path

from enterprise_agent.api.app import app


def main() -> None:
    target = Path("contracts/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Exported OpenAPI to {target}")


if __name__ == "__main__":
    main()
