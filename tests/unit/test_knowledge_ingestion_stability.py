import pytest

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.activities import (
    _classify_knowledge_exception,
    _knowledge_failure_is_terminal,
    _knowledge_failure_retryable,
)
from enterprise_agent.runtime.knowledge_ingestion import _embed_in_batches
from enterprise_agent.storage.knowledge_jobs import format_knowledge_ingestion_failure


async def test_embedding_requests_are_bounded_batches_with_progress() -> None:
    calls: list[tuple[str, ...]] = []
    progress: list[tuple[str, int, int]] = []

    async def embed(texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        calls.append(texts)
        return tuple((float(len(text)),) for text in texts)

    result = await _embed_in_batches(
        ("a", "bb", "ccc", "dddd", "eeeee"),
        embedder=embed,
        batch_size=2,
        progress=lambda phase, current, total: progress.append((phase, current, total)),
    )

    assert calls == [("a", "bb"), ("ccc", "dddd"), ("eeeee",)]
    assert result == ((1.0,), (2.0,), (3.0,), (4.0,), (5.0,))
    assert progress == [("EMBED", 1, 3), ("EMBED", 2, 3), ("EMBED", 3, 3)]


async def test_embedding_batch_rejects_provider_count_mismatch() -> None:
    async def embed(_: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()

    with pytest.raises(DomainError) as raised:
        await _embed_in_batches(("one",), embedder=embed, batch_size=1)

    assert raised.value.code is FailureCode.LLM_SCHEMA_ERROR


def test_ingestion_failure_is_typed_and_only_terminal_at_retry_limit() -> None:
    code, detail = _classify_knowledge_exception(
        DomainError(FailureCode.LLM_SERVER_ERROR, "model transport attempts exhausted")
    )

    assert code is FailureCode.LLM_SERVER_ERROR
    assert detail == "model transport attempts exhausted"
    assert _knowledge_failure_retryable(code) is True
    assert not _knowledge_failure_is_terminal(
        retryable=True,
        attempt=1,
        maximum_attempts=2,
    )
    assert _knowledge_failure_is_terminal(
        retryable=True,
        attempt=2,
        maximum_attempts=2,
    )
    assert format_knowledge_ingestion_failure(code, "embed", detail) == (
        "LLM_SERVER_ERROR [EMBED]: model transport attempts exhausted"
    )

