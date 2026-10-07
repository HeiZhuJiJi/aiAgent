import subprocess
import sys


def test_worker_import_order_does_not_initialize_api_application() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import enterprise_agent.capabilities.builtins; "
                "import enterprise_agent.storage.model_configs"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
