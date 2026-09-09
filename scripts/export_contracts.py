import inspect
import json
from pathlib import Path

from enterprise_agent.contracts import domain
from enterprise_agent.contracts.domain import Contract

for name, cls in inspect.getmembers(domain, inspect.isclass):
    if issubclass(cls, Contract) and cls is not Contract:
        Path(f"contracts/{name}.schema.json").write_text(
            json.dumps(cls.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
print("Generated schemas from Pydantic contracts.")
