import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.contracts.domain import Classification, Contract, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.observability.metrics import LLM_CALL_TOTAL, LLM_LATENCY, LLM_TOKENS
from enterprise_agent.runtime.admission import (
    RedisAdmissionController,
    admitted_call,
    load_admission_controller,
)
from enterprise_agent.runtime.failure_injector import FailureInjector, FailurePoint

logger = logging.getLogger(__name__)


def _parse_structured_reply[T: BaseModel](content: str, schema: type[T]) -> T | None:
    """Validate one unambiguous JSON object without spending another model call."""
    candidates = [content.strip()]
    without_thinking = re.sub(
        r"<think\b[^>]*>.*?</think>",
        "",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    if without_thinking not in candidates:
        candidates.append(without_thinking)
    fenced = re.sub(r"^```(?:json)?\s*", "", without_thinking, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced).strip()
    if fenced not in candidates:
        candidates.append(fenced)

    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except ValidationError:
            continue

    decoder = json.JSONDecoder()
    valid: list[T] = []
    for offset, character in enumerate(without_thinking):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(without_thinking[offset:])
            parsed = schema.model_validate(value)
        except (ValueError, ValidationError):
            continue
        valid.append(parsed)
    return valid[0] if len(valid) == 1 else None


def _structured_schema_error(content: str, schema: type[BaseModel]) -> str:
    """Explain schema defects without exposing response values or arbitrary object keys."""
    cleaned = re.sub(r"<think\b[^>]*>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        json.loads(cleaned)
    except (ValueError, TypeError):
        return "Response is not one complete JSON value. Return complete JSON without surrounding prose."
    known_fields: set[str] = set()
    def fields(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get('properties')
            if isinstance(properties, dict):
                known_fields.update(properties)
            for child in node.values():
                fields(child)
        elif isinstance(node, list):
            for child in node:
                fields(child)
    fields(schema.model_json_schema())
    try:
        schema.model_validate_json(cleaned)
    except ValidationError as exc:
        details = []
        for error in exc.errors(include_url=False, include_input=False)[:8]:
            location = '.'.join(str(part) if isinstance(part, int) or part in known_fields else '<key>'
                                for part in error['loc'])
            detail = {'field': location, 'error': error['type']}
            context = error.get('ctx', {})
            # These constraints are from the declared schema, never response values.
            for key in ('expected', 'max_length', 'min_length', 'le', 'ge'):
                if key in context:
                    detail[key] = str(context[key])[:180]
            details.append(detail)
        return json.dumps(details, ensure_ascii=True, separators=(',', ':'))[:1200]
    return "Response is ambiguous; return exactly one JSON value matching the declared schema."


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


class ModelInvocationError(DomainError):
    """A typed model failure that preserves usage already returned by the provider."""

    def __init__(self, code: FailureCode, detail: str, usage: ModelUsage) -> None:
        self.usage = usage
        super().__init__(code, detail)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
    )


def _error_usage(error: DomainError) -> ModelUsage:
    if isinstance(error, ModelInvocationError):
        return error.usage
    return ModelUsage(input_tokens=0, output_tokens=0)


class ModelGateway:
    def __init__(
        self,
        config: ModelConfig,
        api_key: SecretStr,
        client: httpx.AsyncClient,
        failure_injector: FailureInjector | None = None,
        *,
        admission_workspace_id: UUID | None = None,
        admission_task_id: UUID | None = None,
        admission_sessions: async_sessionmaker[AsyncSession] | None = None,
        admission_controller: RedisAdmissionController | None = None,
    ) -> None:
        if (admission_task_id is None) != (admission_sessions is None):
            raise ValueError("model admission task context is incomplete")
        self.config = config
        self._api_key = api_key
        self._client = client
        self._failure_injector = failure_injector or FailureInjector()
        self._admission_workspace_id = admission_workspace_id
        self._admission_task_id = admission_task_id
        self._admission_sessions = admission_sessions
        self._admission_controller = admission_controller

    @asynccontextmanager
    async def _admission(self) -> AsyncIterator[None]:
        if self._admission_workspace_id is None:
            yield
            return
        owned_redis = None
        controller = self._admission_controller
        if controller is None:
            owned_redis, controller, configured_lease_ms = load_admission_controller()
        else:
            configured_lease_ms = 360_000
        lease_ms = max(
            configured_lease_ms,
            int((self.config.timeout_sec * 3 + 8) * 1000),
        )
        try:
            async with admitted_call(
                controller,
                self._admission_workspace_id,
                self.config.provider,
                "model." + self.config.model,
                is_model=True,
                lease_ms=lease_ms,
                sessions=self._admission_sessions,
                task_id=self._admission_task_id,
            ):
                yield
        finally:
            if owned_redis is not None:
                await owned_redis.aclose()

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
        started_at = time.perf_counter()
        LLM_CALL_TOTAL.labels(self.config.provider).inc()
        try:
            async with self._admission():
                return await self._post_admitted(path, payload)
        finally:
            LLM_LATENCY.labels(self.config.provider).observe(time.perf_counter() - started_at)

    async def _post_admitted(self, path: str, payload: dict[str, object]) -> httpx.Response:
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
                    # A deterministic provider rejection (for example an invalid
                    # credential, request, or endpoint redirect) is not a 5xx
                    # availability failure.  Misclassifying it as LLM_SERVER_ERROR
                    # would incorrectly authorize cross-model fallback.
                    raise DomainError(
                        FailureCode.CAPABILITY_UNAVAILABLE,
                        "model request rejected",
                    )
                else:
                    return response
            if attempt < 2:
                await asyncio.sleep(min(2**attempt, 4))
        raise DomainError(code, "model transport attempts exhausted")

    async def chat(
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        *,
        max_output_tokens: int | None = None,
        disable_thinking: bool = False,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> ModelReply:
        output_limit = (
            self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        )
        if output_limit < 1:
            raise ValueError("max_output_tokens must be positive")
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature must be between zero and two")
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [m.model_dump(include={"role", "content"}) for m in messages],
            "max_tokens": output_limit,
        }
        model_name = self.config.model.casefold()
        if disable_thinking and "qwen" in model_name:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if disable_thinking and (self.config.provider == "DeepSeek" or "deepseek" in model_name):
            payload["thinking"] = {"type": "disabled"}
        if json_mode and (
            "qwen" in model_name or self.config.provider == "DeepSeek" or "deepseek" in model_name
        ):
            payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = temperature
        response = await self._post(
            "chat/completions",
            payload,
            classification,
        )
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise DomainError(
                FailureCode.LLM_SCHEMA_ERROR, "invalid model response envelope"
            ) from None
        try:
            usage = ModelUsage(
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
            )
            LLM_TOKENS.labels(self.config.provider, "input").inc(usage.input_tokens)
            LLM_TOKENS.labels(self.config.provider, "output").inc(usage.output_tokens)
        except (KeyError, ValueError, TypeError):
            raise DomainError(
                FailureCode.LLM_SCHEMA_ERROR, "invalid model response envelope"
            ) from None
        try:
            if self._failure_injector.should_fail(FailurePoint.LLM_SCHEMA_CORRUPTION):
                data["choices"][0]["message"]["content"] = "{injected-corrupt-json"
            return ModelReply(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                usage=usage,
            )
        except (KeyError, IndexError, ValueError, TypeError):
            raise ModelInvocationError(
                FailureCode.LLM_SCHEMA_ERROR,
                "invalid model response envelope",
                usage,
            ) from None

    async def structured[T: BaseModel](
        self,
        messages: tuple[Message, ...],
        schema: type[T],
        classification: Classification,
        *,
        before_call: Callable[[], Awaitable[None]] | None = None,
        after_call: Callable[[ModelUsage], Awaitable[None]] | None = None,
        schema_instruction: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        validate_result: Callable[[T], str | None] | None = None,
        max_attempts: int = 3,
    ) -> tuple[T, ModelUsage]:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        schema_prompt = schema_instruction or (
            "Return only JSON matching this schema: "
            + json.dumps(schema.model_json_schema(), separators=(",", ":"))
        )
        prompt = messages + (
            Message(
                role="user",
                content=schema_prompt,
            ),
        )
        base_prompt = prompt
        total_in, total_out = 0, 0
        for repair in range(max_attempts):
            started_at = time.monotonic()
            try:
                if before_call is not None:
                    await before_call()
                reply = await self.chat(
                    prompt,
                    classification,
                    max_output_tokens=min(
                        self.config.max_output_tokens,
                        512 if max_output_tokens is None else max_output_tokens,
                    ),
                    disable_thinking=True,
                    json_mode=True,
                    temperature=temperature,
                )
            except DomainError as error:
                current_usage = _error_usage(error)
                if after_call is not None and (
                    current_usage.input_tokens or current_usage.output_tokens
                ):
                    await after_call(current_usage)
                consumed = _add_usage(
                    ModelUsage(input_tokens=total_in, output_tokens=total_out),
                    current_usage,
                )
                if consumed.input_tokens or consumed.output_tokens:
                    raise ModelInvocationError(
                        error.code,
                        error.detail,
                        consumed,
                    ) from error
                raise
            total_in += reply.usage.input_tokens
            total_out += reply.usage.output_tokens
            logger.warning(
                "structured model attempt schema=%s attempt=%s elapsed_sec=%.3f input_tokens=%s output_tokens=%s",
                schema.__name__,
                repair + 1,
                time.monotonic() - started_at,
                reply.usage.input_tokens,
                reply.usage.output_tokens,
            )
            if after_call is not None:
                # Account for every provider response before deciding whether a
                # schema repair is allowed. Aggregating usage only after all
                # repairs lets one structured call cross the task budget several
                # times before the runtime can observe it.
                await after_call(reply.usage)
            result = _parse_structured_reply(reply.content, schema)
            schema_error = _structured_schema_error(reply.content, schema) if result is None else None
            validation_error = (
                validate_result(result)
                if result is not None and validate_result is not None
                else None
            )
            if result is not None and validation_error is None:
                return result, ModelUsage(input_tokens=total_in, output_tokens=total_out)
            else:
                logger.warning(
                    "structured model attempt rejected schema=%s attempt=%s reason=%s",
                    schema.__name__,
                    repair + 1,
                    ("schema: " + str(schema_error)) if result is None else "evidence_validation",
                )
                if repair == max_attempts - 1:
                    failure_detail = validation_error or schema_error or "response was not valid schema JSON"
                    raise ModelInvocationError(
                        FailureCode.LLM_SCHEMA_ERROR,
                        "structured output invalid after "
                        f"{max_attempts} attempt(s): "
                        + failure_detail,
                        ModelUsage(input_tokens=total_in, output_tokens=total_out),
                    ) from None
                if result is None:
                    # Replaying a truncated or malformed response increases the next
                    # prompt without giving the model a reliable object to edit. Start
                    # again from the original evidence and ask for a shorter valid object.
                    prompt = base_prompt + (
                        Message(
                            role="user",
                            content=(
                                "The previous response failed schema validation: "
                                + str(schema_error) + ". "
                                "Regenerate it from the original input, keep answer prose concise, "
                                "and return JSON only."
                            ),
                        ),
                    )
                else:
                    # Keep only the immediately preceding valid JSON candidate. Repair
                    # prompts must not grow cumulatively across retries.
                    prompt = base_prompt + (
                        Message(role="assistant", content=reply.content[:8000]),
                        Message(
                            role="user",
                            content=(
                                "The JSON failed the required evidence validation: "
                                + str(validation_error)
                                + ". Correct only those defects using the original supplied "
                                "Evidence; return JSON only."
                            ),
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
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self._egress(classification)
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [m.model_dump(include={"role", "content"}) for m in messages],
            "stream": True,
            "max_tokens": min(
                self.config.max_output_tokens,
                max_output_tokens if max_output_tokens is not None else self.config.max_output_tokens,
            ),
            # Keep ordinary chat natural while reducing provider-default variance.
            # Grounded structured generation uses its stricter temperature=0 path.
            "temperature": 0.4,
        }
        model_name = self.config.model.casefold()
        if "qwen" in model_name:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if self.config.provider == "DeepSeek" or "deepseek" in model_name:
            payload["thinking"] = {"type": "disabled"}
        for attempt in range(3):
            emitted = False
            try:
                async with (
                    self._admission(),
                    self._client.stream(
                        "POST",
                        self.config.base_url.rstrip("/") + "/chat/completions",
                        headers=(
                            {"Authorization": "Bearer " + self._api_key.get_secret_value()}
                            if self._api_key.get_secret_value()
                            else {}
                        ),
                        json=payload,
                        timeout=self.config.timeout_sec,
                        follow_redirects=False,
                    ) as response,
                ):
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(min(2**attempt, 4))
                            continue
                        code = (
                            FailureCode.LLM_RATE_LIMIT
                            if response.status_code == 429
                            else FailureCode.LLM_SERVER_ERROR
                        )
                        raise DomainError(code, "stream transport attempts exhausted")
                    if response.status_code != 200:
                        raise DomainError(
                            FailureCode.CAPABILITY_UNAVAILABLE,
                            "stream request rejected",
                        )
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
                                FailureCode.LLM_SCHEMA_ERROR,
                                "invalid stream event",
                            ) from None
                        if content:
                            emitted = True
                            yield str(content)
                    if ended:
                        return
                    raise httpx.ReadTimeout("stream ended without completion")
            except httpx.TimeoutException:
                code = FailureCode.LLM_TIMEOUT
                detail = "stream timed out"
            except httpx.TransportError:
                code = FailureCode.LLM_SERVER_ERROR
                detail = "stream transport failed"
            if emitted or attempt == 2:
                raise DomainError(code, detail) from None
            await asyncio.sleep(min(2**attempt, 4))
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "unreachable stream retry state")


class FallbackModelGateway:
    """Fail over once after the primary gateway has exhausted its own recovery.

    The wrapper deliberately keeps availability failover narrower than the
    underlying gateway's retry policy:

    * ordinary and streaming calls fail over only for LLM_SERVER_ERROR;
    * structured calls may also fail over after schema repair is exhausted;
    * a stream can switch providers only before its first emitted chunk;
    * embeddings never fail over because models can use incompatible vector
      dimensions and spaces.

    Timeout, rate-limit, policy, budget, cancellation, and unexpected failures
    always propagate from the primary gateway unchanged.
    """

    def __init__(
        self,
        primary: ModelGateway,
        fallback: ModelGateway | None = None,
        *,
        on_fallback: (
            Callable[
                [
                    str,
                    ModelConfig,
                    ModelConfig,
                    Literal["STARTED", "USED", "FAILED", "CANCELLED"],
                    str | None,
                ],
                Awaitable[None],
            ]
            | None
        ) = None,
        on_usage: Callable[[ModelUsage], Awaitable[None]] | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback
        self._on_usage = on_usage
        self._used_fallback = False
        self._fallback_trigger: str | None = None
        self._consumed_usage = ModelUsage(input_tokens=0, output_tokens=0)

    @property
    def config(self) -> ModelConfig:
        """Expose the primary config for callers that inspect model limits."""

        return self._primary.config

    @property
    def primary(self) -> ModelGateway:
        return self._primary

    @property
    def fallback(self) -> ModelGateway | None:
        return self._fallback

    @property
    def used_fallback(self) -> bool:
        return self._used_fallback

    @property
    def fallback_trigger(self) -> str | None:
        return self._fallback_trigger

    @property
    def consumed_usage(self) -> ModelUsage:
        """All structured-generation usage observed across primary and fallback calls."""

        return self._consumed_usage

    @property
    def selected_config(self) -> ModelConfig:
        if self._used_fallback and self._fallback is not None:
            return self._fallback.config
        return self._primary.config

    async def _record_usage(self, usage: ModelUsage) -> None:
        self._consumed_usage = _add_usage(self._consumed_usage, usage)
        if self._on_usage is not None and (usage.input_tokens or usage.output_tokens):
            await self._on_usage(usage)

    async def _mark_fallback(self, trigger: str) -> None:
        if self._fallback is None or self._used_fallback:
            return
        self._used_fallback = True
        self._fallback_trigger = trigger
        logger.warning(
            "explicit model fallback selected",
            extra={
                "trigger": trigger,
                "primary_provider": self._primary.config.provider,
                "primary_model": self._primary.config.model,
                "fallback_provider": self._fallback.config.provider,
                "fallback_model": self._fallback.config.model,
            },
        )
        await self._record_fallback_outcome("STARTED")

    async def _record_fallback_outcome(
        self,
        outcome: Literal["STARTED", "USED", "FAILED", "CANCELLED"],
        error: BaseException | None = None,
    ) -> None:
        if (
            self._on_fallback is None
            or self._fallback is None
            or self._fallback_trigger is None
        ):
            return
        await self._on_fallback(
            self._fallback_trigger,
            self._primary.config,
            self._fallback.config,
            outcome,
            error.code.value if isinstance(error, DomainError) else None,
        )

    def _eligible_fallback(
        self,
        error: DomainError,
        *,
        structured: bool = False,
    ) -> ModelGateway | None:
        if self._fallback is None:
            return None
        if error.code is FailureCode.LLM_SERVER_ERROR:
            return self._fallback
        if structured and error.code is FailureCode.LLM_SCHEMA_ERROR:
            return self._fallback
        return None

    async def chat(
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        *,
        max_output_tokens: int | None = None,
        disable_thinking: bool = False,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> ModelReply:
        try:
            return await self._primary.chat(
                messages,
                classification,
                max_output_tokens=max_output_tokens,
                disable_thinking=disable_thinking,
                json_mode=json_mode,
                temperature=temperature,
            )
        except DomainError as error:
            fallback = self._eligible_fallback(error)
            if fallback is None:
                raise
            await self._mark_fallback(error.code.value)
            try:
                reply = await fallback.chat(
                    messages,
                    classification,
                    max_output_tokens=max_output_tokens,
                    disable_thinking=disable_thinking,
                    json_mode=json_mode,
                    temperature=temperature,
                )
            except asyncio.CancelledError as fallback_error:
                await self._record_fallback_outcome("CANCELLED", fallback_error)
                raise
            except Exception as fallback_error:
                await self._record_fallback_outcome("FAILED", fallback_error)
                raise
            await self._record_fallback_outcome("USED")
            return reply

    async def structured[T: BaseModel](
        self,
        messages: tuple[Message, ...],
        schema: type[T],
        classification: Classification,
        *,
        before_call: Callable[[], Awaitable[None]] | None = None,
        schema_instruction: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        validate_result: Callable[[T], str | None] | None = None,
        max_attempts: int = 3,
    ) -> tuple[T, ModelUsage]:
        primary_usage = ModelUsage(input_tokens=0, output_tokens=0)
        try:
            result, usage = await self._primary.structured(
                messages,
                schema,
                classification,
                before_call=before_call,
                after_call=self._record_usage,
                schema_instruction=schema_instruction,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                validate_result=validate_result,
                max_attempts=max_attempts,
            )
        except DomainError as error:
            primary_usage = _error_usage(error)
            fallback = self._eligible_fallback(error, structured=True)
            if fallback is None:
                raise
            await self._mark_fallback(error.code.value)
            try:
                result, usage = await fallback.structured(
                    messages,
                    schema,
                    classification,
                    before_call=before_call,
                    after_call=self._record_usage,
                    schema_instruction=schema_instruction,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    validate_result=validate_result,
                    max_attempts=max_attempts,
                )
            except asyncio.CancelledError as fallback_error:
                await self._record_fallback_outcome("CANCELLED", fallback_error)
                raise
            except DomainError as fallback_error:
                fallback_usage = _error_usage(fallback_error)
                await self._record_fallback_outcome("FAILED", fallback_error)
                combined = _add_usage(primary_usage, fallback_usage)
                if combined.input_tokens or combined.output_tokens:
                    raise ModelInvocationError(
                        fallback_error.code,
                        fallback_error.detail,
                        combined,
                    ) from fallback_error
                raise
            except Exception as fallback_error:
                await self._record_fallback_outcome("FAILED", fallback_error)
                raise
            await self._record_fallback_outcome("USED")
            return result, _add_usage(primary_usage, usage)
        return result, usage

    async def structured_quality_repair[T: BaseModel](
        self,
        messages: tuple[Message, ...],
        schema: type[T],
        classification: Classification,
        *,
        before_call: Callable[[], Awaitable[None]] | None = None,
        schema_instruction: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        validate_result: Callable[[T], str | None] | None = None,
        max_attempts: int = 3,
    ) -> tuple[T, ModelUsage]:
        """Repair a rejected final answer with the explicit backup when present.

        Callers must reuse their already verified Evidence packet. This method
        changes only synthesis and never repeats retrieval or bypasses validation.
        """

        target = self._fallback or self._primary
        if self._fallback is not None:
            await self._mark_fallback("QUALITY_REPAIR")
        try:
            result, usage = await target.structured(
                messages,
                schema,
                classification,
                before_call=before_call,
                after_call=self._record_usage,
                schema_instruction=schema_instruction,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                validate_result=validate_result,
                max_attempts=max_attempts,
            )
        except asyncio.CancelledError as error:
            await self._record_fallback_outcome("CANCELLED", error)
            raise
        except DomainError as error:
            await self._record_fallback_outcome("FAILED", error)
            raise
        except Exception as error:
            await self._record_fallback_outcome("FAILED", error)
            raise
        await self._record_fallback_outcome("USED")
        return result, usage

    async def embed(
        self, texts: tuple[str, ...], classification: Classification
    ) -> tuple[tuple[float, ...], ...]:
        return await self._primary.embed(texts, classification)

    async def stream(
        self,
        messages: tuple[Message, ...],
        classification: Classification,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        emitted = False
        try:
            async for chunk in self._primary.stream(
                messages,
                classification,
                max_output_tokens=max_output_tokens,
            ):
                emitted = True
                yield chunk
        except DomainError as error:
            fallback = self._eligible_fallback(error)
            if emitted or fallback is None:
                raise
            await self._mark_fallback(error.code.value)
            try:
                async for chunk in fallback.stream(
                    messages,
                    classification,
                    max_output_tokens=max_output_tokens,
                ):
                    yield chunk
            except asyncio.CancelledError as fallback_error:
                await self._record_fallback_outcome("CANCELLED", fallback_error)
                raise
            except Exception as fallback_error:
                await self._record_fallback_outcome("FAILED", fallback_error)
                raise
            await self._record_fallback_outcome("USED")
