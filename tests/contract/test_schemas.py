import inspect
import json
from pathlib import Path

from enterprise_agent.contracts import domain
from enterprise_agent.contracts.domain import Contract


def test_generated_contract_schemas_are_current():
    for name, cls in inspect.getmembers(domain, inspect.isclass):
        if issubclass(cls, Contract) and cls is not Contract:
            assert (
                json.loads(Path(f"contracts/{name}.schema.json").read_text(encoding="utf-8"))
                == cls.model_json_schema()
            )


def test_frozen_spec_has_all_sections_and_matching_digest():
    import hashlib
    import re

    source = Path("docs/spec/00-master-spec.md").read_bytes()
    digest = Path("docs/spec/SOURCE.sha256").read_text(encoding="utf-8-sig").split()[0]
    assert hashlib.sha256(source).hexdigest().upper() == digest
    text = source.decode("utf-8-sig")
    assert sorted(map(int, re.findall(r"^# (\d+)\.", text, re.MULTILINE))) == list(range(86))
    matrix = Path("SPEC_COMPLIANCE_MATRIX.md").read_text(encoding="utf-8")
    for prefix in ("INV", "ACC"):
        for number in range(1, 16):
            assert f"{prefix}-{number:03}" in matrix
