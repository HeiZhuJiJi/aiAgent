import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.skills.package import validate_package


@pytest.mark.parametrize(
    ("name", "version", "steps", "capabilities"),
    [
        (
            "web-research",
            "1.0.1",
            ["search-web", "synthesize-answer", "review-answer"],
            {"web.search", "model.structured_output", "runtime.review"},
        ),
        (
            "document-analysis",
            "1.0.0",
            [
                "retrieve-selected-document",
                "synthesize-document-answer",
                "review-document-answer",
            ],
            {"knowledge.hybrid_search", "model.structured_output", "runtime.review"},
        ),
        (
            "grounded-research",
            "1.0.0",
            [
                "retrieve-selected-document",
                "search-current-web",
                "synthesize-grounded-answer",
                "review-grounded-answer",
            ],
            {
                "knowledge.hybrid_search",
                "web.search",
                "model.structured_output",
                "runtime.review",
            },
        ),
    ],
)
def test_builtin_research_skills_are_deterministic_and_valid(
    name: str,
    version: str,
    steps: list[str],
    capabilities: set[str],
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = (
        root
        / "apps"
        / "web"
        / "public"
        / "builtin-skills"
        / f"{name}.skill.zip"
    )

    subprocess.run(
        [sys.executable, str(root / "scripts" / "generate_builtin_skills.py")],
        check=True,
        cwd=root,
    )
    first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    subprocess.run(
        [sys.executable, str(root / "scripts" / "generate_builtin_skills.py")],
        check=True,
        cwd=root,
    )
    blob = output.read_bytes()

    assert hashlib.sha256(blob).hexdigest() == first_hash
    package = validate_package(blob, builtin_descriptors())
    assert package.manifest.name == name
    assert package.manifest.version == version
    assert package.workflow.steps[0].max_attempts == 1
    assert [step.step_key for step in package.workflow.steps] == steps
    assert {
        dependency.capability_id for dependency in package.manifest.dependencies
    } == capabilities
