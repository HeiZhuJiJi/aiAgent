import io
import json
import zipfile

import pytest

from enterprise_agent.domain.states import DomainError
from enterprise_agent.skills.package import validate_package
from tests.security.test_policy import policy


def archive(extra=None):
    manifest = {
        "name": "supplier-risk",
        "version": "1.0.0",
        "description": "Supplier risk",
        "runtime_contract": 1,
        "dependencies": [{"capability_id": "admission", "version_range": ">=1,<2"}],
    }
    files = {
        "skill.yaml": json.dumps(manifest),
        "instructions.md": "Analyze supplier evidence.",
        "workflow.yaml": json.dumps(
            {
                "steps": [
                    {
                        "step_key": "admit",
                        "objective": "request admission",
                        "dependencies": [],
                        "capability_requirements": [
                            {"capability_id": "admission", "version_range": ">=1,<2"}
                        ],
                        "expected_output": "request",
                        "success_criteria": ["created"],
                        "risk_hint": "EXTERNAL_SIDE_EFFECT",
                        "execution_mode": "CAPABILITY",
                        "parallelizable": False,
                        "max_attempts": 3,
                    }
                ]
            }
        ),
        "schemas/input.schema.json": '{"type":"object"}',
        "schemas/output.schema.json": '{"type":"object"}',
        "policies/permissions.yaml": '{"permissions":["supplier.write"]}',
        "eval/cases.json": "[]",
    }
    if extra:
        files.update(extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "/absolute.py",
        "C:/escape.py",
        "run.py",
        "schemas/../../escape.json",
        "schemas\\escape.json",
    ],
)
def test_skill_rejects_executable_and_unsafe_archive_paths(path):
    with pytest.raises(DomainError):
        validate_package(archive({path: 'print("unsafe")'}), (policy().capability,))


def test_empty_workflow_is_not_implementation():
    with pytest.raises(DomainError):
        validate_package(archive({"workflow.yaml": '{"steps":[]}'}), (policy().capability,))


def test_zip_bomb_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("instructions.md", "A" * 1_000_000)
    with pytest.raises(DomainError):
        validate_package(buffer.getvalue(), ())


def test_valid_declarative_package_accepted():
    package = validate_package(archive(), (policy().capability,))
    assert package.manifest.name == "supplier-risk"
    assert package.workflow.steps[0].step_key == "admit"


def test_missing_dependency_rejected():
    with pytest.raises(DomainError):
        validate_package(archive(), ())
