import asyncio
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from enterprise_agent.contracts.domain import Classification, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import (
    FallbackModelGateway,
    Message,
    ModelConfig,
    ModelGateway,
    ModelInvocationError,
    ModelReply,
    ModelUsage,
)


class _StructuredReply(BaseModel):
    answer: str


def _config(model: str) -> ModelConfig:
    return ModelConfig(
        provider="OpenAICompatible",
        base_url="https://" + model + ".example/v1",
        model=model,
        external=True,
        version="1",
    )


class _ScriptedGateway:
    def __init__(
        self,
        model: str,
        *,
        chat_error: BaseException | None = None,
        structured_error: DomainError | None = None,
        stream_events: tuple[str | DomainError, ...] = (),
        embed_error: DomainError | None = None,
    ) -> None:
        self.config = _config(model)
        self.chat_error = chat_error
        self.structured_error = structured_error
        self.stream_events = stream_events
        self.embed_error = embed_error
        self.chat_calls = 0
        self.structured_calls = 0
        self.stream_calls = 0
        self.embed_calls = 0
        self.chat_options: dict[str, object] = {}

    async def chat(
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        **options: object,
    ) -> ModelReply:
        self.chat_calls += 1
        self.chat_options = options
        if self.chat_error is not None:
            raise self.chat_error
        return ModelReply(
            content=self.config.model,
            model=self.config.model,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )

    async def structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        classification: Classification,
        **options: object,
    ) -> tuple[BaseModel, ModelUsage]:
        self.structured_calls += 1
        before_call = options.get("before_call")
        if callable(before_call):
            await before_call()
        if self.structured_error is not None:
            after_call = options.get("after_call")
            if callable(after_call) and isinstance(
                self.structured_error, ModelInvocationError
            ):
                await after_call(self.structured_error.usage)
            raise self.structured_error
        usage = ModelUsage(
            input_tokens=1,
            output_tokens=1,
        )
        after_call = options.get("after_call")
        if callable(after_call):
            await after_call(usage)
        return schema.model_validate({"answer": self.config.model}), usage

    async def embed(
        self,
        texts: tuple[str, ...],
        classification: Classification,
    ) -> tuple[tuple[float, ...], ...]:
        self.embed_calls += 1
        if self.embed_error is not None:
            raise self.embed_error
        return tuple((float(index),) for index, _text in enumerate(texts))

    async def stream(
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        for event in self.stream_events:
            if isinstance(event, DomainError):
                raise event
            yield event


def _gateway(scripted: _ScriptedGateway) -> ModelGateway:
    return cast(ModelGateway, scripted)


def _error(code: FailureCode) -> DomainError:
    return DomainError(code, "scripted failure")


async def _collect(stream: AsyncIterator[str]) -> tuple[str, ...]:
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(chunk)
    return tuple(chunks)


async def test_chat_falls_back_once_after_primary_server_error() -> None:
    primary = _ScriptedGateway("primary", chat_error=_error(FailureCode.LLM_SERVER_ERROR))
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    reply = await gateway.chat(
        (Message(role="user", content="hello"),),
        Classification.INTERNAL,
        max_output_tokens=123,
        disable_thinking=True,
        json_mode=True,
        temperature=0,
    )

    assert reply.model == "fallback"
    assert primary.chat_calls == fallback.chat_calls == 1
    assert fallback.chat_options == {
        "max_output_tokens": 123,
        "disable_thinking": True,
        "json_mode": True,
        "temperature": 0,
    }
    assert gateway.config.model == "primary"


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.LLM_TIMEOUT,
        FailureCode.LLM_RATE_LIMIT,
        FailureCode.LLM_SCHEMA_ERROR,
        FailureCode.CAPABILITY_UNAVAILABLE,
        FailureCode.POLICY_DENIED,
        FailureCode.BUDGET_EXCEEDED,
    ],
)
async def test_chat_does_not_fall_back_for_ineligible_failures(code: FailureCode) -> None:
    error = _error(code)
    primary = _ScriptedGateway("primary", chat_error=error)
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    with pytest.raises(DomainError) as raised:
        await gateway.chat(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        )

    assert raised.value is error
    assert primary.chat_calls == 1
    assert fallback.chat_calls == 0


@pytest.mark.parametrize(
    "code",
    [FailureCode.LLM_SERVER_ERROR, FailureCode.LLM_SCHEMA_ERROR],
)
async def test_structured_falls_back_after_primary_recovery_is_exhausted(
    code: FailureCode,
) -> None:
    primary = _ScriptedGateway("primary", structured_error=_error(code))
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    reply, usage = await gateway.structured(
        (Message(role="user", content="answer"),),
        _StructuredReply,
        Classification.INTERNAL,
    )

    assert reply.answer == "fallback"
    assert usage == ModelUsage(input_tokens=1, output_tokens=1)
    assert primary.structured_calls == fallback.structured_calls == 1


async def test_structured_schema_repairs_preserve_primary_usage_when_fallback_succeeds() -> None:
    calls = 0

    def invalid_structured_reply(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "primary",
                "choices": [{"message": {"content": "not valid json"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    fallback = _ScriptedGateway("fallback")
    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_structured_reply)) as client:
        primary = ModelGateway(_config("primary"), SecretStr("secret"), client)
        gateway = FallbackModelGateway(primary, _gateway(fallback))

        reply, usage = await gateway.structured(
            (Message(role="user", content="answer"),),
            _StructuredReply,
            Classification.INTERNAL,
        )

    assert reply.answer == "fallback"
    assert calls == 3
    assert usage == ModelUsage(input_tokens=22, output_tokens=10)
    assert gateway.consumed_usage == usage


async def test_structured_respects_explicit_attempt_limit() -> None:
    calls = 0

    def invalid_structured_reply(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "primary",
                "choices": [{"message": {"content": "not valid json"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_structured_reply)) as client:
        gateway = ModelGateway(_config("primary"), SecretStr("secret"), client)
        with pytest.raises(ModelInvocationError) as raised:
            await gateway.structured(
                (Message(role="user", content="answer"),),
                _StructuredReply,
                Classification.INTERNAL,
                max_attempts=1,
            )

    assert calls == 1
    assert raised.value.code is FailureCode.LLM_SCHEMA_ERROR


async def test_structured_accounts_each_reply_before_admitting_a_repair() -> None:
    calls = 0

    def invalid_structured_reply(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "primary",
                "choices": [{"message": {"content": "not valid json"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    usage_events: list[ModelUsage] = []

    async def observe_usage(usage: ModelUsage) -> None:
        usage_events.append(usage)

    async def admit_call() -> None:
        if sum(item.input_tokens + item.output_tokens for item in usage_events) >= 10:
            raise DomainError(FailureCode.BUDGET_EXCEEDED, "scripted token limit")

    fallback = _ScriptedGateway("fallback")
    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_structured_reply)) as client:
        gateway = FallbackModelGateway(
            ModelGateway(_config("primary"), SecretStr("secret"), client),
            _gateway(fallback),
            on_usage=observe_usage,
        )

        with pytest.raises(DomainError) as raised:
            await gateway.structured(
                (Message(role="user", content="answer"),),
                _StructuredReply,
                Classification.INTERNAL,
                before_call=admit_call,
            )

    assert raised.value.code is FailureCode.BUDGET_EXCEEDED
    assert calls == 1
    assert fallback.structured_calls == 0
    assert usage_events == [ModelUsage(input_tokens=7, output_tokens=3)]
    assert gateway.consumed_usage == ModelUsage(input_tokens=7, output_tokens=3)


async def test_malformed_primary_envelope_preserves_usage_when_fallback_succeeds() -> None:
    def malformed_envelope(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "primary",
                "choices": [],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5},
            },
        )

    usage_events: list[ModelUsage] = []

    async def observe_usage(usage: ModelUsage) -> None:
        usage_events.append(usage)

    fallback = _ScriptedGateway("fallback")
    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed_envelope)) as client:
        gateway = FallbackModelGateway(
            ModelGateway(_config("primary"), SecretStr("secret"), client),
            _gateway(fallback),
            on_usage=observe_usage,
        )

        reply, usage = await gateway.structured(
            (Message(role="user", content="answer"),),
            _StructuredReply,
            Classification.INTERNAL,
        )

    assert reply.answer == "fallback"
    assert usage == ModelUsage(input_tokens=14, output_tokens=6)
    assert usage_events == [
        ModelUsage(input_tokens=13, output_tokens=5),
        ModelUsage(input_tokens=1, output_tokens=1),
    ]
    assert gateway.consumed_usage == usage


async def test_structured_does_not_fall_back_for_timeout() -> None:
    error = _error(FailureCode.LLM_TIMEOUT)
    primary = _ScriptedGateway("primary", structured_error=error)
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    with pytest.raises(DomainError) as raised:
        await gateway.structured(
            (Message(role="user", content="answer"),),
            _StructuredReply,
            Classification.INTERNAL,
        )

    assert raised.value is error
    assert fallback.structured_calls == 0


async def test_stream_falls_back_only_before_first_chunk() -> None:
    primary = _ScriptedGateway(
        "primary",
        stream_events=(_error(FailureCode.LLM_SERVER_ERROR),),
    )
    fallback = _ScriptedGateway("fallback", stream_events=("safe ", "answer"))
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    chunks = await _collect(
        gateway.stream(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        )
    )

    assert chunks == ("safe ", "answer")
    assert primary.stream_calls == fallback.stream_calls == 1


async def test_stream_never_splices_fallback_after_primary_output() -> None:
    error = _error(FailureCode.LLM_SERVER_ERROR)
    primary = _ScriptedGateway("primary", stream_events=("partial", error))
    fallback = _ScriptedGateway("fallback", stream_events=("replacement",))
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))
    chunks: list[str] = []

    with pytest.raises(DomainError) as raised:
        async for chunk in gateway.stream(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        ):
            chunks.append(chunk)

    assert raised.value is error
    assert chunks == ["partial"]
    assert fallback.stream_calls == 0


async def test_stream_does_not_fall_back_for_rate_limit_before_output() -> None:
    error = _error(FailureCode.LLM_RATE_LIMIT)
    primary = _ScriptedGateway("primary", stream_events=(error,))
    fallback = _ScriptedGateway("fallback", stream_events=("replacement",))
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    with pytest.raises(DomainError) as raised:
        await _collect(
            gateway.stream(
                (Message(role="user", content="hello"),),
                Classification.INTERNAL,
            )
        )

    assert raised.value is error
    assert fallback.stream_calls == 0


async def test_fallback_failure_propagates_without_retrying_primary() -> None:
    primary = _ScriptedGateway("primary", chat_error=_error(FailureCode.LLM_SERVER_ERROR))
    fallback_error = _error(FailureCode.LLM_SERVER_ERROR)
    fallback = _ScriptedGateway("fallback", chat_error=fallback_error)
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    with pytest.raises(DomainError) as raised:
        await gateway.chat(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        )

    assert raised.value is fallback_error
    assert primary.chat_calls == fallback.chat_calls == 1


async def test_fallback_observer_records_a_failing_backup_call() -> None:
    primary = _ScriptedGateway("primary", chat_error=_error(FailureCode.LLM_SERVER_ERROR))
    fallback_error = _error(FailureCode.LLM_SERVER_ERROR)
    fallback = _ScriptedGateway("fallback", chat_error=fallback_error)
    observed: list[tuple[str, str, str, str, str | None]] = []

    async def observe(
        trigger: str,
        primary_config: ModelConfig,
        fallback_config: ModelConfig,
        outcome: str,
        failure_code: str | None,
    ) -> None:
        observed.append(
            (trigger, primary_config.model, fallback_config.model, outcome, failure_code)
        )

    gateway = FallbackModelGateway(
        _gateway(primary),
        _gateway(fallback),
        on_fallback=observe,
    )

    with pytest.raises(DomainError) as raised:
        await gateway.chat(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        )

    assert raised.value is fallback_error
    assert observed == [
        (
            FailureCode.LLM_SERVER_ERROR.value,
            "primary",
            "fallback",
            "STARTED",
            None,
        ),
        (
            FailureCode.LLM_SERVER_ERROR.value,
            "primary",
            "fallback",
            "FAILED",
            FailureCode.LLM_SERVER_ERROR.value,
        )
    ]
    assert gateway.used_fallback


async def test_fallback_observer_records_a_cancelled_backup_call() -> None:
    primary = _ScriptedGateway("primary", chat_error=_error(FailureCode.LLM_SERVER_ERROR))
    fallback = _ScriptedGateway("fallback", chat_error=asyncio.CancelledError())
    observed: list[tuple[str, str | None]] = []

    async def observe(
        _trigger: str,
        _primary_config: ModelConfig,
        _fallback_config: ModelConfig,
        outcome: str,
        failure_code: str | None,
    ) -> None:
        observed.append((outcome, failure_code))

    gateway = FallbackModelGateway(
        _gateway(primary),
        _gateway(fallback),
        on_fallback=observe,
    )

    with pytest.raises(asyncio.CancelledError):
        await gateway.chat(
            (Message(role="user", content="hello"),),
            Classification.INTERNAL,
        )

    assert observed == [("STARTED", None), ("CANCELLED", None)]


async def test_failed_structured_fallback_reports_all_primary_and_backup_usage() -> None:
    calls = {"primary": 0, "fallback": 0}

    def invalid_structured_reply(request: httpx.Request) -> httpx.Response:
        model = request.url.host.split(".", 1)[0]
        calls[model] += 1
        prompt_tokens, completion_tokens = (7, 3) if model == "primary" else (5, 2)
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [{"message": {"content": "not valid json"}}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            },
        )

    usage_events: list[ModelUsage] = []
    fallback_events: list[tuple[str, str | None]] = []

    async def observe_usage(usage: ModelUsage) -> None:
        usage_events.append(usage)

    async def observe_fallback(
        _trigger: str,
        _primary_config: ModelConfig,
        _fallback_config: ModelConfig,
        outcome: str,
        failure_code: str | None,
    ) -> None:
        fallback_events.append((outcome, failure_code))

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_structured_reply)) as client:
        gateway = FallbackModelGateway(
            ModelGateway(_config("primary"), SecretStr("secret"), client),
            ModelGateway(_config("fallback"), SecretStr("secret"), client),
            on_fallback=observe_fallback,
            on_usage=observe_usage,
        )

        with pytest.raises(ModelInvocationError) as raised:
            await gateway.structured(
                (Message(role="user", content="answer"),),
                _StructuredReply,
                Classification.INTERNAL,
            )

    assert calls == {"primary": 3, "fallback": 3}
    assert usage_events == [
        ModelUsage(input_tokens=7, output_tokens=3),
        ModelUsage(input_tokens=7, output_tokens=3),
        ModelUsage(input_tokens=7, output_tokens=3),
        ModelUsage(input_tokens=5, output_tokens=2),
        ModelUsage(input_tokens=5, output_tokens=2),
        ModelUsage(input_tokens=5, output_tokens=2),
    ]
    assert gateway.consumed_usage == ModelUsage(input_tokens=36, output_tokens=15)
    assert raised.value.usage == gateway.consumed_usage
    assert fallback_events == [
        ("STARTED", None),
        ("FAILED", FailureCode.LLM_SCHEMA_ERROR.value),
    ]


async def test_embeddings_never_cross_model_boundaries() -> None:
    error = _error(FailureCode.LLM_SERVER_ERROR)
    primary = _ScriptedGateway("primary", embed_error=error)
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    with pytest.raises(DomainError) as raised:
        await gateway.embed(("text",), Classification.INTERNAL)

    assert raised.value is error
    assert primary.embed_calls == 1
    assert fallback.embed_calls == 0


async def test_quality_repair_prefers_explicit_fallback_without_replaying_primary() -> None:
    primary = _ScriptedGateway("primary")
    fallback = _ScriptedGateway("fallback")
    gateway = FallbackModelGateway(_gateway(primary), _gateway(fallback))

    reply, _ = await gateway.structured_quality_repair(
        (Message(role="user", content="repair this answer using the same evidence"),),
        _StructuredReply,
        Classification.INTERNAL,
    )

    assert reply.answer == "fallback"
    assert primary.structured_calls == 0
    assert fallback.structured_calls == 1
    assert gateway.used_fallback
    assert gateway.fallback_trigger == "QUALITY_REPAIR"
    assert gateway.selected_config.model == "fallback"
