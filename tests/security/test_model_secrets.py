import base64
import os
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from enterprise_agent.contracts.domain import Classification, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.models.gateway import Message, ModelConfig, ModelGateway
from tests.fakes.model import ScriptedModel


class Answer(BaseModel):
    count: int


def config():
    return ModelConfig(
        provider="OpenAICompatible",
        base_url="https://example.invalid/v1",
        model="test",
        external=True,
        version="1",
    )


def test_secret_bound_to_workspace_and_identity():
    cipher = SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    w, s = uuid4(), uuid4()
    sealed = cipher.encrypt(w, s, "sensitive-value")
    assert b"sensitive-value" not in sealed
    assert cipher.decrypt(w, s, sealed) == "sensitive-value"
    for wrong_w, wrong_s in [(uuid4(), s), (w, uuid4())]:
        with pytest.raises(DomainError, match="secret authentication failed"):
            cipher.decrypt(wrong_w, wrong_s, sealed)
    with pytest.raises(DomainError):
        cipher.decrypt(w, s, sealed[:-1] + bytes([sealed[-1] ^ 1]))


async def test_model_egress_denied_before_http():
    transport = ScriptedModel([])
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = ModelGateway(config(), SecretStr("not-logged"), client)
        with pytest.raises(DomainError) as raised:
            await gateway.chat(
                (Message(role="user", content="restricted data"),), Classification.RESTRICTED
            )
        assert raised.value.code == FailureCode.POLICY_DENIED
    assert transport.calls == 0


async def test_structured_repairs_and_records_all_usage():
    transport = ScriptedModel(["not-json", '{"count":"wrong"}', '{"count":3}'])
    async with httpx.AsyncClient(transport=transport) as client:
        result, usage = await ModelGateway(config(), SecretStr("not-logged"), client).structured(
            (Message(role="user", content="count"),), Answer, Classification.PUBLIC
        )
    assert result.count == 3
    assert transport.calls == 3
    assert usage.input_tokens == 36 and usage.output_tokens == 12


async def test_structured_stops_after_two_repairs():
    transport = ScriptedModel(["invalid"] * 4)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DomainError) as raised:
            await ModelGateway(config(), SecretStr("not-logged"), client).structured(
                (Message(role="user", content="count"),), Answer, Classification.PUBLIC
            )
        assert raised.value.code == FailureCode.LLM_SCHEMA_ERROR
    assert transport.calls == 3
