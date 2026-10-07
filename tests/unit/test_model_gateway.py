import json

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from enterprise_agent.contracts.domain import Classification, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import Message, ModelConfig, ModelGateway


class _StructuredReply(BaseModel):
    answer: str


def _config() -> ModelConfig:
    return ModelConfig(
        provider="OpenAICompatible",
        base_url="https://model.example/v1",
        model="test-model",
        external=True,
        version="1",
    )


@pytest.mark.parametrize("status_code", [400, 401, 403, 302])
async def test_provider_rejection_is_not_a_server_availability_failure(
    status_code: int,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, headers={"location": "https://model.example/v2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DomainError) as raised:
            await ModelGateway(_config(), SecretStr("secret"), client).chat(
                (Message(role="user", content="hello"),),
                Classification.INTERNAL,
            )

    assert raised.value.code is FailureCode.CAPABILITY_UNAVAILABLE
    assert calls == 1


async def test_stream_provider_rejection_is_not_a_server_availability_failure() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(_config(), SecretStr("secret"), client)
        with pytest.raises(DomainError) as raised:
            async for _ in gateway.stream(
                (Message(role="user", content="hello"),),
                Classification.INTERNAL,
            ):
                pass

    assert raised.value.code is FailureCode.CAPABILITY_UNAVAILABLE
    assert calls == 1


async def test_invalid_json_repair_does_not_replay_or_accumulate_malformed_output() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        content = "{truncated" if len(requests) < 3 else '{"answer":"ok"}'
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    config = ModelConfig(
        provider="OpenAICompatible",
        base_url="https://model.example/v1",
        model="test-model",
        external=True,
        version="1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, usage = await ModelGateway(config, SecretStr("secret"), client).structured(
            (Message(role="user", content="answer"),),
            _StructuredReply,
            Classification.INTERNAL,
            max_output_tokens=256,
        )

    assert result.answer == "ok"
    assert usage.input_tokens == 30
    assert len(requests) == 3
    repair_messages = requests[1]["messages"]
    final_messages = requests[2]["messages"]
    assert isinstance(repair_messages, list)
    assert isinstance(final_messages, list)
    assert len(repair_messages) == len(final_messages) == 3
    assert all(message.get("role") != "assistant" for message in repair_messages)
    assert all(message.get("role") != "assistant" for message in final_messages)


async def test_schema_retry_receives_specific_field_constraint_without_response_values():
    from typing import Literal

    from pydantic import BaseModel

    from tests.fakes.model import ScriptedModel

    class Selection(BaseModel):
        mode: Literal['ACCEPT', 'RETRY']

    transport = ScriptedModel(['{"mode":"PRIVATE_INVALID_VALUE"}', '{"mode":"ACCEPT"}'])
    async with httpx.AsyncClient(transport=transport) as client:
        value, _ = await ModelGateway(_config(), SecretStr('test-key'), client).structured(
            (Message(role='user', content='Select a mode.'),), Selection, Classification.INTERNAL,
            max_attempts=2)
    assert value.mode == 'ACCEPT'
    feedback = transport.payloads[1]['messages'][-1]['content']
    assert 'mode' in feedback and 'literal_error' in feedback
    assert 'ACCEPT' in feedback and 'RETRY' in feedback
    assert 'PRIVATE_INVALID_VALUE' not in feedback


def test_schema_diagnostic_does_not_echo_unknown_keys_or_input():
    from enterprise_agent.models.gateway import _structured_schema_error
    detail = _structured_schema_error('{"sensitive-key":"sensitive-value"}', _StructuredReply)
    assert 'answer' in detail and 'missing' in detail
    assert 'sensitive-key' not in detail and 'sensitive-value' not in detail
