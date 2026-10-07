import base64
import json
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


class BrokenAfterTokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadError("connection lost after token")


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
    assert transport.max_tokens == [512, 512, 512]
    assert all("chat_template_kwargs" not in item for item in transport.payloads)
    assert all("response_format" not in item for item in transport.payloads)
    assert usage.input_tokens == 36 and usage.output_tokens == 12


async def test_structured_accepts_one_strict_object_inside_common_model_wrappers():
    transport = ScriptedModel(
        ['<think>private reasoning</think>\nHere is the result:\n```json\n{"count":3}\n```']
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result, usage = await ModelGateway(config(), SecretStr("not-logged"), client).structured(
            (Message(role="user", content="count"),),
            Answer,
            Classification.PUBLIC,
        )

    assert result.count == 3
    assert transport.calls == 1
    assert usage.input_tokens == 12 and usage.output_tokens == 4


async def test_structured_stops_after_two_repairs():
    transport = ScriptedModel(["invalid"] * 4)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DomainError) as raised:
            await ModelGateway(config(), SecretStr("not-logged"), client).structured(
                (Message(role="user", content="count"),), Answer, Classification.PUBLIC
            )
        assert raised.value.code == FailureCode.LLM_SCHEMA_ERROR
    assert transport.calls == 3


async def test_qwen_structured_call_disables_thinking_without_affecting_chat():
    transport = ScriptedModel(['{"count":3}', "plain chat"])
    qwen = config().model_copy(update={"model": "Qwen/Qwen3.8-27B"})
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = ModelGateway(qwen, SecretStr("not-logged"), client)
        result, _ = await gateway.structured(
            (Message(role="user", content="count"),),
            Answer,
            Classification.PUBLIC,
            max_output_tokens=256,
            temperature=0.0,
        )
        reply = await gateway.chat((Message(role="user", content="hello"),), Classification.PUBLIC)
    assert result.count == 3 and reply.content == "plain chat"
    assert transport.payloads[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert transport.payloads[0]["response_format"] == {"type": "json_object"}
    assert transport.payloads[0]["max_tokens"] == 256
    assert transport.payloads[0]["temperature"] == 0.0
    assert "chat_template_kwargs" not in transport.payloads[1]
    assert "response_format" not in transport.payloads[1]
    assert "temperature" not in transport.payloads[1]


async def test_deepseek_structured_call_uses_json_mode_and_disables_thinking():
    transport = ScriptedModel(['{"count":3}'])
    deepseek = config().model_copy(update={"provider": "DeepSeek", "model": "deepseek-v4-flash"})
    async with httpx.AsyncClient(transport=transport) as client:
        result, _ = await ModelGateway(deepseek, SecretStr("not-logged"), client).structured(
            (Message(role="user", content="count"),),
            Answer,
            Classification.PUBLIC,
            temperature=0.0,
        )

    assert result.count == 3
    assert transport.payloads[0]["thinking"] == {"type": "disabled"}
    assert transport.payloads[0]["response_format"] == {"type": "json_object"}


async def test_structured_repairs_a_semantically_invalid_result_in_place():
    transport = ScriptedModel(['{"count":1}', '{"count":3}'])
    async with httpx.AsyncClient(transport=transport) as client:
        result, usage = await ModelGateway(config(), SecretStr("not-logged"), client).structured(
            (Message(role="user", content="count"),),
            Answer,
            Classification.PUBLIC,
            validate_result=lambda value: (
                None if value.count == 3 else "count must equal supplied Evidence"
            ),
        )

    assert result.count == 3
    assert transport.calls == 2
    assert usage.input_tokens == 24 and usage.output_tokens == 8
    assert "count must equal supplied Evidence" in transport.payloads[1]["messages"][-1]["content"]


async def test_stream_retries_a_transient_failure_before_first_token() -> None:
    calls = 0
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payloads.append(json.loads(request.content))
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        deepseek = config().model_copy(
            update={"provider": "DeepSeek", "model": "deepseek-v4-flash"}
        )
        chunks = [
            chunk
            async for chunk in ModelGateway(deepseek, SecretStr("not-logged"), client).stream(
                (Message(role="user", content="hello"),),
                Classification.PUBLIC,
            )
        ]

    assert chunks == ["hello"]
    assert calls == 2
    assert all(item["thinking"] == {"type": "disabled"} for item in payloads)
    assert all(item["temperature"] == 0.4 for item in payloads)


async def test_stream_never_retries_after_visible_output() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=BrokenAfterTokenStream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = ModelGateway(config(), SecretStr("not-logged"), client).stream(
            (Message(role="user", content="hello"),),
            Classification.PUBLIC,
        )
        assert await anext(stream) == "partial"
        with pytest.raises(DomainError) as raised:
            await anext(stream)

    assert raised.value.code == FailureCode.LLM_SERVER_ERROR
    assert calls == 1
