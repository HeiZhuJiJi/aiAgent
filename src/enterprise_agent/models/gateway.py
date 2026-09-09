import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from enterprise_agent.contracts.domain import Classification, Contract, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.failure_injector import FailureInjector, FailurePoint


class ModelConfig(Contract):
    provider: Literal["Qwen", "DeepSeek", "OpenAICompatible"]
    base_url: str
    model: str
    external: bool
    timeout_sec: float = Field(gt=0, default=60)
    max_output_tokens: int = Field(gt=0, default=4096)
    version: str


class Message(Contract):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelReply(Contract):
    content: str
    usage: ModelUsage
    model: str


class ModelGateway:
    def __init__(
        self,
        config: ModelConfig,
        api_key: SecretStr,
        client: httpx.AsyncClient,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._client = client
        self._failure_injector = failure_injector or FailureInjector()

    def _egress(self, classification: Classification) -> None:
        if self.config.external and classification in {
            Classification.RESTRICTED,
            Classification.CONFIDENTIAL,
        }:
            raise DomainError(FailureCode.POLICY_DENIED, "model data egress denied")

    async def _post(
        self, path: str, payload: dict[str, object], classification: Classification
    ) -> httpx.Response:
        self._egress(classification)
        for attempt in range(3):
            response: httpx.Response | None = None
            try:
                if self._failure_injector.should_fail(FailurePoint.LLM_TIMEOUT):
                    raise httpx.ReadTimeout("injected model timeout")
                if self._failure_injector.should_fail(FailurePoint.LLM_429):
                    code = FailureCode.LLM_RATE_LIMIT
                else:
                    response = await self._client.post(
                        self.config.base_url.rstrip("/") + "/" + path,
                        json=payload,
                        headers=(
                            {"Authorization": "Bearer " + self._api_key.get_secret_value()}
                            if self._api_key.get_secret_value()
                            else {}
                        ),
                        timeout=self.config.timeout_sec,
                        follow_redirects=False,
                    )
            except httpx.TimeoutException:
                code = FailureCode.LLM_TIMEOUT
            except httpx.TransportError:
                code = FailureCode.LLM_SERVER_ERROR
            else:
                if response is None:
                    pass
                elif response.status_code == 429:
                    code = FailureCode.LLM_RATE_LIMIT
                elif response.status_code >= 500:
                    code = FailureCode.LLM_SERVER_ERROR
                elif response.is_error or response.is_redirect:
                    raise DomainError(FailureCode.LLM_SERVER_ERROR, "model request rejected")
                else:
                    return response
            if attempt < 2:
                await asyncio.sleep(min(2**attempt, 4))
        raise DomainError(code, "model transport attempts exhausted")

    async def chat(
        self, messages: tuple[Message, ...], classification: Classification
    ) -> ModelReply:
        response = await self._post(
            "chat/completions",
            {
                "model": self.config.model,
                "messages": [m.model_dump(include={"role", "content"}) for m in messages],
                "max_tokens": self.config.max_output_tokens,
            },
            classification,
        )
        try:
            data = response.json()
            if self._failure_injector.should_fail(FailurePoint.LLM_SCHEMA_CORRUPTION):
                data["choices"][0]["message"]["content"] = "{injected-corrupt-json"
            return ModelReply(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                usage=ModelUsage(
                    input_tokens=data["usage"]["prompt_tokens"],
                    output_tokens=data["usage"]["completion_tokens"],
                ),
            )
        except (KeyError, IndexError, ValueError, TypeError):
            raise DomainError(
                FailureCode.LLM_SCHEMA_ERROR, "invalid model response envelope"
            ) from None

    async def structured[T: BaseModel](
        self,
        messages: tuple[Message, ...],
        schema: type[T],
        classification: Classification,
        *,
        before_call: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[T, ModelUsage]:
        prompt = messages + (
            Message(
                role="user",
                content="Return only JSON matching this schema: "
                + json.dumps(schema.model_json_schema()),
            ),
        )
        total_in, total_out = 0, 0
        for repair in range(3):
            if before_call is not None:
                await before_call()
            reply = await self.chat(prompt, classification)
            total_in += reply.usage.input_tokens
            total_out += reply.usage.output_tokens
            try:
                result = schema.model_validate_json(reply.content)
                return result, ModelUsage(input_tokens=total_in, output_tokens=total_out)
            except ValidationError:
                if repair == 2:
                    raise DomainError(
                        FailureCode.LLM_SCHEMA_ERROR, "structured output invalid after two repairs"
                    ) from None
                prompt = prompt + (
                    Message(role="assistant", content=reply.content),
                    Message(
                        role="user",
                        content="The JSON failed schema validation. Correct it using the provided schema; return JSON only.",
                    ),
                )
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "unreachable repair state")

    async def embed(
        self, texts: tuple[str, ...], classification: Classification
    ) -> tuple[tuple[float, ...], ...]:
        response = await self._post(
            "embeddings", {"model": self.config.model, "input": list(texts)}, classification
        )
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            if [item["index"] for item in ordered] != list(range(len(texts))):
                raise ValueError("embedding count/index mismatch")
            return tuple(tuple(float(v) for v in item["embedding"]) for item in ordered)
        except (KeyError, ValueError, TypeError):
            raise DomainError(FailureCode.LLM_SCHEMA_ERROR, "invalid embedding response") from None

    async def stream(
        self, messages: tuple[Message, ...], classification: Classification
    ) -> AsyncIterator[str]:
        self._egress(classification)
        try:
            async with self._client.stream(
                "POST",
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers=(
                    {"Authorization": "Bearer " + self._api_key.get_secret_value()}
                    if self._api_key.get_secret_value()
                    else {}
                ),
                json={
                    "model": self.config.model,
                    "messages": [m.model_dump(include={"role", "content"}) for m in messages],
                    "stream": True,
                    "max_tokens": self.config.max_output_tokens,
                },
                timeout=self.config.timeout_sec,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise DomainError(FailureCode.LLM_SERVER_ERROR, "stream request rejected")
                ended = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value == "[DONE]":
                        ended = True
                        break
                    try:
                        chunk = json.loads(value)
                        content = chunk["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError, TypeError):
                        raise DomainError(
                            FailureCode.LLM_SCHEMA_ERROR, "invalid stream event"
                        ) from None
                    if content:
                        yield str(content)
                if not ended:
                    raise DomainError(FailureCode.LLM_TIMEOUT, "stream ended without completion")
        except httpx.TimeoutException:
            raise DomainError(FailureCode.LLM_TIMEOUT, "stream timed out") from None
        except httpx.TransportError:
            raise DomainError(FailureCode.LLM_SERVER_ERROR, "stream transport failed") from None
