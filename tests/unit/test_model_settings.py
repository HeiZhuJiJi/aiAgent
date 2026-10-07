import base64
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from enterprise_agent.contracts.domain import VersionRef
from enterprise_agent.models.gateway import ModelConfig
from enterprise_agent.models.settings import load_embedding_settings, load_model_settings
from enterprise_agent.runtime import activities


def test_reads_file_and_masks_key(tmp_path: Path):
    path = tmp_path / "test.env"
    path.write_text(
        "MODEL_BASE_URL=https://model.invalid/v1/\nMODEL_NAME=example\nMODEL_API_KEY=test-secret\n"
    )
    config, key = load_model_settings(path, environ={})
    assert config.base_url == "https://model.invalid/v1"
    assert config.external is True
    assert key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(key)


def test_environment_overrides_file(tmp_path: Path):
    path = tmp_path / "test.env"
    path.write_text(
        "MODEL_BASE_URL=https://model.invalid/v1\nMODEL_NAME=old\nMODEL_API_KEY=test-secret\n"
    )
    config, _ = load_model_settings(path, environ={"MODEL_NAME": "new"})
    assert config.model == "new"


@pytest.mark.parametrize(
    "url",
    [
        "http://model.invalid/v1",
        "https://user:secret@model.invalid/v1",
        "https://model.invalid/v1?key=secret",
        "https://model.invalid/v1#fragment",
    ],
)
def test_unsafe_endpoint_rejected_without_echoing_credentials(tmp_path: Path, url: str):
    path = tmp_path / "test.env"
    path.write_text(f"MODEL_BASE_URL={url}\nMODEL_NAME=example\nMODEL_API_KEY=test-secret\n")
    with pytest.raises(ValueError) as error:
        load_model_settings(path, environ={})
    assert "test-secret" not in str(error.value)
    assert url not in str(error.value)


def test_missing_settings_name_only(tmp_path: Path):
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        load_model_settings(tmp_path / "absent.env", environ={})


def test_local_loopback_allows_http_and_optional_key(tmp_path: Path):
    path = tmp_path / "local.env"
    path.write_text(
        "MODEL_MODE=local\nMODEL_BASE_URL=http://127.0.0.1:8000/v1\nMODEL_NAME=example\nMODEL_API_KEY=\n"
    )
    config, key = load_model_settings(path, environ={})
    assert config.external is False
    assert config.base_url == "http://127.0.0.1:8000/v1"
    assert key.get_secret_value() == ""


def test_local_mode_accepts_docker_host_address(tmp_path: Path):
    path = tmp_path / "local.env"
    path.write_text(
        "MODEL_MODE=local\nMODEL_BASE_URL=http://host.docker.internal:8000/v1\nMODEL_NAME=example\n"
    )
    config, key = load_model_settings(path, environ={})
    assert config.external is False
    assert config.base_url == "http://host.docker.internal:8000/v1"
    assert key.get_secret_value() == ""


def test_local_embedding_accepts_compose_service_address(tmp_path: Path):
    path = tmp_path / "local.env"
    path.write_text(
        "EMBEDDING_MODE=local\n"
        "EMBEDDING_BASE_URL=http://embedding:80/v1\n"
        "EMBEDDING_NAME=BAAI/bge-m3\n"
    )
    config, key = load_embedding_settings(path, environ={})
    assert config.external is False
    assert config.base_url == "http://embedding:80/v1"
    assert key.get_secret_value() == ""


def test_local_mode_cannot_mark_public_endpoint_as_internal(tmp_path: Path):
    path = tmp_path / "local.env"
    path.write_text(
        "MODEL_MODE=local\nMODEL_BASE_URL=https://public.invalid/v1\nMODEL_NAME=example\n"
    )
    with pytest.raises(ValueError, match="loopback"):
        load_model_settings(path, environ={})


def test_embedding_settings_are_independent_from_chat_model(tmp_path: Path):
    path = tmp_path / "models.env"
    path.write_text(
        "MODEL_MODE=local\n"
        "MODEL_BASE_URL=http://127.0.0.1:8000/v1\n"
        "MODEL_NAME=chat-only\n"
        "EMBEDDING_MODE=local\n"
        "EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1\n"
        "EMBEDDING_NAME=BAAI/bge-m3\n"
    )
    config, key = load_embedding_settings(path, environ={})
    assert config.base_url == "http://127.0.0.1:8001/v1"
    assert config.model == "BAAI/bge-m3"
    assert config.external is False
    assert key.get_secret_value() == ""


def test_embedding_settings_do_not_fall_back_to_chat_model(tmp_path: Path):
    path = tmp_path / "models.env"
    path.write_text(
        "MODEL_MODE=local\nMODEL_BASE_URL=http://127.0.0.1:8000/v1\nMODEL_NAME=chat-only\n"
    )
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        load_embedding_settings(path, environ={})


async def test_replan_resolves_selected_chat_model_through_frozen_snapshot_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, config_id, snapshot_id = uuid4(), uuid4(), uuid4()
    selected = ModelConfig(
        provider="DeepSeek",
        base_url="https://model.invalid/v1",
        model="selected-chat-model",
        external=True,
        version="workspace-config-3",
    )
    key = SecretStr("selected-secret")
    reference = VersionRef(
        identifier=f"model.config:{config_id}",
        version="3:DeepSeek:selected-chat-model",
        sha256="a" * 64,
    )
    seen: list[tuple[object, object, object]] = []

    async def fake_snapshot(_session, loaded_workspace_id, loaded_snapshot_id):
        assert loaded_workspace_id == workspace_id
        assert loaded_snapshot_id == snapshot_id
        return SimpleNamespace(model_config_versions=(reference,))

    async def fake_frozen_chain(
        _session,
        loaded_workspace_id,
        loaded_references,
        cipher,
    ):
        seen.append((loaded_workspace_id, loaded_references, cipher))
        return ((selected, key),)

    monkeypatch.setattr(activities, "load_snapshot", fake_snapshot)
    monkeypatch.setattr(
        activities,
        "load_frozen_task_model_gateway_chain",
        fake_frozen_chain,
    )
    monkeypatch.setenv(
        "SECRET_MASTER_KEY",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )

    config, secret = await activities._model_settings_for_task(  # type: ignore[arg-type]
        object(),
        workspace_id,
        snapshot_id,
    )

    assert config == selected
    assert secret.get_secret_value() == "selected-secret"
    assert len(seen) == 1
    assert seen[0][0:2] == (workspace_id, (reference,))
    assert seen[0][2] is not None
