from collections.abc import Mapping
from typing import ClassVar

from enterprise_agent.capabilities.builtins import WebSearchAssessment
from enterprise_agent.contracts.domain import CapabilityRequest
from enterprise_agent.runtime.web_provider import (
    WebDiscoveryCandidate,
    WebFetchResult,
    WebSearchResult,
)


class ScriptedWebProvider:
    calls: ClassVar[list[str]] = []
    fetch_calls: ClassVar[list[str]] = []
    responses: ClassVar[Mapping[str, tuple[WebSearchResult, ...]]] = {}
    fetched: ClassVar[Mapping[str, WebFetchResult]] = {}
    candidates: ClassVar[Mapping[str, tuple[WebDiscoveryCandidate, ...]]] = {}

    def __init__(self, _config: object, _client: object) -> None:
        self.discovery_candidates: list[WebDiscoveryCandidate] = []

    @classmethod
    def configure(
        cls,
        responses: Mapping[str, tuple[WebSearchResult, ...]],
        fetched: Mapping[str, WebFetchResult] | None = None,
        candidates: Mapping[str, tuple[WebDiscoveryCandidate, ...]] | None = None,
    ) -> None:
        cls.calls = []
        cls.fetch_calls = []
        cls.responses = responses
        cls.fetched = fetched or {}
        cls.candidates = candidates or {}

    async def search(self, query: str, _limit: int) -> tuple[WebSearchResult, ...]:
        type(self).calls.append(query)
        self.discovery_candidates.extend(type(self).candidates.get(query, ()))
        return type(self).responses.get(query, ())

    async def fetch(self, url: str) -> WebFetchResult:
        type(self).fetch_calls.append(url)
        if url not in type(self).fetched:
            from enterprise_agent.contracts.domain import FailureCode
            from enterprise_agent.domain.states import DomainError
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "test page unavailable")
        return type(self).fetched[url]


async def research_task_context(
    _sessions: object,
    _request: CapabilityRequest,
) -> dict[str, object]:
    return {
        "original_request": "目标对象目前是什么状态？",
        "resolved_request": "查询目标对象当前状态",
        "search_queries": ["precise dated query", "broader discovery query"],
    }


async def research_coverage_assessment(
    _sessions: object,
    _request: CapabilityRequest,
    *,
    context: dict[str, object],
    attempted_queries: tuple[str, ...],
    results: tuple[WebSearchResult, ...],
) -> WebSearchAssessment:
    assert context["original_request"] == "目标对象目前是什么状态？"
    assert attempted_queries
    return WebSearchAssessment(
        sufficient=bool(results),
        missing_aspects=() if results else ("direct current evidence",),
        additional_queries=() if results else ("authoritative source class query",),
    )


class SlowSearchPipeline:
    """A completed source followed by work that outlives the provider deadline."""
    def __init__(self, result):
        self.result = result
        self.cancelled = False

    async def __call__(self, value, client, base_url, observed, pending):
        import asyncio
        observed.append(self.result)
        async def unfinished_hint():
            try:
                await asyncio.sleep(60)
                return []
            finally:
                self.cancelled = True
        pending.append(asyncio.create_task(unfinished_hint()))
        await asyncio.sleep(60)
        return []
