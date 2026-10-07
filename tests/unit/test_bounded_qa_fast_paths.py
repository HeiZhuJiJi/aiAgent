import hashlib
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import (
    AnswerContract,
    BoundedResearchPlan,
    CapabilityRequest,
    SourcePlan,
)
from enterprise_agent.runtime.web_provider import WebFetchResult, WebSearchResult
from tests.fakes.web_provider import ScriptedWebProvider


def _request(query: str) -> CapabilityRequest:
    return CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="web.search",
        capability_version="1.0.0",
        arguments={"query": query, "limit": 4},
    )


def _result(*, url: str, title: str, body: str, published_at: str | None = None) -> WebSearchResult:
    observed_at = "2026-09-14T01:00:00Z"
    snippet = (
        f"{title}\n来源发布日期：{published_at or '未提供'}\n"
        f"检索时间：{observed_at}\n\n网页正文摘录：\n{body}"
    )
    return WebSearchResult(
        title=title,
        url=url,
        snippet=snippet,
        published_at=published_at,
        observed_at=observed_at,
        content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
    )


async def test_authoritative_opened_result_still_checks_semantic_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "Python current stable version"
    source = _result(
        url="https://www.python.org/downloads/",
        title="Download Python",
        body=(
            "The official downloads page lists the current stable Python release and "
            "provides installers, release notes, and supported platform details."
        ),
    )
    ScriptedWebProvider.configure({query: (source,)})
    coverage_calls = 0

    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": query, "search_queries": [query]}

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        nonlocal coverage_calls
        coverage_calls += 1
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(query),
    )

    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.calls == [query]
    assert coverage_calls == 1


async def test_ambiguous_weather_result_keeps_coverage_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "广州黄埔明天天气怎么样"
    source = _result(
        url="https://www.weather.com.cn/weather/101280111.shtml",
        title="黄埔天气预报",
        body="全国广东广州黄埔天气页面提供逐日天气、最高和最低气温、降雨概率及风力信息。",
    )
    ScriptedWebProvider.configure({query: (source,)})
    coverage_calls = 0

    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": query, "search_queries": [query]}

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        nonlocal coverage_calls
        coverage_calls += 1
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)

    await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(query),
    )

    assert ScriptedWebProvider.calls == [query]
    assert coverage_calls == 1


async def test_coverage_planner_queries_preempt_router_recovery_after_empty_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "precise current query"
    first_fallback = "broader current query"
    second_fallback = "official source query"
    planner_queries = tuple(f"planner recovery {index}" for index in range(4))
    ScriptedWebProvider.configure({})
    coverage_calls = 0

    async def context(*_args: object) -> dict[str, object]:
        return {
            "resolved_request": query,
            "search_queries": [query, first_fallback, second_fallback],
        }

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        nonlocal coverage_calls
        coverage_calls += 1
        return builtins.WebSearchAssessment(
            sufficient=False,
            additional_queries=planner_queries,
        )

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(query),
    )

    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.calls == [query, *planner_queries]
    assert coverage_calls == 1


def _evidence_payload(
    *,
    host: str,
    quality: str,
    substantive_characters: int,
) -> dict[str, object]:
    return {
        "evidence_id": str(uuid4()),
        "locator": f"https://{host}/article",
        "source_quality": quality,
        "opened_page": True,
        "substantive_characters": substantive_characters,
    }


def test_deterministic_selector_only_skips_clear_quality_gap() -> None:
    strong = [
        _evidence_payload(
            host=f"source-{index}.gov",
            quality="PRIMARY_OR_OFFICIAL",
            substantive_characters=800,
        )
        for index in range(4)
    ]
    weak = _evidence_payload(
        host="thin.example",
        quality="GENERAL_WEB",
        substantive_characters=60,
    )

    assert (
        builtins._deterministic_web_evidence_selection(
            [*strong, weak],
            minimum_items=3,
            maximum_items=4,
            minimum_publishers=2,
        )
        == strong
    )

    ambiguous = _evidence_payload(
        host="www.reuters.com",
        quality="ESTABLISHED_EDITORIAL",
        substantive_characters=800,
    )
    assert (
        builtins._deterministic_web_evidence_selection(
            [*strong, ambiguous],
            minimum_items=3,
            maximum_items=4,
            minimum_publishers=2,
        )
        is None
    )


def test_grounded_answer_must_cover_explicit_office_holder_roles() -> None:
    candidate = builtins.WebStructuredAnswer(
        answer="一篇使馆新闻提到澳大利亚总督参加活动。",
        answer_status="ANSWERED",
        evidence_ids=(uuid4(),),
        claims=(),
    )

    assert (
        builtins._answer_core_coverage_error(
            candidate,
            "现在澳大利亚的总统是谁",
            None,
        )
        is not None
    )


def test_current_emperor_cannot_disappear_from_a_multi_role_answer() -> None:
    candidate = builtins.WebStructuredAnswer(
        answer="The current prime minister of Japan is named in the cited page.",
        answer_status="ANSWERED",
        evidence_ids=(uuid4(),),
    )
    assert (
        builtins._answer_core_coverage_error(
            candidate,
            "Who are the current prime minister and emperor of Japan?",
            None,
        )
        is not None
    )


async def test_terminal_refusals_and_exhausted_repairs_still_receive_quality_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id = uuid4()
    terminal = builtins.WebStructuredAnswer(
        answer="The available evidence does not support a reliable answer.",
        answer_status="NO_RELIABLE_RESULTS",
        evidence_ids=(evidence_id,),
    )
    answered = builtins.WebStructuredAnswer(
        answer="The official page supports the answer.",
        answer_status="ANSWERED",
        evidence_ids=(evidence_id,),
    )
    calls = 0
    recorded: list[builtins.AnswerQualityAssessment] = []

    async def assess(*_args: object, **_kwargs: object) -> builtins.AnswerQualityAssessment:
        nonlocal calls
        calls += 1
        return builtins.AnswerQualityAssessment(
            acceptable=True,
            answered_core_request=True,
            language_matches=True,
            appropriate_depth=True,
        )

    async def record(
        _sessions: object,
        _request: object,
        assessment: builtins.AnswerQualityAssessment | None,
    ) -> None:
        if assessment is not None:
            recorded.append(assessment)

    monkeypatch.setattr(builtins, "_assess_answer_quality", assess)
    monkeypatch.setattr(builtins, "_record_answer_quality_assessment", record)
    sessions = cast(async_sessionmaker[AsyncSession], object())
    gateway = cast(builtins.ModelGateway, object())
    request = _request("current fact")

    assert (
        await builtins._assess_answer_quality_if_needed(
            sessions,
            request,
            gateway,
            instruction="current fact",
            resolved_query="current fact",
            candidate=terminal,
            evidence_payload=[],
            grounding_validation_failures=0,
        )
        is not None
    )
    assert calls == 1

    assert (
        await builtins._assess_answer_quality_if_needed(
            sessions,
            request,
            gateway,
            instruction="current fact",
            resolved_query="current fact",
            candidate=answered,
            evidence_payload=[],
            grounding_validation_failures=0,
        )
    ) is not None
    assert calls == 2
    assert len(recorded) == 2

    assert (
        await builtins._assess_answer_quality_if_needed(
            sessions,
            request,
            gateway,
            instruction="current fact",
            resolved_query="current fact",
            candidate=answered,
            evidence_payload=[],
            grounding_validation_failures=3,
        )
        is not None
    )
    assert calls == 3


def test_answer_quality_decision_converts_legacy_boolean_rejection_to_revision() -> None:
    legacy_rejection = builtins.AnswerQualityAssessment(
        acceptable=False,
        answered_core_request=False,
        language_matches=True,
        appropriate_depth=False,
        critique="The refusal ignored supported evidence.",
    )
    partial = builtins.AnswerQualityAssessment(
        decision="PARTIAL_ACCEPT",
        acceptable=False,
        answered_core_request=True,
        language_matches=True,
        appropriate_depth=True,
        supported_claim_indexes=(1,),
        unsupported_claim_indexes=(2,),
    )

    assert builtins._answer_quality_decision(legacy_rejection) == "REVISE"
    assert builtins._answer_quality_decision(partial) == "PARTIAL_ACCEPT"


async def test_generation_repair_still_reviews_quality_without_another_generation_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def assess(*_args: object, **_kwargs: object) -> builtins.AnswerQualityAssessment:
        nonlocal calls
        calls += 1
        return builtins.AnswerQualityAssessment(
            acceptable=True,
            answered_core_request=True,
            language_matches=True,
            appropriate_depth=True,
        )

    async def record(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(builtins, "_assess_answer_quality", assess)
    monkeypatch.setattr(builtins, "_record_answer_quality_assessment", record)
    candidate = builtins.WebStructuredAnswer(
        answer="Supported answer",
        answer_status="ANSWERED",
        evidence_ids=(uuid4(),),
    )
    result = await builtins._assess_answer_quality_if_needed(
        cast(async_sessionmaker[AsyncSession], object()),
        _request("current fact"),
        cast(builtins.ModelGateway, object()),
        instruction="current fact",
        resolved_query="current fact",
        candidate=candidate,
        evidence_payload=[],
        grounding_validation_failures=1,
        generation_attempts=2,
    )
    assert result is not None and result.acceptable
    assert calls == 1
    assert not builtins._grounded_repair_available(2, 12.0)
    assert not builtins._grounded_repair_available(1, None)
    assert builtins._grounded_repair_available(1, 12.0)


def _answer_contract(
    answer_type: str,
    *,
    completeness: str = "BEST_EFFORT",
    minimum_answer_items: int | None = 1,
    maximum_answer_items: int | None = 1,
    minimum_evidence_items: int = 1,
    minimum_independent_sources: int = 1,
) -> AnswerContract:
    return AnswerContract.model_validate(
        {
            "answer_type": answer_type,
            "required_aspects": ["direct answer"],
            "completeness": completeness,
            "freshness": "CURRENT",
            "minimum_answer_items": minimum_answer_items,
            "maximum_answer_items": maximum_answer_items,
            "require_explicit_dates": False,
            "minimum_evidence_items": minimum_evidence_items,
            "minimum_independent_sources": minimum_independent_sources,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "CONCISE" if answer_type == "FACT" else "BULLETS",
        }
    )


def test_answer_contract_keeps_narrow_facts_fast_and_reviews_complex_answers() -> None:
    candidate = builtins.WebStructuredAnswer(
        answer="The official page supports the answer.",
        answer_status="ANSWERED",
        evidence_ids=(uuid4(),),
    )

    assert not builtins._answer_quality_requires_model(
        candidate,
        grounding_validation_failures=0,
        answer_contract=_answer_contract("FACT"),
    )
    assert builtins._answer_quality_requires_model(
        candidate,
        grounding_validation_failures=0,
        answer_contract=_answer_contract(
            "LIST",
            completeness="COMPLETE",
            minimum_answer_items=2,
            maximum_answer_items=5,
        ),
    )
    assert builtins._answer_quality_requires_model(
        candidate,
        grounding_validation_failures=1,
        answer_contract=_answer_contract("FACT"),
    )


def test_web_execution_prefers_the_validated_research_plan_queries() -> None:
    plan = BoundedResearchPlan(
        web_queries=("official current release", "independent release coverage"),
        retrieval_queries=(),
        source_plan=SourcePlan(
            required_sources=("PUBLIC_WEB",),
            preferred_sources=("OFFICIAL_OR_FIRST_PARTY", "INDEPENDENT_MEDIA"),
            fallback_sources=("GENERAL_WEB",),
            forbidden_sources=("PARAMETRIC_KNOWLEDGE",),
        ),
        result_limit=4,
        answer_contract=_answer_contract("FACT"),
    )

    assert builtins._planned_web_queries(
        {
            "search_queries": ["legacy query that must not win"],
            "research_plan": plan.model_dump(mode="json"),
        },
        "legacy primary that must not win",
    ) == ("official current release", "independent release coverage")


def test_deterministic_web_coverage_enforces_the_research_contract() -> None:
    query = "Acme latest announcements"
    official = _result(
        url="https://www.acme.com/news",
        title="Acme News",
        body="Acme published a current announcement with enough substantive source detail.",
    )
    independent = _result(
        url="https://www.reuters.com/technology/acme-update",
        title="Acme update",
        body="Reuters independently reported the current Acme announcement and its impact.",
    )
    narrow_plan = BoundedResearchPlan(
        web_queries=(query,),
        retrieval_queries=(),
        source_plan=SourcePlan(
            required_sources=("PUBLIC_WEB",),
            preferred_sources=("OFFICIAL_OR_FIRST_PARTY",),
            fallback_sources=("GENERAL_WEB",),
            forbidden_sources=("PARAMETRIC_KNOWLEDGE",),
        ),
        result_limit=4,
        answer_contract=_answer_contract("FACT"),
    )
    broad_plan = narrow_plan.model_copy(
        update={
            "answer_contract": _answer_contract(
                "LIST",
                completeness="COMPLETE",
                minimum_answer_items=2,
                maximum_answer_items=5,
                minimum_evidence_items=2,
                minimum_independent_sources=2,
            )
        }
    )

    assert builtins._deterministic_web_search_sufficient(
        query,
        (official,),
        narrow_plan,
    )
    assert not builtins._deterministic_web_search_sufficient(
        query,
        (official,),
        broad_plan,
    )
    # The hard minima now pass, but a multi-item contract still requires the one
    # bounded semantic coverage assessment rather than a rule-only short circuit.
    assert not builtins._deterministic_web_search_sufficient(
        query,
        (official, independent),
        broad_plan,
    )


def test_search_plan_requires_evidence_for_each_current_office_role() -> None:
    query = "Who are the current prime minister and emperor of Japan?"
    plan = BoundedResearchPlan(
        web_queries=(query,),
        retrieval_queries=(),
        source_plan=SourcePlan(
            required_sources=("PUBLIC_WEB",),
            forbidden_sources=("PARAMETRIC_KNOWLEDGE",),
        ),
        result_limit=4,
        answer_contract=_answer_contract("FACT"),
    )
    prime_minister = _result(
        url="https://japan.kantei.go.jp/",
        title="Prime Minister's Office of Japan",
        body="The Prime Minister of Japan spoke at a public event this week.",
    )
    emperor = _result(
        url="https://www.kunaicho.go.jp/",
        title="Imperial Household Agency",
        body="The Emperor of Japan attended a public ceremony this week.",
    )
    assert not builtins._web_search_meets_plan_minima(query, (prime_minister,), plan)
    assert builtins._web_search_meets_plan_minima(query, (prime_minister, emperor), plan)

    implicit_current = "日本首相是谁，那天皇呢？"
    implicit_plan = plan.model_copy(
        update={
            "answer_contract": plan.answer_contract.model_copy(
                update={"required_aspects": ("日本首相", "日本天皇")}
            )
        }
    )
    assert not builtins._web_search_meets_plan_minima(
        implicit_current, (prime_minister,), implicit_plan
    )


async def test_multi_role_search_preserves_the_second_planned_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "Who are the current prime minister and emperor of Japan?"
    first_query = "current prime minister of Japan"
    second_query = "current emperor of Japan"
    contract = _answer_contract("FACT").model_copy(
        update={"required_aspects": ("current prime minister", "current emperor")}
    )
    plan = BoundedResearchPlan(
        web_queries=(first_query, second_query),
        retrieval_queries=(),
        source_plan=SourcePlan(
            required_sources=("PUBLIC_WEB",),
            forbidden_sources=("PARAMETRIC_KNOWLEDGE",),
        ),
        result_limit=4,
        answer_contract=contract,
    )
    ScriptedWebProvider.configure(
        {
            first_query: (
                _result(
                    url="https://japan.kantei.go.jp/",
                    title="Prime Minister's Office of Japan",
                    body="The current Prime Minister of Japan spoke at a public event.",
                ),
            ),
            second_query: (
                _result(
                    url="https://www.kunaicho.go.jp/",
                    title="Imperial Household Agency",
                    body="The current Emperor of Japan attended a public ceremony.",
                ),
            ),
        }
    )

    async def context(*_args: object) -> dict[str, object]:
        return {
            "resolved_request": question,
            "research_plan": plan.model_dump(mode="json"),
        }

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        return builtins.WebSearchAssessment(
            sufficient=True,
            additional_queries=("another prime minister query",) * 4,
        )

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)
    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(first_query),
    )
    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.calls[:2] == [first_query, second_query]


async def test_two_city_forecast_searches_both_planned_cities_even_after_first_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "明天东莞和广州的天气怎么样"
    first_query = "2026年9月18日 东莞 天气预报"
    second_query = "2026年9月18日 广州 天气预报"
    broadened_query = "广州 天气预报"
    contract = _answer_contract("FORECAST").model_copy(
        update={"required_aspects": ("东莞天气", "广州天气")}
    )
    plan = BoundedResearchPlan(
        web_queries=(first_query, second_query),
        retrieval_queries=(),
        source_plan=SourcePlan(
            required_sources=("PUBLIC_WEB",), forbidden_sources=("PARAMETRIC_KNOWLEDGE",)
        ),
        result_limit=4,
        answer_contract=contract,
    )
    ScriptedWebProvider.configure(
        {
            first_query: (
                _result(
                    url="https://www.nmc.cn/publish/forecast/AGD/dongguan.html",
                    title="东莞-天气预报",
                    body="东莞明天多云，最高33℃，最低25℃。东莞气象台提供逐日天气预报。",
                ),
            ),
            second_query: (),
            broadened_query: (
                _result(
                    url="https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
                    title="广州-天气预报",
                    body="广州明天多云，最高32℃，最低26℃。广州气象台提供逐日天气预报。",
                ),
            ),
        },
        {
            url: WebFetchResult(
                url=url,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            )
            for url, content in (
                (
                    "https://www.nmc.cn/publish/forecast/AGD/dongguan.html",
                    "东莞-天气预报\n09/18\n多云\n33℃\n25℃",
                ),
                (
                    "https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
                    "广州-天气预报\n09/18\n多云\n32℃\n26℃",
                ),
            )
        },
    )

    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": question, "research_plan": plan.model_dump(mode="json")}

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(first_query),
    )

    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.calls[:2] == [first_query, second_query]
    assert ScriptedWebProvider.calls[2] == broadened_query
    results = result.output["results"]
    assert any("09/18" in item["snippet"] for item in results)


async def test_official_http_forecast_result_is_refetched_over_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "明天广州天气预报"
    http_url = "http://www.nmc.cn/publish/forecast/AGD/guangzhou.html"
    https_url = "https://www.nmc.cn/publish/forecast/AGD/guangzhou.html"
    content = "广州-天气预报\n09/18\n多云\n32℃\n26℃"
    ScriptedWebProvider.configure(
        {
            query: (
                _result(
                    url=http_url,
                    title="广州-天气预报",
                    body="广州气象台发布官方每日天气预报，页面包含最高气温和最低气温。",
                ),
            )
        },
        {
            https_url: WebFetchResult(
                url=https_url,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            )
        },
    )

    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": query, "search_queries": [query]}

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)

    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()),
        _request(query),
    )

    assert result.outcome == "SUCCEEDED"
    assert ScriptedWebProvider.fetch_calls == [https_url]


def test_web_excerpt_retains_named_role_beyond_navigation() -> None:
    fact = "Example Person, Chief Justice of the Example Republic, leads the court."
    content = "Court biographies\n网页正文摘录：\n" + "Navigation menu\n" * 160 + fact
    excerpt = builtins._focused_web_excerpt(content, "首席大法官是谁", 600)
    assert fact in excerpt
    assert len(excerpt) <= 600


def test_historical_excerpt_keeps_requested_rows_and_units() -> None:
    content = (
        "Weather history\n网页正文摘录：\n"
        + "Navigation\n" * 180
        + "Daily Observations\nDate\nTemperature (°F)\nMax\nMin\n"
        + "9/14/2026\n91\n78\n9/13/2026\n92\n76\n9/7/2026\n90\n74\n9/6/2026\n89\n75\n"
    )
    excerpt = builtins._focused_web_excerpt(content, "2026-09-07至2026-09-13", 700)
    assert "Temperature (°F)" in excerpt
    assert "9/13/2026\n92\n76" in excerpt
    assert "9/7/2026\n90\n74" in excerpt
    assert "9/6/2026\n89\n75" not in excerpt
    assert len(excerpt) <= 700


def test_us_date_normalization_supports_equivalent_date_without_inventing_values() -> None:
    assert "2026-09-13" in builtins._normalized_fact_text("9/13/2026")
    assert "2026-09-13" in builtins._normalized_fact_text("2026年9月13日")
    assert "2026-09-14" not in builtins._normalized_fact_text("9/13/2026")


def test_explicit_official_government_role_query_recognizes_real_primary_source():
    assert builtins._primary_web_source(
        "President of the United States official White House",
        "https://www.whitehouse.gov/administration/",
        "President of the United States",
    )
    assert not builtins._primary_web_source(
        "OpenAI official release", "https://openai-news.example.org/", "unverified OpenAI news"
    )


def test_direct_source_link_discovery_does_not_treat_links_as_evidence_or_allow_other_hosts():
    from enterprise_agent.runtime.web_provider import WebFetchResult
    page = WebFetchResult(url="https://sports.example/team", content="Index", content_hash="a" * 64,
        source_links=(("https://sports.example/report/1", "Inter Miami victory recap"),
                      ("http://127.0.0.1/private", "Inter Miami victory recap"),
                      ("https://user:secret@sports.example/report", "Inter Miami victory recap")))
    assert builtins._web_followup_urls("Inter Miami latest match", page) == ("https://sports.example/report/1",)
    assert page.content == "Index"


@pytest.mark.parametrize("answer", [
    "比赛于2026 年 9 月 16 日进行，结果2-0。",
    "比赛于2026-09-16进行，结果2-0。",
])
def test_latest_match_calendar_guard_accepts_valid_date_formats(answer):
    candidate = builtins.WebStructuredAnswer(answer=answer, answer_status="ANSWERED", evidence_ids=(uuid4(),))
    assert builtins._answer_core_coverage_error(candidate, "球队最新比赛结果是什么", None) is None
    undated = candidate.model_copy(update={"answer": "本赛季比赛结果2-0。"})
    assert "calendar" in builtins._answer_core_coverage_error(undated, "球队最新比赛结果是什么", None)


def test_historical_interval_prefers_complete_table_without_relaxing_source_minimum():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    def item(url, content):
        return base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB", "locator": url,
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()})
    table = item("https://weather.example/history", "Daily records\n网页正文摘录：\n9/7/2026 92 F\n9/13/2026 91 F")
    index = item("https://archive.example/", "General climate archive: select a station and date.")
    contract = _answer_contract("SUMMARY").model_copy(update={"freshness": "HISTORICAL", "required_aspects": ("weather 2026-09-07 through 2026-09-13",)})
    payload = builtins._analysis_evidence_payload((index, table), "web-research", "weather", contract)
    assert payload[0]["evidence_id"] == str(table.evidence_id)
    assert {x["evidence_id"] for x in payload} == {str(table.evidence_id), str(index.evidence_id)}
    assert builtins._analysis_evidence_payload((index, table), "web-research", "weather", contract, web_source_limit=1)[0]["evidence_id"] == str(table.evidence_id)
    diverse = contract.model_copy(update={"minimum_evidence_items": 2, "minimum_independent_sources": 2})
    payload = builtins._analysis_evidence_payload((index, table), "web-research", "weather", diverse)
    assert len(payload) == 2


def test_document_policy_advice_requires_usefulness_review() -> None:
    from enterprise_agent.capabilities.builtins import WebStructuredAnswer
    candidate = WebStructuredAnswer(answer_status="ANSWERED", answer="A grounded but potentially overrestrictive policy answer.", claims=(), evidence_ids=(uuid4(),))
    assert builtins._answer_quality_requires_model(
        candidate, grounding_validation_failures=0,
        answer_contract=_answer_contract("FACT").model_copy(update={"answer_type": "DOCUMENT_QA", "freshness": "SOURCE_BOUND"}),
    )


def test_forecast_location_ignores_relative_period_and_optional_metrics():
    assert builtins._forecast_aspect_place("迈阿密下周（2026-09-21 至 2026-09-27）逐日天气预报") == "迈阿密"
    assert builtins._forecast_aspect_place("其他显著天气（如雷暴、大风）") is None
    assert builtins._forecast_aspect_place("黄埔（广州）2026-09-18天气") == "黄埔广州"


def test_forecast_excerpt_keeps_requested_dated_periods_after_navigation():
    content = "Example Forecast\n网页正文摘录：\n" + "Navigation menu\n" * 150 + "Forecast issued: 2026-09-17\n2026-09-18T06:00:00 | Friday | High: 91\n2026-09-21T06:00:00 | Monday | High: 88 | precipitation probability: 80%\n2026-09-22T06:00:00 | Tuesday | High: 89"
    selected = builtins._forecast_evidence_excerpt(content, "2026-09-21 至 2026-09-27", 450)
    assert "Monday | High: 88" in selected
    assert "Tuesday | High: 89" in selected
    assert "Friday | High: 91" not in selected


def test_single_historical_date_uses_dated_records_not_unrelated_weather_widgets():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    def item(url, content):
        return base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB", "locator": url,
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()})
    record = item("https://weather.example/history", "Daily observations\n网页正文摘录：\n9/11/2026\n88 °F / 75 °F")
    widget = item("https://association.example/", "Weather widget\n网页正文摘录：\n33℃ / 25℃\nAssociation news")
    contract = _answer_contract("FACT").model_copy(update={"freshness": "HISTORICAL", "required_aspects": ("2026-09-11 天气",)})
    selected = builtins._analysis_evidence_payload((widget, record), "web-research", "历史天气", contract)
    assert selected[0]["evidence_id"] == str(record.evidence_id)
    assert {row["evidence_id"] for row in selected} == {str(record.evidence_id), str(widget.evidence_id)}
    assert builtins._analysis_evidence_payload((widget, record), "web-research", "history", contract, web_source_limit=1)[0]["evidence_id"] == str(record.evidence_id)
    required = contract.model_copy(update={"minimum_independent_sources": 2})
    assert len(builtins._analysis_evidence_payload((widget, record), "web-research", "历史天气", required)) == 2


def test_short_forecast_context_excludes_today_when_next_week_requested():
    content = "Official forecast\n网页正文摘录：\n2026-09-17T06:00:00 | Today | High: 30°C\n2026-09-21T06:00:00 | Monday | High: 31°C"
    excerpt = builtins._forecast_evidence_excerpt(content, "2026-09-21 至 2026-09-27", 4500)
    assert "Monday | High: 31°C" in excerpt
    assert "Today | High: 30°C" not in excerpt


async def test_coverage_timeout_preserves_already_retrieved_results(monkeypatch):
    query = "Example archive record"
    source = _result(url="https://archive.example/record", title="Archive record", body="Retrieved archival facts.")
    ScriptedWebProvider.configure({query: (source,)})
    async def context(*args):
        return {"resolved_request": query, "search_queries": [query]}
    async def timeout(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", timeout)
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(query))
    assert result.outcome == "SUCCEEDED"
    assert source.url in str(result.output)


def test_missing_coverage_dates_are_request_scope_not_invented_measurements():
    query = "2026-09-21 至 2026-09-27 天气"
    value = "2026-09-24至9月27日暂无预报。2026-09-22最高31°C。"
    result = builtins._answer_facts_without_scope_dates(value, query)
    assert "2026-09-24" not in result and "9月27日" not in result
    assert "2026-09-22最高31°C" in result
    bad = builtins._answer_facts_without_scope_dates("2026-10-01暂无数据，温度99°C。", query)
    assert "2026-10-01" in bad and "99°C" in bad


def test_answer_repair_preserves_feedback_without_claiming_missing_evidence():
    from enterprise_agent.contracts.domain import FailureCode
    for critique in ("Translate the product comparison", "Correct the inventory row", "Use the supplied document section"):
        error = builtins._answer_repair_error(critique, ("unanswered portion",))
        assert error.code is FailureCode.VALIDATION_FAILED
        assert error.detail.startswith("ANSWER_REPAIR_REQUIRED:")
        assert critique in str(error)
        assert "EVIDENCE_INSUFFICIENT" not in str(error)



def test_unit_spacing_is_equivalent_but_measurements_stay_distinct():
    assert builtins._normalized_fact_text("67 % 31 °C 88 °F") == builtins._normalized_fact_text("67% 31°C 88°F")
    assert builtins._normalized_fact_text("67 %") != builtins._normalized_fact_text("68%")
    assert builtins._normalized_fact_text("31 °C") != builtins._normalized_fact_text("31 °F")


def test_forecast_prefers_aligned_requested_rows_over_same_source_validity_header():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    def item(url, content):
        return base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB", "locator": url,
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()})
    html = item("https://forecast.weather.gov/MapClick.php?lat=20", "Forecast\n网页正文摘录：\nForecast valid through 2026-09-23\nToday High: 88")
    rows = item("https://forecast.weather.gov/MapClick.php?lat=20&FcstType=json&unit=1", "Forecast\n网页正文摘录：\n2026-09-21T06:00:00 | Monday | High: 31°C")
    contract = _answer_contract("FORECAST").model_copy(update={"required_aspects": ("2026-09-21 至 2026-09-27天气",)})
    payload = builtins._analysis_evidence_payload((html, rows), "web-research", "天气", contract)
    assert [row["evidence_id"] for row in payload] == [str(rows.evidence_id)]
    strict = contract.model_copy(update={"minimum_evidence_items": 2})
    assert len(builtins._analysis_evidence_payload((html, rows), "web-research", "天气", strict)) == 2


@pytest.mark.parametrize("note", ["9月24日至27日超出预报范围。", "24–27日暂无可靠数据。", "9月24日至9月27日尚未获取可靠预报。"])
def test_missing_forecast_coverage_accepts_compact_chinese_date_ranges(note):
    normalized = builtins._answer_facts_without_scope_dates(note, "2026-09-21 至 2026-09-27天气")
    assert "24" not in normalized and "27" not in normalized


def test_single_day_excerpt_never_includes_monthly_summary_even_if_page_fits():
    content = "Weather history\n网页正文摘录：\nMonthly Wind Max 13 Min 4\nDaily Observations\nDate\nWind (mph)\n9/11/2026\n88 °F / 75 °F\n7 mph\n0 in\n9/12/2026\n12 mph"
    selected = builtins._focused_web_excerpt(content, "2026-09-11天气", 4500)
    assert "7 mph" in selected and "88 °F" in selected
    assert "Max 13" not in selected and "Min 4" not in selected
    assert "12 mph" not in selected


def test_partial_answer_distinguishes_unpublished_scope_from_searchable_gaps() -> None:
    assessment = builtins.AnswerQualityAssessment(
        decision="PARTIAL_ACCEPT", acceptable=True, answered_core_request=True,
        language_matches=True, appropriate_depth=True, supported_claim_indexes=(1,),
        inherent_limitations=("Remaining requested dates exceed the published forecast horizon.",),
    )
    builtins._require_quality_replan(assessment)
    assert builtins._answer_quality_decision(assessment) == "PARTIAL_ACCEPT"
    searchable = assessment.model_copy(update={"missing_aspects": ("Requested published record absent",)})
    with pytest.raises(builtins.DomainError) as error:
        builtins._require_quality_replan(searchable)
    assert error.value.code == builtins.FailureCode.VALIDATION_FAILED
    assert error.value.detail == (
        'EVIDENCE_INSUFFICIENT: {"missing_aspects": ["Requested published record absent"], "critique": ""}'
    )


def test_focused_excerpt_keeps_short_record_label_and_value() -> None:
    body = "Navigation item\n" * 80 + "Owner\nAda Lovelace\nStatus\nActive\n" + "Unrelated footer\n" * 80
    excerpt = builtins._focused_web_excerpt("Record\n网页正文摘录：\n" + body, "record owner", 400)
    assert "Owner\nAda Lovelace" in excerpt
    assert len(excerpt) <= 400


def test_searchable_gap_replans_even_when_model_mislabels_it_as_revision() -> None:
    assessment = builtins.AnswerQualityAssessment(
        decision="REVISE", acceptable=False, answered_core_request=False,
        language_matches=True, appropriate_depth=True,
        missing_aspects=("Requested record absent from supplied sources",),
    )
    with pytest.raises(builtins.DomainError) as error:
        builtins._require_quality_replan(assessment)
    assert error.value.code == builtins.FailureCode.VALIDATION_FAILED
    assert error.value.detail == (
        'EVIDENCE_INSUFFICIENT: {"missing_aspects": ["Requested record absent from supplied sources"], "critique": ""}'
    )


def test_web_context_keeps_distinct_reports_after_publisher_minimum(monkeypatch) -> None:
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    reports = []
    for index, host in enumerate(("reports.example", "reports.example", "independent.example", "reports.example", "other.example", "last.example")):
        content = f"Report {index}\n网页正文摘录：\n" + f"Distinct fact {index}. " * 30
        reports.append(base.model_copy(update={
            "evidence_id": uuid4(), "source_type": "WEB", "locator": f"https://{host}/{index}",
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }))
    # Rank all candidates equally so this specifically exercises publisher allocation.
    reports.sort(key=lambda item: (item.retrieved_at, item.evidence_id))
    for index, item in enumerate(reports):
        host = ("reports.example", "reports.example", "independent.example", "reports.example", "other.example", "last.example")[index]
        reports[index] = item.model_copy(update={"locator": f"https://{host}/{index}"})
    contract = _answer_contract("SUMMARY").model_copy(update={"minimum_independent_sources": 2})
    payload = builtins._analysis_evidence_payload(tuple(reports), "web-research", "", contract)
    locators = [item["locator"] for item in payload]
    assert "https://independent.example/2" in locators
    assert all(f"https://reports.example/{index}" in locators for index in (0, 1, 3))
    assert len(payload) == 5


def test_incremental_draft_retains_only_supported_claims_with_unchanged_sources() -> None:
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    content = "Record\n网页正文摘录：\nThe inventory contains 31 items."
    source = base.model_copy(update={"source_type": "WEB", "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest()})
    claim = builtins.GroundedAnswerClaim(content="There are 31 items.", supports=({
        "evidence_id": source.evidence_id, "quote": "The inventory contains 31 items."},))
    candidate = builtins.WebStructuredAnswer(answer="There are 31 items and 8 locations.",
        answer_status="ANSWERED", evidence_ids=(source.evidence_id,),
        claims=(claim, claim.model_copy(update={"content": "There are 8 locations."})))
    quality = builtins.AnswerQualityAssessment(decision="REPLAN", acceptable=False,
        answered_core_request=False, language_matches=True, appropriate_depth=True,
        supported_claim_indexes=(1, 2), unsupported_claim_indexes=(2,), missing_aspects=("Location count",))
    draft = builtins._supported_answer_draft(candidate, quality, (source,))
    assert builtins._restore_answer_draft(draft, (source,)) == (claim,)
    assert builtins._restore_answer_draft(draft, ()) == ()
    changed = source.model_copy(update={"content": content + " changed"})
    assert builtins._restore_answer_draft(draft, (changed,)) == ()
    changed = changed.model_copy(update={"content_hash": hashlib.sha256(changed.content.encode()).hexdigest()})
    assert builtins._restore_answer_draft(draft, (changed,)) == ()


async def test_recovery_queries_share_final_batch_with_unsearched_aspects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = tuple(f"Orion status variant {index}" for index in range(5))
    recovery = "Orion missing maintenance interval official record"
    retained = _result(url="https://orion.example/status", title="Orion status",
                       body="Orion is operating; maintenance interval is not listed.")
    recovered = _result(url="https://orion.example/maintenance", title="Orion maintenance",
                        body="Orion maintenance interval is published in this record.")
    plan = BoundedResearchPlan(
        web_queries=queries, retrieval_queries=(),
        source_plan=SourcePlan(required_sources=("PUBLIC_WEB",), forbidden_sources=("PARAMETRIC_KNOWLEDGE",)), result_limit=4,
        answer_contract=_answer_contract("EXPLANATION").model_copy(
            update={"required_aspects": ("operating status", "maintenance interval")}
        ),
    )
    ScriptedWebProvider.configure({queries[0]: (retained,), recovery: (recovered,)})

    async def context(*_args: object) -> dict[str, object]:
        return {"resolved_request": "Orion operating status and maintenance interval",
                "research_plan": plan.model_dump(mode="json")}

    async def assess(*_args: object, **_kwargs: object) -> builtins.WebSearchAssessment:
        return builtins.WebSearchAssessment(
            sufficient=False, missing_aspects=("maintenance interval",),
            additional_queries=(recovery,),
        )

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)
    result = await builtins._web_search_handler(
        cast(async_sessionmaker[AsyncSession], object()), _request(queries[0]),
    )
    assert recovery in ScriptedWebProvider.calls
    assert queries[1] in ScriptedWebProvider.calls
    assert len(ScriptedWebProvider.calls) <= 5
    output = cast(dict[str, object], result.output)
    results = cast(list[dict[str, object]], output["results"])
    assert {item["url"] for item in results} == {retained.url, recovered.url}


def test_short_table_cells_count_as_substantive_source_content():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    content = "Specifications\n" + "\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a\n" + "Model\nMemory\nAlpha\n16 GB\nBeta\n32 GB"
    item = base.model_copy(update={"source_type": "WEB", "content": content})
    assert builtins._web_evidence_substantive_characters(item) >= len("ModelMemoryAlpha16 GBBeta32 GB")


def test_forecast_source_packing_does_not_drop_alternate_date_notation():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    sources = []
    for url, content in (
        ("https://records.example/district", "District forecast\n\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a\n09/18\nClear\n24 C\n09/19\nRain\n22 C"),
        ("https://city.example/region", "Region forecast\n\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a\n2026-09-18: Clear, 26 C"),
    ):
        sources.append(base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB", "locator": url,
            "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()}))
    contract = _answer_contract("FORECAST").model_copy(update={"required_aspects": ("District forecast 2026-09-18 to 2026-09-19",)})
    packed = builtins._analysis_evidence_payload(tuple(sources), "web-research", "District forecast", contract)
    assert {item["locator"] for item in packed} == {item.locator for item in sources}


def test_semantic_review_keeps_reply_and_claim_order_without_replaying_quotes():
    import json
    source_id = uuid4()
    quote = "The original source contains a long independently verified fact. " * 8
    claims = tuple(builtins.GroundedAnswerClaim(content=f"Fact {index}", supports=({
        "evidence_id": source_id, "quote": quote},)) for index in range(2))
    candidate = builtins.WebStructuredAnswer(answer="Full answer including a limitation.",
        answer_status="ANSWERED", evidence_ids=(source_id,), claims=claims)
    payload = builtins._semantic_review_candidate(candidate)
    assert payload["answer"] == candidate.answer
    assert payload["claims"] == [{"content": f"Fact {index}", "evidence_ids": [str(source_id)]}
                                 for index in range(2)]
    assert candidate.claims[0].supports[0].quote == quote
    assert len(json.dumps(payload)) < len(candidate.model_dump_json()) / 2


async def test_recovery_skips_previously_collected_direct_url_but_runs_new_query(monkeypatch):
    old_url = "https://orion.example/"
    query = "Orion missing inventory"
    followup = "Orion inventory member list"
    record = _result(url="https://orion.example/inventory", title="Orion inventory",
                     body="The inventory contains Alpha and Beta.")
    ScriptedWebProvider.configure({followup: (record,)})

    async def context(*_args):
        return {"resolved_request": query, "recovery_query": query,
                "previously_collected_urls": [old_url]}

    async def assess(*_args, **_kwargs):
        return builtins.WebSearchAssessment(sufficient=False, missing_aspects=("inventory",),
            additional_queries=(followup,), candidate_urls=(old_url,))

    monkeypatch.setattr(builtins, "HttpWebProvider", ScriptedWebProvider)
    monkeypatch.setattr(builtins, "load_web_provider_config", lambda: object())
    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "_assess_web_search", assess)
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(query))
    assert old_url not in ScriptedWebProvider.fetch_calls
    assert followup in ScriptedWebProvider.calls
    assert any(item["url"] == record.url for item in result.output["results"])


def test_quote_repair_never_drops_short_cells_between_source_lines():
    from tests.unit.test_reviewer import review_fixture
    _, _, source = review_fixture()
    body = "Operational status for Alpha\nOK\nOperational status for Beta"
    source = source.model_copy(update={"content": body})
    repaired = builtins._best_verbatim_quote("Operational status for Alpha and Beta",
        "Operational status for Alpha\nOperational status for Beta", source)
    assert repaired is not None
    assert builtins._normalized_support_text(repaired) in builtins._normalized_support_text(body)
    assert "OK" in repaired


def test_historical_packing_retains_target_entity_beside_other_entity_iso_dates():
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    def evidence(url, body):
        return base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB", "locator": url,
            "content": body, "content_hash": hashlib.sha256(body.encode()).hexdigest()})
    target = evidence("https://record.example/alpha", "Alpha record: September 7 through 13, 2026. Output was 12 units.")
    adjacent = evidence("https://archive.example/beta", "Beta record: 2026-09-07 through 2026-09-13. Output was 40 units.")
    contract = _answer_contract("SUMMARY").model_copy(update={"freshness": "HISTORICAL",
        "required_aspects": ("Alpha output 2026-09-07 through 2026-09-13",)})
    payload = builtins._analysis_evidence_payload((target, adjacent), "web-research", "Alpha output", contract)
    assert {x["evidence_id"] for x in payload} == {str(target.evidence_id), str(adjacent.evidence_id)}
    assert next(x["content"] for x in payload if x["evidence_id"] == str(target.evidence_id)) == target.content
