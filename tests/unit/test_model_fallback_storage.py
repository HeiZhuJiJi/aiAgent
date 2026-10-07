import base64
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from enterprise_agent.contracts.domain import FailureCode, TaskGoal
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.models.gateway import ModelConfig
from enterprise_agent.storage import model_configs, tasks


def _cipher() -> SecretCipher:
    return SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())


def _loaded(
    config_id: UUID,
    *,
    base_url: str,
    model: str,
    kind: str = "CHAT",
    provider: str = "OpenAICompatible",
    version: int = 1,
):
    return (
        ModelConfig(
            provider=provider,
            base_url=base_url,
            model=model,
            external=True,
            version=f"workspace-config-{version}",
        ),
        SecretStr(f"secret-{config_id}"),
        SimpleNamespace(id=config_id, kind=kind, version=version),
    )


async def test_chat_model_chain_never_discovers_an_implicit_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, primary_id = uuid4(), uuid4()
    primary = _loaded(
        primary_id,
        base_url="https://primary.example/v1",
        model="primary",
    )
    loaded_ids: list[UUID] = []

    async def fake_load(_session, loaded_workspace_id, config_id, _cipher):
        assert loaded_workspace_id == workspace_id
        loaded_ids.append(config_id)
        return primary

    monkeypatch.setattr(model_configs, "load_model_gateway_config", fake_load)

    chain = await model_configs.load_chat_model_gateway_chain(
        object(),  # type: ignore[arg-type]
        workspace_id,
        primary_id,
        None,
        _cipher(),
    )

    assert chain == (primary,)
    assert loaded_ids == [primary_id]


async def test_chat_model_chain_preserves_the_explicit_primary_fallback_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, primary_id, fallback_id = uuid4(), uuid4(), uuid4()
    configured = {
        primary_id: _loaded(
            primary_id,
            base_url="https://primary.example/v1",
            model="primary",
            provider="DeepSeek",
            version=3,
        ),
        fallback_id: _loaded(
            fallback_id,
            base_url="https://fallback.example/v1",
            model="fallback",
            provider="Qwen",
            version=7,
        ),
    }
    loaded_ids: list[UUID] = []

    async def fake_load(_session, loaded_workspace_id, config_id, _cipher):
        assert loaded_workspace_id == workspace_id
        loaded_ids.append(config_id)
        return configured[config_id]

    monkeypatch.setattr(model_configs, "load_model_gateway_config", fake_load)

    chain = await model_configs.load_chat_model_gateway_chain(
        object(),  # type: ignore[arg-type]
        workspace_id,
        primary_id,
        fallback_id,
        _cipher(),
    )

    assert chain == (configured[primary_id], configured[fallback_id])
    assert loaded_ids == [primary_id, fallback_id]


async def test_chat_model_chain_rejects_duplicate_or_non_chat_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, primary_id, fallback_id = uuid4(), uuid4(), uuid4()
    primary = _loaded(
        primary_id,
        base_url="https://Model.Example/v1/",
        model="same-model",
    )
    duplicate = _loaded(
        fallback_id,
        base_url="https://model.example:443/v1",
        model="same-model",
    )
    configured = {primary_id: primary, fallback_id: duplicate}

    async def fake_load(_session, _workspace_id, config_id, _cipher):
        return configured[config_id]

    monkeypatch.setattr(model_configs, "load_model_gateway_config", fake_load)

    with pytest.raises(DomainError) as same_id:
        await model_configs.load_chat_model_gateway_chain(
            object(),  # type: ignore[arg-type]
            workspace_id,
            primary_id,
            primary_id,
            _cipher(),
        )
    assert same_id.value.code is FailureCode.VALIDATION_FAILED

    with pytest.raises(DomainError, match="different endpoint or model") as duplicate_model:
        await model_configs.load_chat_model_gateway_chain(
            object(),  # type: ignore[arg-type]
            workspace_id,
            primary_id,
            fallback_id,
            _cipher(),
        )
    assert duplicate_model.value.code is FailureCode.VALIDATION_FAILED

    configured[fallback_id] = _loaded(
        fallback_id,
        base_url="https://fallback.example/v1",
        model="embedding",
        kind="EMBEDDING",
    )
    with pytest.raises(DomainError, match="fallback configuration is not a chat model"):
        await model_configs.load_chat_model_gateway_chain(
            object(),  # type: ignore[arg-type]
            workspace_id,
            primary_id,
            fallback_id,
            _cipher(),
        )


async def test_task_model_refs_freeze_only_the_explicit_ordered_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, primary_id, fallback_id = uuid4(), uuid4(), uuid4()
    primary = _loaded(
        primary_id,
        base_url="https://primary.example/v1",
        model="primary",
        provider="DeepSeek",
        version=3,
    )
    fallback = _loaded(
        fallback_id,
        base_url="https://fallback.example/v1",
        model="fallback",
        provider="Qwen",
        version=7,
    )
    seen: list[tuple[UUID, UUID, UUID | None]] = []

    async def fake_chain(
        _session,
        loaded_workspace_id,
        loaded_primary_id,
        loaded_fallback_id,
        _cipher,
    ):
        seen.append((loaded_workspace_id, loaded_primary_id, loaded_fallback_id))
        return (primary, fallback)

    monkeypatch.setattr(tasks, "load_chat_model_gateway_chain", fake_chain)
    monkeypatch.setenv(
        "SECRET_MASTER_KEY",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )
    goal = TaskGoal(
        goal="answer durably",
        constraints=(),
        deliverables=("answer",),
        workspace_id=workspace_id,
        time_budget_sec=60,
        token_budget=1000,
        cost_budget=1,
        user_context=json.dumps(
            {
                "model_config_id": str(primary_id),
                "fallback_model_config_id": str(fallback_id),
            }
        ),
    )

    refs = await tasks._selected_model_version_refs(  # type: ignore[arg-type]
        object(),
        goal,
    )

    assert refs == (
        tasks.version_ref(
            f"model.config:{primary_id}",
            "3:DeepSeek:primary",
        ),
        tasks.version_ref(
            f"model.config:{fallback_id}",
            "7:Qwen:fallback",
        ),
    )
    assert seen == [(workspace_id, primary_id, fallback_id)]


async def test_task_rejects_fallback_without_an_explicit_primary() -> None:
    goal = TaskGoal(
        goal="answer durably",
        constraints=(),
        deliverables=("answer",),
        workspace_id=uuid4(),
        time_budget_sec=60,
        token_budget=1000,
        cost_budget=1,
        user_context=json.dumps({"fallback_model_config_id": str(uuid4())}),
    )

    with pytest.raises(DomainError, match="requires a selected primary model") as raised:
        await tasks._selected_model_version_refs(  # type: ignore[arg-type]
            object(),
            goal,
        )
    assert raised.value.code is FailureCode.VALIDATION_FAILED
