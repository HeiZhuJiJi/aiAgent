"""Run the production bootstrap shell in its pinned image, with a test-only CLI."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("scenario", "exit_code", "describes", "creates"),
    [
        ("existing", 0, 1, 0),
        ("created", 0, 2, 1),
        ("already_exists", 0, 2, 1),
        ("response_loss", 0, 2, 1),
        ("recovering", 0, 3, 1),
        ("unavailable", 1, 4, 2),
        ("unconfirmed_create", 1, 4, 2),
    ],
)
def test_namespace_bootstrap_requires_confirmed_availability(
    scenario: str, exit_code: int, describes: int, creates: int
) -> None:
    compose = yaml.safe_load((ROOT / "deploy/compose/infrastructure.yml").read_text())
    image = compose["services"]["temporal-namespace"]["image"]
    script = (ROOT / "deploy/compose/temporal-namespace.sh").as_posix()
    fake = (ROOT / "tests/fakes/temporal_namespace.sh").as_posix()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={script},target=/bootstrap.sh,readonly",
            "--mount",
            f"type=bind,source={fake},target=/fake.sh,readonly",
            "--env",
            f"TEST_NAMESPACE_CASE={scenario}",
            "--env",
            "TEMPORAL_NAMESPACE_INIT_ATTEMPTS=2",
            "--env",
            "TEMPORAL_NAMESPACE_INIT_DELAY_SECONDS=0",
            "--entrypoint",
            "/bin/sh",
            image,
            "-ec",
            ". /fake.sh; . /bootstrap.sh",
        ],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == exit_code, output
    assert output.count("TEST_CALL describe") == describes, output
    assert output.count("TEST_CALL create") == creates, output
    assert ("Namespace default is available." in output) == (exit_code == 0)
    if exit_code != 0:
        assert "could not be verified after 2 attempts" in output
