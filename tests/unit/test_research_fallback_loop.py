import hashlib
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import CapabilityRequest
from enterprise_agent.runtime.web_provider import WebFetchResult, WebSearchResult
from tests.fakes.web_provider import (
    ScriptedWebProvider,
    research_coverage_assessment,
    research_task_context,
)


async def test_web_search_replans_after_empty_primary_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snippet = (
        "Official publication date: 2026-09-14. "
        "The requested entity and current fact are stated on this page."
    )
    recovered = WebSearchResult(
        title="Authoritative current result",
        url="https://authority.example/current",
        snippet=snippet,
        published_at="2026-09-14",
        observed_at="2026-09-14T01:00:00Z",
        content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
    )
    ScriptedWebProvider.configure({"authoritative source class query": (recovered,)})
    coverage_calls = 0

    async def assessment(*args: object, **kwargs: object) -> builtins.WebSearchAssessment:
        nonlocal coverage_calls
        coverage_calls += 1
        return await research_coverage_assessment(*args, **kwargs)

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", research_task_context)
    monkeypatch.setattr(
        builtins,
        "_assess_web_search",
        assessment,
    )
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="web.search",
        capability_version="1.0.0",
        arguments={"query": "precise dated query", "limit": 3},
    )

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        request,
    )

    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.calls == [
        "precise dated query",
        "authoritative source class query",
        "broader discovery query",
    ]
    assert coverage_calls == 1
    assert result.output == {
        "query": "precise dated query",
        "attempted_queries": ["precise dated query", "authoritative source class query", "broader discovery query"],
        "results": [recovered.model_dump(mode="json", exclude={"schema_version"})],
    }


async def test_web_search_opens_model_selected_authoritative_source_after_empty_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    official_url = "https://government.example/office"
    page = "Official Office\nThe current office holder is Alex Example."
    fetched = WebFetchResult(
        url=official_url,
        content=page,
        content_hash=hashlib.sha256(page.encode()).hexdigest(),
    )
    ScriptedWebProvider.configure({}, {official_url: fetched})

    async def assessment(*_args: object, **kwargs: object) -> builtins.WebSearchAssessment:
        results = cast(tuple[WebSearchResult, ...], kwargs["results"])
        return builtins.WebSearchAssessment(
            sufficient=bool(results),
            missing_aspects=() if results else ("authoritative first-party evidence",),
            additional_queries=(),
            candidate_urls=() if results else (official_url,),
        )

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", research_task_context)
    monkeypatch.setattr(builtins, "_assess_web_search", assessment)
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="web.search",
        capability_version="1.0.0",
        arguments={"query": "precise dated query", "limit": 3},
    )

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        request,
    )

    assert ScriptedWebProvider.calls == [
        "precise dated query",
        "broader discovery query",
    ]
    assert ScriptedWebProvider.fetch_calls == [official_url]
    output = cast(dict[str, object], result.output)
    results = cast(list[dict[str, object]], output["results"])
    assert results[0]["url"] == official_url
    snippet = cast(str, results[0]["snippet"])
    assert "网页正文摘录：" in snippet
    assert "The current office holder is Alex Example." in snippet


@pytest.mark.parametrize("missing_day", [None, 9])
@pytest.mark.parametrize("candidate_origin", ["search", "assessment"])
async def test_historical_observations_do_not_override_insufficient_semantic_coverage(
    monkeypatch: pytest.MonkeyPatch, missing_day: int | None, candidate_origin: str,
) -> None:
    from enterprise_agent.contracts.domain import AnswerContract, BoundedResearchPlan, SourcePlan

    query = "Miami weather September 7-13 2026"
    url = "https://www.wunderground.com/history/monthly/us/fl/miami/KMIA/date/2026-09"
    page = "Miami Daily Observations\nTemperature Max Average Min Precipitation (in)\n" + "\n".join(
        f"9/{day}/2026\n90\n82\n74\n0.02" for day in range(7, 14) if day != missing_day
    )
    plan = BoundedResearchPlan(
        web_queries=(query, "Miami September daily observations"), retrieval_queries=(), result_limit=5,
        source_plan=SourcePlan(required_sources=("PUBLIC_WEB",), preferred_sources=("OFFICIAL_OR_FIRST_PARTY",), fallback_sources=("GENERAL_WEB",), forbidden_sources=("PARAMETRIC_KNOWLEDGE",)),
        answer_contract=AnswerContract(require_explicit_dates=True, partial_answer_policy="REQUIRE_COMPLETE", organization="STRUCTURED_SUMMARY", answer_type="SUMMARY", freshness="HISTORICAL", completeness="COMPLETE", required_aspects=("2026-09-07", "2026-09-13"), minimum_answer_items=1, maximum_answer_items=8, minimum_evidence_items=1, minimum_independent_sources=1),
    )
    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": query, "research_plan": plan.model_dump(mode="json")}

    async def assessment(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        return builtins.WebSearchAssessment(sufficient=False, candidate_urls=(url,))

    daily_url = "https://www.wunderground.com/history/daily/us/fl/miami/KMIA"
    assert builtins._historical_weather_source_urls((daily_url,), plan) == (url,)
    assert builtins._historical_weather_source_urls(("https://www.wunderground.com.evil.test/history/daily/us/fl/miami/KMIA",), plan) == ()
    assert builtins._historical_weather_source_urls(("https://www.wunderground.com/history/daily/xx/other-town/TEST/date/2024-1-1",), plan) == ("https://www.wunderground.com/history/monthly/xx/other-town/TEST/date/2026-09",)
    discovery_text = "Miami Weather History. Current station conditions; historical daily, weekly and monthly observations are available from the archive."
    discovered = WebSearchResult(title="Miami Weather History", url=daily_url, snippet=discovery_text, published_at=None, observed_at="2026-09-17T00:00:00Z", content_hash=hashlib.sha256(discovery_text.encode()).hexdigest())
    ScriptedWebProvider.configure({query: (discovered,)} if candidate_origin == "search" else {}, {url: WebFetchResult(url=url, content=page, content_hash=hashlib.sha256(page.encode()).hexdigest())})
    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assessment)
    request = CapabilityRequest(command_id=uuid4(), operation_id=uuid4(), workspace_id=uuid4(), task_id=uuid4(), step_id=uuid4(), attempt_id=uuid4(), fencing_token=1, capability_id="web.search", capability_version="1.0.0", arguments={"query": query, "limit": 5})
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), request)
    # The assessor explicitly reports insufficient scope. Date rows alone must
    # not override it, even when every requested date is present.
    assert ScriptedWebProvider.calls == list(plan.web_queries)
    assert ScriptedWebProvider.fetch_calls == [url]
    assert result.outcome == "SUCCEEDED"
    output = cast(dict[str, object], result.output)
    results = cast(list[dict[str, object]], output["results"])
    assert page in str(results[0]["snippet"])
    assert results[0]["content_hash"] == hashlib.sha256(str(results[0]["snippet"]).encode()).hexdigest()
    opened = WebSearchResult.model_validate(results[0])
    stricter = plan.model_copy(update={"answer_contract": plan.answer_contract.model_copy(update={"minimum_independent_sources": 2})})
    assert not builtins._historical_weather_table_covers_interval(query, (opened,), stricter)


def test_discovered_links_are_ranked_without_inventing_endpoints():
    links = (("https://example.org/privacy", "Privacy"),
             ("https://example.org/reports/q3", "Inventory quarterly report"),
             ("https://user:password@example.org/data", "Inventory data"))
    selected = builtins._rank_discovery_links("inventory quarterly report", links)
    assert selected[0] == links[1]
    assert set(selected).issubset(set(links))
    assert links[2] not in selected


def test_discovery_metadata_is_not_published_as_factual_evidence():
    item = WebSearchResult(title="Inventory", url="https://example.org", snippet="A directory",
        observed_at="2026-09-17T00:00:00Z", content_hash="a" * 64,
        source_links=(("https://example.org/data", "Unverified inventory figures"),))
    assert item.source_links
    assert "source_links" not in item.model_dump()
    assert "Unverified inventory figures" not in item.snippet


def test_discovery_ranking_does_not_mistake_a_hostname_for_every_link_relevance():
    links = (("https://inventory.example/privacy", "Privacy"),
        ("https://inventory.example/reports/quarter-three", "Inventory availability and delivery report for the current quarter"),
        ("https://inventory.example/", "Home"))
    assert builtins._rank_discovery_links("inventory current report", links)[0] == links[1]


def test_chinese_discovery_ranking_handles_unsegmented_questions():
    links = (("https://example.org/index", "首页"),
             ("https://example.org/reports", "供应链库存报告"),
             ("https://example.org/contact", "联系"))
    assert builtins._rank_discovery_links("请查看供应链库存报告的最新情况", links)[0] == links[1]
