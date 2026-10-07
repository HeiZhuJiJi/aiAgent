import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import apps.web_provider.main as web_provider
from apps.web_provider.main import (
    SearchBackendUnavailable,
    SearchRequest,
    SearchResult,
    build_query_variants,
    extract_visible_text,
    fetch_public_page,
    normalize_results,
    rank_diverse_results,
    rank_results,
    relevant_excerpt,
    search_searxng,
    source_corroboration_sufficient,
    temporal_compatible,
    viable_results,
)


def test_normalize_results_filters_invalid_and_duplicate_urls() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Guangzhou weather",
                    "url": "https://weather.example/guangzhou",
                    "content": "Live weather from a public source.",
                },
                {
                    "title": "Duplicate",
                    "url": "https://weather.example/guangzhou",
                    "content": "duplicate",
                },
                {"title": "Unsafe", "url": "file:///etc/passwd", "content": "unsafe"},
                {"title": "", "url": "https://example.com/empty", "content": ""},
            ]
        },
        10,
    )

    assert len(results) == 1
    assert results[0].url == "https://weather.example/guangzhou"
    assert results[0].content_hash == hashlib.sha256(results[0].snippet.encode()).hexdigest()


def test_search_request_uses_a_bounded_default_result_limit() -> None:
    assert SearchRequest(query="current public information").limit == 5


def test_opened_page_date_inference_uses_article_header_not_late_recommendations() -> None:
    result = SearchResult(
        title="Acme update",
        url="https://news.example/article/42",
        snippet="Acme update",
        content_hash="source",
        observed_at="2026-09-14T00:00:00+00:00",
    )
    page = (
        "Acme update\nPublished 2026-05-06\nArticle body. "
        + "x" * 2200
        + " Recommended on 2026-09-14."
    )

    assert web_provider._inferred_page_date(result, page) == "2026-05-06"


async def test_news_vertical_parses_dates_and_resolves_original_article_urls() -> None:
    rss = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss><channel><item>
      <title>Acme released Atlas</title>
      <link>https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fwww.reuters.com%2Fworld%2Facme-atlas</link>
      <pubDate>Sun, 13 Sep 2026 11:05:00 GMT</pubDate>
      <description>Acme released Atlas after an independent review.</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.bing.com"
        assert request.url.params["format"] == "rss"
        assert request.url.params["q"] == "acme"
        return httpx.Response(200, content=rss)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await web_provider._search_news_rss("Acme latest news", client)

    assert len(results) == 1
    assert results[0].url == "https://www.reuters.com/world/acme-atlas"
    assert results[0].published_at == "2026-09-13"
    assert "Acme released Atlas after an independent review." in results[0].snippet


def test_current_month_query_rejects_articles_identified_as_an_older_month() -> None:
    older = SearchResult(
        title="特朗普最新动态",
        url="https://example.com/article/20260704-trump-update",
        snippet="特朗普相关报道。",
        content_hash="older",
        published_at="2026-07-04",
        observed_at="2026-09-14T00:00:00+00:00",
    )
    requested_month = SearchResult(
        title="特朗普9月12日最新动态",
        url="https://example.com/article/20260912-trump-update",
        snippet="特朗普相关报道。",
        content_hash="current",
        published_at="2026-09-12",
        observed_at="2026-09-14T00:00:00+00:00",
    )

    query = "Trump latest update 2026-09"

    assert not temporal_compatible(query, older)
    assert temporal_compatible(query, requested_month)


def test_normalize_results_caps_untrusted_search_text() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "T" * 900,
                    "url": "https://example.com/long",
                    "content": "C" * 9000,
                }
            ]
        },
        1,
    )

    assert len(results[0].title) == 500
    assert len(results[0].snippet) < 1900
    assert "来源发布日期：未提供" in results[0].snippet
    assert "检索时间：" in results[0].snippet


async def test_search_searxng_uses_json_api_and_enforces_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/essearch/api/autocomplete":
            assert request.url.host == "www.nmc.cn"
            assert request.url.params["q"] == "广州"
            return httpx.Response(200, json={"code": 0, "data": []})
        assert request.url.path == "/search"
        assert request.url.params["q"] == "广州天气"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"广州天气 Result {index}",
                        "url": f"https://result{index}.gov.cn/item",
                        "content": f"Snippet {index}",
                    }
                    for index in range(3)
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="广州天气", limit=2),
            client,
            "http://searxng:8080",
        )

    assert [item.title for item in results] == [
        "广州天气 Result 0",
        "广州天气 Result 1",
    ]


async def test_search_collapses_high_confidence_cms_aliases_for_one_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Acme 2026 年度报告",
                        "url": "https://reports.gov.cn/news/mpost_4705510.html",
                        "content": "Acme 2026 年度报告披露核心指标。",
                    },
                    {
                        "title": "Acme 发布 2026 年度报告",
                        "url": "https://reports.gov.cn/open/4/4705/mpost_4705510.html",
                        "content": "Acme 2026 年度报告披露核心指标。",
                    },
                    {
                        "title": "Acme 2026 年度报告解读",
                        "url": "https://analysis.gov.cn/report/acme-2026",
                        "content": "Acme 2026 年度报告的独立公开解读。",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="Acme 2026 年度报告", limit=3),
            client,
            "http://searxng:8080",
        )

    urls = [item.url for item in results]
    assert len(urls) == 2
    assert sum("mpost_4705510.html" in url for url in urls) == 1


async def test_search_searxng_returns_auditable_empty_result_without_retrying() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="unavailable current information 2026-09-12", limit=3),
            client,
            "http://searxng:8080",
        )

    assert results == []


async def test_current_events_use_news_vertical_when_searxng_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    async def news_results(
        _query: str,
        _client: httpx.AsyncClient,
        *,
        limit: int = 12,
    ) -> list[SearchResult]:
        assert limit == 12
        return [
            SearchResult(
                title="Acme announces a current public update",
                url="https://www.bbc.com/news/acme-current-update",
                snippet=(
                    "Acme announces a current public update\n"
                    "来源发布日期：2026-09-13\n"
                    "Acme announced a material public update after an independent review."
                ),
                published_at="2026-09-13",
                observed_at="2026-09-14T00:00:00+00:00",
                content_hash="news-rss-result",
            )
        ]

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    monkeypatch.setattr(web_provider, "_search_news_rss", news_results)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    request = SearchRequest(query="Acme latest news", limit=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            request,
            client,
            "http://news-fallback-test:8080",
        )

    assert [item.url for item in results] == ["https://www.bbc.com/news/acme-current-update"]


async def test_current_officeholder_uses_news_vertical_only_when_search_has_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def reject_page_fetches(_url: str) -> bool:
        return False

    async def news_results(
        query: str,
        _client: httpx.AsyncClient,
        *,
        limit: int = 12,
    ) -> list[SearchResult]:
        calls.append(query)
        assert limit == 12
        return [
            SearchResult(
                title="Current prime minister of Japan confirmed in public report",
                url="https://www.bbc.com/news/japan-government-current",
                snippet=(
                    "Japan current prime minister public report\n"
                    "来源发布日期：2026-09-15\n"
                    "The current prime minister of Japan was confirmed by the government."
                ),
                published_at="2026-09-15",
                observed_at="2026-09-16T00:00:00+00:00",
                content_hash="officeholder-news-result",
            )
        ]

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    monkeypatch.setattr(web_provider, "_search_news_rss", news_results)

    def empty_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(empty_handler)) as client:
        results = await search_searxng(
            SearchRequest(query="current prime minister of Japan", limit=2),
            client,
            "http://officeholder-fallback-test:8080",
        )

    assert calls == ["current prime minister of Japan"]
    assert [item.url for item in results] == ["https://www.bbc.com/news/japan-government-current"]


def test_current_officeholder_recognizes_planner_current_year_query() -> None:
    current_year = datetime.now(UTC).year
    assert web_provider._requests_current_officeholder(f"Japan prime minister {current_year}")
    assert not web_provider._requests_current_officeholder(
        f"former Japan prime minister {current_year}"
    )
    assert web_provider._entity_terms(f"日本首相是谁 {current_year}") == ("日本",)
    assert web_provider._entity_terms(f"澳大利亚最高领导人是谁 {current_year}") == ("澳大利亚",)
    assert web_provider._news_subject_query(f"日本首相是谁 {current_year}") == ("日本首相")


def test_current_officeholder_source_hints_are_role_pages_without_fixed_names() -> None:
    japan = web_provider._official_role_source_hints("2026年 日本天皇 现任 天皇陛下 官方")
    australia = web_provider._official_role_source_hints(
        "2026年 澳大利亚 国家元首 总督 总理 现任 官方"
    )
    assert [url for _title, url in japan] == [
        "https://www.kunaicho.go.jp/en/learn/about/history/history01.html"
    ]
    assert [url for _title, url in australia] == [
        "https://www.pm.gov.au/about-prime-minister",
        "https://www.gg.gov.au/about-governor-general/civics-and-democracy",
    ]
    assert web_provider._official_role_source_hints("former Japan prime minister 2024") == ()


async def test_current_officeholder_opens_first_party_biography_when_search_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public_page(_url: str) -> bool:
        return True

    monkeypatch.setattr(web_provider, "_public_destination", public_page)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search-test":
            return httpx.Response(200, json={"results": []})
        if request.url.host == "www.kunaicho.go.jp":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<main><h1>His Majesty the Emperor Naruhito</h1><p>Accession 2019</p></main>",
            )
        if request.url.host == "www.bing.com":
            return httpx.Response(200, text="<rss><channel></channel></rss>")
        raise AssertionError(f"unexpected URL: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="2026年 日本天皇 现任 天皇陛下 官方", limit=3),
            client,
            "http://search-test",
        )
    assert len(results) == 1
    assert results[0].url == "https://www.kunaicho.go.jp/en/learn/about/history/history01.html"
    assert "Emperor Naruhito" in results[0].snippet
    assert "网页正文摘录" in results[0].snippet


def test_current_official_role_page_is_not_rejected_for_accession_year() -> None:
    page = web_provider.SearchResult(
        title="日本天皇（宫内厅官方人物资料）",
        url="https://www.kunaicho.go.jp/en/learn/about/history/history01.html",
        snippet="网页正文摘录：His Majesty the Emperor Naruhito. Accession 2019.",
        observed_at="2026-09-16T00:00:00+00:00",
        content_hash="a" * 64,
    )
    assert web_provider.temporal_compatible("2026 日本天皇", page)
    assert web_provider.viable_results("2026 日本天皇", [page]) == [page]
    assert not web_provider.temporal_compatible("2024 日本天皇", page)


def test_official_australian_role_page_survives_mistaken_president_title() -> None:
    page = web_provider.SearchResult(
        title="澳大利亚国家元首与总督（官方公民制度资料）",
        url="https://www.gg.gov.au/about-governor-general/civics-and-democracy",
        snippet=(
            "网页正文摘录：The Governor-General is His Majesty the King's representative "
            "and Australia's Head of State."
        ),
        observed_at="2026-09-16T00:00:00+00:00",
        content_hash="b" * 64,
    )
    assert web_provider.viable_results("2026 现任澳大利亚总统", [page]) == [page]
    assert web_provider._primary_source("2026 现任澳大利亚总统", page)


async def test_empty_search_results_do_not_poison_the_short_lived_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    query = "TransientEntity current"
    calls = 0
    first_request_calls = len(web_provider._selected_search_queries(query))

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= first_request_calls:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "TransientEntity current status",
                        "url": "https://transiententity.com/status",
                        "content": "TransientEntity current public status.",
                    }
                ]
            },
        )

    request = SearchRequest(query=query, limit=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await search_searxng(request, client, "http://empty-cache-test:8080")
        second = await search_searxng(request, client, "http://empty-cache-test:8080")

    assert first == []
    assert len(second) == 1
    assert calls > first_request_calls


async def test_search_uses_other_query_variants_when_one_backend_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        seen_queries.append(query)
        if query == "广州 2026-09-12":
            return httpx.Response(503)
        if query == "广州":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "广州 2026-09-12 官方信息",
                            "url": "https://guangzhou.gov.cn/current",
                            "content": "广州当天公开信息。",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="广州 2026-09-12", limit=3),
            client,
            "http://searxng:8080",
        )

    assert seen_queries[0] == "广州 2026-09-12"
    assert "广州" in seen_queries
    assert len(seen_queries) <= 3
    assert results[0].url == "https://guangzhou.gov.cn/current"


def test_normalize_results_rejects_invalid_backend_payload() -> None:
    with pytest.raises(SearchBackendUnavailable):
        normalize_results({"unexpected": []}, 10)


def test_date_query_variants_preserve_exact_and_localized_dates() -> None:
    assert build_query_variants("广州白云区 天气预报 2026-09-12 至 2026-09-18") == (
        "广州白云区 天气预报 2026-09-12 至 2026-09-18",
        "广州白云区 天气预报 2026-09-12 至 2026-09-18 官方",
        "广州白云区 天气预报 2026年9月12日 至 2026年9月18日",
        "广州白云区 天气预报",
    )


def test_selected_queries_keep_the_localized_exact_date_before_date_free_fallbacks() -> None:
    selected = web_provider._selected_search_queries("东莞 天气 2026-09-11")

    assert selected == (
        "东莞 天气 2026-09-11",
        "东莞 天气 2026年9月11日",
        "东莞天气",
    )


def test_query_understanding_keeps_descriptors_and_dates_out_of_hard_constraints() -> None:
    query = "2026年9月14日 广州市 黄埔区 今日天气 实况 预报"

    assert web_provider._entity_terms(query) == ("广州市", "黄埔区")
    assert web_provider._topic_terms(query) == ()


def test_viable_results_accept_administrative_suffix_aliases() -> None:
    observed_at = datetime.now(UTC).isoformat()
    result = SearchResult(
        title="广州黄埔天气预报",
        url="https://example.test/forecast",
        snippet="2026年9月14日，广州黄埔多云，26℃至32℃。",
        observed_at=observed_at,
        content_hash="hash",
    )

    assert viable_results(
        "2026年9月14日 广州市 黄埔区 今日天气 实况 预报",
        [result],
    ) == [result]


def test_authoritative_specialist_is_opened_before_full_entity_validation() -> None:
    today = datetime.now(UTC).date()
    result = SearchResult(
        title="黄埔天气预报",
        url="https://www.weather.com.cn/weather/101280111.shtml",
        snippet="黄埔7天天气预报。",
        observed_at=datetime.now(UTC).isoformat(),
        content_hash="hash",
    )
    query = f"{today.year}年{today.month}月{today.day}日 广州市 黄埔区 今日天气预报"

    assert viable_results(query, [result]) == []
    assert viable_results(
        query,
        [result],
        allow_partial_authoritative_entities=True,
    ) == [result]


def test_opened_authoritative_specialist_must_match_every_entity() -> None:
    result = SearchResult(
        title="黄埔天气预报",
        url="https://www.weather.com.cn/weather/101280111.shtml",
        snippet="网页正文摘录：\n全国 > 广东 > 广州 > 黄埔\n14日 雷阵雨 33/25℃",
        observed_at=datetime.now(UTC).isoformat(),
        content_hash="hash",
    )

    assert viable_results(
        "2026年9月14日 广州市 黄埔区 今日天气预报",
        [result],
    ) == [result]


def test_interval_coverage_identifies_missing_boundaries_without_affecting_latest_windows() -> None:
    partial = normalize_results(
        {
            "results": [
                {
                    "title": "Acme status 2026-09-13",
                    "url": "https://acme.com/status",
                    "content": "Acme status for 2026-09-13.",
                }
            ]
        },
        1,
    )

    missing = web_provider._missing_temporal_boundaries(
        "Acme status 2026-09-13 至 2026-09-19",
        partial,
    )
    assert [item.isoformat() for item in missing] == ["2026-09-19"]
    assert web_provider._focused_temporal_queries(
        "Acme status 2026-09-13 至 2026-09-19",
        missing,
    ) == ("Acme status 2026年9月19日",)
    assert not web_provider._missing_temporal_boundaries(
        "Acme 最新动态 2026-08-15 至 2026-09-13",
        partial,
    )


def test_english_current_information_query_gets_a_localized_intent_variant() -> None:
    variants = build_query_variants("DeepSeek latest chat model release as of 2026-09-13 official")

    assert "DeepSeek 最新 chat model 发布 截至 2026-09-13 官方" in variants


def test_document_query_variant_prefers_entity_year_and_document_title() -> None:
    selected = web_provider._selected_search_queries(
        "请说明2024年广东省全年平均年降水量，并使用官方水资源公报"
    )

    assert "2024年 广东省 水资源公报" in selected


def test_localized_compact_variant_is_preferred_for_official_current_query() -> None:
    selected = web_provider._selected_search_queries(
        "DeepSeek latest chat model release as of 2026-09-13 official"
    )

    assert selected[1] == "DeepSeek chat model 发布 官方"


def test_latest_query_prefers_highest_relevant_semantic_version() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Python Release Python 3.9.6",
                    "url": "https://www.python.org/downloads/release/python-396/",
                    "content": "Python stable release.",
                },
                {
                    "title": "Python 3.14.7 documentation",
                    "url": "https://docs.python.org/3/",
                    "content": "Python stable documentation.",
                },
            ]
        },
        10,
    )

    assert rank_results("Python current stable release", results, 2)[0].title == (
        "Python 3.14.7 documentation"
    )


def test_current_query_prefers_the_authoritative_release_hub_over_old_release_page() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Python Release Python 3.9.6",
                    "url": "https://www.python.org/downloads/release/python-396/",
                    "content": "Python stable release.",
                },
                {
                    "title": "Download Python",
                    "url": "https://www.python.org/downloads/",
                    "content": "Download the latest stable Python release.",
                },
            ]
        },
        10,
    )

    assert rank_results("Python current stable release", results, 2)[0].url == (
        "https://www.python.org/downloads/"
    )


def test_comparison_query_variants_focus_each_requested_document_type() -> None:
    variants = build_query_variants("2024年广东省 年平均降水量 水资源公报 气候公报 统计口径")

    assert "2024年广东省 年平均降水量 水资源公报" in variants
    assert "2024年广东省 年平均降水量 气候公报" in variants


def test_comparison_instructions_are_not_treated_as_entities() -> None:
    query = "2024年 广东省 年平均降水量 气候公报 水资源公报 统计口径"

    assert web_provider._entity_terms(query) == ("广东省",)


def test_current_action_words_are_not_misclassified_as_entities() -> None:
    assert web_provider._entity_terms("OpenAI 最新发布 公告 官方信息 2026-09-13") == ("openai",)
    assert "OpenAI" in build_query_variants("OpenAI 最新发布 公告 官方信息 2026-09-13")


@pytest.mark.parametrize(
    ("question", "expected_terms"),
    [
        ("OpenAI最近发布了什么产品", ("OpenAI", "产品")),
        ("广州市人工智能产业最新政策", ("广州市", "人工智能", "政策")),
        ("中国新能源汽车2025年出口数据", ("中国", "2025年", "新能源汽车", "数据")),
    ],
)
def test_natural_language_queries_gain_compact_generic_variants(
    question: str,
    expected_terms: tuple[str, ...],
) -> None:
    variants = build_query_variants(question)

    assert any(
        all(term.casefold() in variant.casefold() for term in expected_terms) and " " in variant
        for variant in variants
    )


def test_visible_text_and_excerpt_keep_relevant_facts_without_page_scripts() -> None:
    content = extract_visible_text(
        "<html><script>ignore me</script><body><p>导航</p>"
        "<article><h1>2024 年广东气候公报</h1>"
        "<p>全省年平均降水量为 2133.8 毫米，较常年偏多 19%。</p>"
        "</article></body></html>"
    )

    assert "ignore me" not in content
    assert "2133.8 毫米" in relevant_excerpt("2024 广东 年平均降水量", content)


def test_excerpt_normalizes_localized_date_and_keeps_neighboring_values() -> None:
    content = "广州黄埔天气\n2026年09月12日 星期六\n晴 25 ~ 33℃\n湿度：75%，东北风 2级\n无关导航"

    excerpt = relevant_excerpt(
        "广州黄埔 天气预报 2026-09-12",
        content,
    )

    assert "2026年09月12日" in excerpt
    assert "晴 25 ~ 33℃" in excerpt
    assert "湿度：75%" in excerpt


def test_excerpt_preserves_repeated_values_and_intermediate_interval_rows() -> None:
    content = (
        "Acme 每日指标\n09/13\n状态正常\n值 10\n09/14\n状态正常\n值 10\n"
        "09/15\n状态正常\n值 11\n09/16\n状态正常\n值 12\n09/17\n状态正常\n"
        "值 12\n09/18\n状态正常\n值 13\n09/19\n状态正常\n值 13"
    )

    excerpt = relevant_excerpt(
        "Acme 每日指标 2026-09-13 至 2026-09-19",
        content,
    )

    assert "09/16\n状态正常\n值 12" in excerpt
    assert excerpt.count("状态正常") >= 2


async def test_search_enriches_top_results_with_public_page_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_public(_url: str) -> bool:
        return True

    monkeypatch.setattr(web_provider, "_public_destination", allow_public)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searxng":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "2024 年广东气候公报",
                            "url": "https://facts.gov.cn/report",
                            "content": "广东年度气候统计。",
                        }
                    ]
                },
            )
        assert str(request.url) == "https://facts.gov.cn/report"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><body><article><p>"
                "2024 年广东全省年平均降水量为 2133.8 毫米。"
                "</p></article></body></html>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_searxng(
            SearchRequest(query="2024 广东 年平均降水量 气候公报", limit=1),
            client,
            "http://searxng:8080",
        )

    assert "网页正文摘录" in results[0].snippet
    assert "2133.8 毫米" in results[0].snippet
    assert results[0].content_hash == hashlib.sha256(results[0].snippet.encode()).hexdigest()


async def test_fetch_rejects_private_destinations_before_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("private URL must not be requested")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_public_page(client, "http://127.0.0.1/private") is None


@pytest.mark.parametrize(
    ("query", "matching_title", "other_title"),
    [
        (
            "广州白云区 天气预报 2026-09-12",
            "广州白云区 2026-09-12 天气预报",
            "广州荔湾区年度气候统计",
        ),
        (
            "OpenAI 最新消息 2026-09-12",
            "OpenAI 2026-09-12 最新消息",
            "另一家公司历史新闻",
        ),
        (
            "iPhone 17 Pro 最新数据 2026-09-12",
            "iPhone 17 Pro 2026-09-12 最新价格",
            "旧款 Android 手机价格",
        ),
    ],
)
def test_rank_results_prioritizes_matching_entity_time_and_information_type(
    query: str,
    matching_title: str,
    other_title: str,
) -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": other_title,
                    "url": "https://irrelevant.example/result",
                    "content": "This result concerns a different entity, place, or period.",
                },
                {
                    "title": matching_title,
                    "url": "https://relevant.example/result",
                    "content": f"{matching_title}，包含与请求直接相关的公开信息。",
                },
            ]
        },
        10,
    )

    ranked = rank_results(query, results, 2)

    assert ranked[0].url == "https://relevant.example/result"
    assert matching_title in ranked[0].snippet


def test_rank_results_prefers_entity_primary_source_over_seo_summary() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Acme 2026 latest product guide",
                    "url": "https://best-products.example/acme-2026",
                    "content": "An SEO summary of Acme's latest product.",
                },
                {
                    "title": "Acme product release",
                    "url": "https://acme.com/news/product-release",
                    "content": "Acme's official product release information.",
                },
            ]
        },
        10,
    )

    ranked = rank_results("Acme latest product", results, 2)

    assert ranked[0].url == "https://acme.com/news/product-release"


def test_news_ranking_prefers_established_editorial_source_over_self_media() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "特朗普 9 月 14 日最新动态",
                    "url": "https://www.163.com/dy/article/SELF_MEDIA.html",
                    "content": "特朗普最新动态与相关新闻。",
                    "publishedDate": "2026-09-14",
                },
                {
                    "title": "特朗普称美伊战事将在中期选举后结束",
                    "url": ("https://news.rthk.hk/rthk/ch/component/k2/1869474-20260910.htm"),
                    "content": "香港电台报道特朗普的最新公开表态。",
                    "publishedDate": "2026-09-10",
                },
            ]
        },
        10,
    )

    ranked = rank_results("特朗普 最新动态 2026年9月", results, 2)

    assert "rthk.hk" in ranked[0].url


def test_latest_news_ranking_does_not_let_old_publisher_trust_override_recency() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "特朗普 1 月公开活动",
                    "url": "https://news.rthk.hk/world/20260123.htm",
                    "content": "香港电台报道特朗普一月的活动。",
                    "publishedDate": "2026-01-23",
                },
                {
                    "title": "特朗普 9 月 13 日最新公开表态",
                    "url": "https://current-news.example/20260913.html",
                    "content": "特朗普在九月十三日发表最新公开表态。",
                    "publishedDate": "2026-09-13",
                },
            ]
        },
        10,
    )

    ranked = rank_results("特朗普 最新动态 2026年9月", results, 2)

    assert ranked[0].url == "https://current-news.example/20260913.html"


def test_current_event_discovery_builds_a_topic_specific_verification_query() -> None:
    result = normalize_results(
        {
            "results": [
                {
                    "title": "特朗普称考虑解密更多 9·11 事件档案 - 国际观察",
                    "url": "https://news.example/event",
                    "content": "候选事件摘要。",
                }
            ]
        },
        1,
    )[0]

    assert web_provider._requests_current_events("特朗普最近有什么最新动态")
    assert not web_provider._requests_current_events("特朗普是谁")
    assert web_provider._corroboration_query(result) == ("特朗普称考虑解密更多 9·11 事件档案")


def test_current_event_hub_yields_concrete_verification_queries() -> None:
    result = SearchResult(
        title="Acme latest news - 24 hour rolling updates",
        url="https://news.example/tags/acme",
        snippet=(
            "Acme latest news\n\n网页正文摘录：\n"
            "Acme announced Atlas on 2026-09-14\n"
            "Acme signed a supply agreement yesterday\n"
            "加载更多"
        ),
        observed_at=datetime.now(UTC).isoformat(),
        content_hash="hash",
    )

    queries = web_provider._event_verification_queries(
        "Acme latest news",
        [result],
    )

    assert queries == (
        "Acme announced Atlas on 2026-09-14",
        "Acme signed a supply agreement yesterday",
    )


def test_current_news_ranking_uses_hubs_for_discovery_not_as_best_final_source() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Acme latest news - 24 hour rolling updates",
                    "url": "https://news.example/tags/acme",
                    "content": "Acme latest event reports.",
                    "publishedDate": "2026-09-14",
                },
                {
                    "title": "Acme announced Atlas",
                    "url": "https://news.example/2026/09/14/acme-atlas",
                    "content": "Acme announced Atlas in a public event.",
                    "publishedDate": "2026-09-14",
                },
            ]
        },
        10,
    )

    assert rank_results("Acme latest news", results, 2)[0].url.endswith("/acme-atlas")


def test_rank_results_prefers_specific_location_over_related_city() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "广州天气预报",
                    "url": "https://example.com/guangzhou",
                    "content": "广州市天气预报。",
                },
                {
                    "title": "白云天气预报",
                    "url": "https://example.com/baiyun",
                    "content": "白云天气预报与气温。",
                },
            ]
        },
        10,
    )

    ranked = rank_results("广州白云区 天气预报", results, 2)

    assert ranked[0].url == "https://example.com/baiyun"


def test_rank_results_treats_year_place_and_metric_as_hard_constraints() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "2023 年深圳市气候公报暨 2024 年趋势预测",
                    "url": "https://weather.sz.gov.cn/report",
                    "content": "深圳年平均降水量统计及广东天气趋势。",
                },
                {
                    "title": "2024 年中国气候公报",
                    "url": "https://www.cma.gov.cn/report",
                    "content": "全国年平均降水量统计。",
                },
                {
                    "title": "2024 年广东省气候公报",
                    "url": "https://gd.cma.gov.cn/report",
                    "content": "广东全省年平均降水量为 2133.8 毫米。",
                },
            ]
        },
        10,
    )

    ranked = rank_results(
        "2024年广东 年降雨量 年平均降水量 气候公报",
        results,
        3,
    )

    assert ranked[0].url == "https://gd.cma.gov.cn/report"


def test_rank_results_penalizes_related_but_wrong_geography() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "2024 年粤港澳大湾区气候监测公报",
                    "url": "https://example.com/gba",
                    "content": "广东省参与编制，年平均降水量为 2208.2 毫米。",
                },
                {
                    "title": "2024 年广东天气气候特征",
                    "url": "https://example.com/guangdong",
                    "content": "广东全省年平均降水量为 2133.8 毫米。",
                },
            ]
        },
        10,
    )

    ranked = rank_results("2024年广东 年平均降水量 气候公报", results, 2)

    assert ranked[0].url == "https://example.com/guangdong"


def test_diverse_ranking_prevents_one_host_from_crowding_out_other_sources() -> None:
    results = normalize_results(
        {
            "results": [
                *[
                    {
                        "title": f"广东 2024 年报告副本 {index}",
                        "url": f"https://one.example/report-{index}",
                        "content": "广东 2024 年平均降水量。",
                    }
                    for index in range(4)
                ],
                {
                    "title": "广东 2024 年独立报告",
                    "url": "https://two.example/report",
                    "content": "广东 2024 年平均降水量。",
                },
            ]
        },
        10,
    )

    ranked = rank_diverse_results("广东 2024 年平均降水量", results, 3)

    assert [item.url for item in ranked] == [
        "https://one.example/report-0",
        "https://one.example/report-1",
        "https://two.example/report",
    ]


def test_source_corroboration_requires_primary_or_two_independent_hosts() -> None:
    one_general = normalize_results(
        {
            "results": [
                {
                    "title": "Acme latest update",
                    "url": "https://news.example.com/acme",
                    "content": "One secondary report.",
                }
            ]
        },
        1,
    )
    primary = [one_general[0].model_copy(update={"url": "https://acme.com/updates/latest"})]
    independent = [
        one_general[0].model_copy(
            update={
                "snippet": one_general[0].snippet
                + "\n\n网页正文摘录：\nAcme latest update from the opened page."
            }
        ),
        one_general[0].model_copy(update={"url": "https://other.example/acme"}),
    ]

    assert not source_corroboration_sufficient("Acme latest update", one_general)
    assert source_corroboration_sufficient("Acme latest update", primary)
    assert source_corroboration_sufficient("Acme latest update", independent)


def test_newsroom_subdomains_are_one_publisher_for_diversity_and_corroboration() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "事件报道一",
                    "url": "https://news.sina.com.cn/world/one",
                    "content": "事件最新报道。\n\n网页正文摘录：\n事件最新报道。",
                },
                {
                    "title": "事件报道二",
                    "url": "https://finance.sina.com.cn/world/two",
                    "content": "事件后续报道。\n\n网页正文摘录：\n事件后续报道。",
                },
                {
                    "title": "独立媒体报道",
                    "url": "https://www.bbc.com/zhongwen/articles/three",
                    "content": "独立媒体报道。\n\n网页正文摘录：\n独立媒体报道。",
                },
            ]
        },
        3,
    )

    ranked = rank_diverse_results("事件 最新新闻", results, 2, max_per_host=1)

    assert {web_provider._publisher_identity(item.url) for item in ranked} == {
        "sina.com.cn",
        "bbc.com",
    }
    assert not source_corroboration_sufficient("事件 最新新闻", results[:2])
    assert source_corroboration_sufficient("事件 最新新闻", [results[0], results[2]])


def test_current_event_words_are_topics_not_required_entities() -> None:
    results = [
        SearchResult(
            title="特朗普最新消息与公开活动",
            url="https://news.example/trump",
            snippet="特朗普今天表示将公布新的安排。",
            observed_at=datetime.now(UTC).isoformat(),
            content_hash="0" * 64,
        )
    ]

    assert viable_results("特朗普 新动态 最新消息", results) == results


def test_two_secondary_snippets_are_not_enough_without_opened_page_evidence() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Acme update",
                    "url": "https://one.example/acme",
                    "content": "A search-result description.",
                },
                {
                    "title": "Acme update",
                    "url": "https://two.example/acme",
                    "content": "Another search-result description.",
                },
            ]
        },
        2,
    )

    assert not source_corroboration_sufficient("Acme latest update", results)


def test_viable_results_rejects_unrelated_fallback_noise() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Microsoft account support",
                    "url": "https://example.com/account",
                    "content": "Sign in and manage an unrelated account.",
                },
                {
                    "title": "广州黄埔 2026-09-12 天气预报",
                    "url": "https://example.com/weather",
                    "content": "广州黄埔当天气温和降水预报。",
                },
            ]
        },
        10,
    )

    filtered = viable_results("广州黄埔 2026-09-12 天气", results)

    assert [item.url for item in filtered] == ["https://example.com/weather"]


def test_temporal_filter_rejects_an_explicit_stale_page_for_a_current_query() -> None:
    today = datetime.now(UTC).date().isoformat()
    results = normalize_results(
        {
            "results": [
                {
                    "title": "2025-12-13 东莞天气回顾",
                    "url": "https://stale.example/2025/12/13/weather",
                    "content": "2025年12月13日东莞降温并有小雨。",
                    "publishedDate": "2025-12-13",
                },
                {
                    "title": "东莞天气预报",
                    "url": "https://current.example/weather",
                    "content": "东莞当前逐日天气预报。",
                },
            ]
        },
        10,
    )

    assert not temporal_compatible(f"东莞 天气预报 {today}", results[0])
    assert temporal_compatible(f"东莞 天气预报 {today}", results[1])
    assert [item.url for item in viable_results(f"东莞 天气预报 {today}", results)] == [
        "https://current.example/weather"
    ]


def test_current_date_rejects_a_result_whose_url_exposes_a_conflicting_year() -> None:
    today = datetime.now(UTC).date()
    stale_year = today.year - 1
    result = normalize_results(
        {
            "results": [
                {
                    "title": f"广州今日天气 {today.isoformat()}",
                    "url": f"https://weather.example/archive/{stale_year}1021.html",
                    "content": f"广州 {today.isoformat()} 气温 29℃。",
                }
            ]
        },
        1,
    )[0]

    assert not temporal_compatible(f"广州 天气 {today.isoformat()}", result)


def test_current_information_prefers_a_topic_page_over_a_history_archive() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "广州历史天气查询",
                    "url": "https://weather.example/history/guangzhou",
                    "content": "广州往年天气记录和历史气温。",
                },
                {
                    "title": "广州7天天气预报",
                    "url": "https://weather.example/forecast/guangzhou",
                    "content": "广州未来逐日天气、气温和降雨预报。",
                },
            ]
        },
        2,
    )

    ranked = rank_results("广州 未来一周 天气预报", results, 2)

    assert ranked[0].url == "https://weather.example/forecast/guangzhou"


def test_latest_news_prefers_a_recent_dated_result_over_an_old_article() -> None:
    today = datetime.now(UTC).date()
    recent = today - timedelta(days=3)
    old = today - timedelta(days=500)
    results = normalize_results(
        {
            "results": [
                {
                    "title": f"Acme update {old.isoformat()}",
                    "url": f"https://news.example/{old.isoformat()}/acme",
                    "content": "Acme old news update.",
                },
                {
                    "title": f"Acme update {recent.isoformat()}",
                    "url": f"https://news.example/{recent.isoformat()}/acme",
                    "content": "Acme recent news update.",
                },
            ]
        },
        2,
    )

    ranked = rank_results("Acme 最新动态", results, 2)

    assert ranked[0].url.endswith(f"/{recent.isoformat()}/acme")


def test_live_interval_page_can_pass_without_fabricating_missing_endpoint_dates() -> None:
    start = datetime.now(UTC).date()
    end = start + timedelta(days=6)
    middle = start + timedelta(days=2)
    result = normalize_results(
        {
            "results": [
                {
                    "title": "Acme未来一周逐日时间表",
                    "url": "https://acme.com/schedule",
                    "content": (
                        f"{start.month}月{start.day}日 正常；"
                        f"{middle.month}月{middle.day}日 维护；"
                        f"{end.month}月{end.day}日 正常。"
                    ),
                }
            ]
        },
        1,
    )
    query = f"Acme 未来一周 时间表 {start.isoformat()} 至 {end.isoformat()}"

    assert web_provider._temporal_interval_sufficient(query, result)


def test_historical_interval_still_requires_explicit_boundary_evidence() -> None:
    result = normalize_results(
        {
            "results": [
                {
                    "title": "Acme历史记录",
                    "url": "https://acme.com/history",
                    "content": "历史记录摘要，但未列出请求区间。",
                }
            ]
        },
        1,
    )

    assert not web_provider._temporal_interval_sufficient(
        "Acme 过去一周 历史记录 2026-09-01 至 2026-09-07",
        result,
    )


def test_as_of_query_accepts_an_earlier_release_and_rejects_a_future_one() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "Download Python",
                    "url": "https://www.python.org/downloads/",
                    "content": "Python 3.14.7 was released on 2026-08-14.",
                    "publishedDate": "2026-08-14",
                },
                {
                    "title": "Future Python release",
                    "url": "https://www.python.org/downloads/future/",
                    "content": "Python 3.15.0 will be released on 2026-10-01.",
                    "publishedDate": "2026-10-01",
                },
            ]
        },
        10,
    )
    query = "Python current stable release as of 2026-09-13 official"

    assert temporal_compatible(query, results[0])
    assert not temporal_compatible(query, results[1])
    assert [item.url for item in viable_results(query, results)] == [
        "https://www.python.org/downloads/"
    ]


def test_query_intent_words_are_not_misclassified_as_hard_entities() -> None:
    assert web_provider._entity_terms("Python current stable release 2026-09-13 official") == (
        "python",
    )
    assert web_provider._entity_terms(
        "EU AI Act implementation timeline as of 2026-09-13 official"
    ) == ("eu",)
    assert web_provider._entity_terms("2024年 广东省 全省平均年降水量 水资源公报 官方") == (
        "广东省",
    )


def test_explicit_official_query_does_not_treat_lookalike_domain_as_primary() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "DeepSeek chat assistant",
                    "url": "https://deepseek.chat/",
                    "content": "An unofficial chat website.",
                },
                {
                    "title": "DeepSeek official release",
                    "url": "https://api-docs.deepseek.com/news/news250120",
                    "content": "DeepSeek official model release announcement.",
                },
            ]
        },
        10,
    )

    assert not web_provider._primary_source("DeepSeek official release", results[0])
    assert web_provider._primary_source("DeepSeek official release", results[1])
    assert not source_corroboration_sufficient(
        "DeepSeek official release",
        (results[0],),
    )


def test_short_acronym_does_not_claim_an_unrelated_prefixed_domain() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "EUROPA - unofficial mirror",
                    "url": "http://eu-int.net/",
                    "content": "An unofficial EU information mirror.",
                },
                {
                    "title": "European Commission",
                    "url": "https://commission.europa.eu/",
                    "content": "Official European Commission website.",
                },
            ]
        },
        10,
    )

    assert not web_provider._primary_source("EU official information", results[0])
    assert web_provider._primary_source("EU official information", results[1])


def test_temporal_filter_keeps_an_undated_dynamic_page_for_a_near_current_query() -> None:
    today = datetime.now(UTC).date().isoformat()
    result = normalize_results(
        {
            "results": [
                {
                    "title": "Acme current service status dashboard",
                    "url": "https://status.example.com/acme",
                    "content": "Last indexed in 2025. Live incidents and current availability.",
                }
            ]
        },
        1,
    )[0]

    assert temporal_compatible(f"Acme service status {today}", result)


def test_temporal_filter_does_not_use_a_live_page_for_a_nearby_historical_day() -> None:
    requested_day = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    result = normalize_results(
        {
            "results": [
                {
                    "title": "Acme current service status dashboard",
                    "url": "https://status.example.com/acme",
                    "content": "Live incidents and current availability.",
                }
            ]
        },
        1,
    )[0]

    assert not temporal_compatible(f"Acme service status {requested_day}", result)


def test_temporal_filter_rejects_a_dated_archive_disguised_as_latest() -> None:
    today = datetime.now(UTC).date().isoformat()
    result = normalize_results(
        {
            "results": [
                {
                    "title": "Acme latest release",
                    "url": "https://news.example.com/2025/02/28/acme",
                    "content": "A later retrospective mentions 2026 but republishes the old item.",
                }
            ]
        },
        1,
    )[0]

    assert not temporal_compatible(f"Acme latest release {today}", result)


def test_viable_results_enforces_explicit_entity_or_place() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "2024 年浙江省水资源公报",
                    "url": "https://zj.example/report",
                    "content": "2024 年全省平均降水量为 1863.9 毫米。",
                },
                {
                    "title": "2024 年广东省水资源公报",
                    "url": "https://gd.example/report",
                    "content": "2024 年广东省平均降水量为 2190.8 毫米。",
                },
            ]
        },
        10,
    )

    filtered = viable_results(
        "2024年 广东省 全年平均降水量 水资源公报",
        results,
    )

    assert [item.url for item in filtered] == ["https://gd.example/report"]


def test_viable_results_enforces_requested_document_type() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "2024 年广东省水资源公报",
                    "url": "https://gd.example/water",
                    "content": "广东省平均年降水量为 2190.8 毫米。",
                },
                {
                    "title": "2024 年广东气候监测公报",
                    "url": "https://gd.example/climate",
                    "content": "广东省年平均降水量气象统计。",
                },
            ]
        },
        10,
    )

    filtered = viable_results(
        "2024年 广东省 年平均降水量 气候公报",
        results,
    )

    assert [item.url for item in filtered] == ["https://gd.example/climate"]


def test_viable_results_understands_unspaced_conversational_brand_question() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "OpenAI 发布新产品",
                    "url": "https://example.com/openai",
                    "content": "OpenAI 最新产品发布信息。",
                },
                {
                    "title": "Microsoft 发布新产品",
                    "url": "https://example.com/microsoft",
                    "content": "另一家公司发布信息。",
                },
            ]
        },
        10,
    )

    filtered = viable_results("OpenAI最近发布了什么产品", results)

    assert [item.url for item in filtered] == ["https://example.com/openai"]


def test_viable_results_understands_unspaced_policy_question() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "广州市人工智能产业 2026 年工作要点政策解读",
                    "url": "https://gz.gov.cn/policy",
                    "content": "广州市人民政府发布人工智能产业政策。",
                },
                {
                    "title": "短视频平台首页",
                    "url": "https://example.com/video",
                    "content": "与查询无关的娱乐内容。",
                },
            ]
        },
        10,
    )

    filtered = viable_results("广州市人工智能产业最新政策", results)

    assert [item.url for item in filtered] == ["https://gz.gov.cn/policy"]


def test_viable_results_rejects_entity_only_pages_without_topic_coverage() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "title": "中国政府门户网站",
                    "url": "https://example.com/china",
                    "content": "介绍中国的综合信息。",
                },
                {
                    "title": "中国新能源汽车 2025 年出口数据",
                    "url": "https://example.com/vehicle-export",
                    "content": "新能源汽车出口数量和同比变化统计。",
                },
            ]
        },
        10,
    )

    filtered = viable_results("中国新能源汽车2025年出口数据", results)

    assert [item.url for item in filtered] == ["https://example.com/vehicle-export"]


async def test_search_reuses_short_lived_cache_without_requerying_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "CacheEntity current result",
                        "url": "https://cacheentity.com/result",
                        "content": "CacheEntity current information.",
                    }
                ]
            },
        )

    request = SearchRequest(query="CacheEntity current", limit=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await search_searxng(request, client, "http://cache-test:8080")
        second = await search_searxng(request, client, "http://cache-test:8080")

    assert first == second
    assert calls == 1


def test_search_cache_keeps_a_bounded_stale_copy_for_upstream_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_key = "bounded-stale-cache"
    clock = [100.0]
    result = SearchResult(
        title="Previously verified result",
        url="https://example.com/verified",
        snippet="Previously opened and verified public evidence.",
        observed_at="2026-09-14T00:00:00+00:00",
        content_hash="verified",
    )
    monkeypatch.setattr(web_provider.time, "monotonic", lambda: clock[0])
    web_provider._SEARCH_CACHE.pop(cache_key, None)
    web_provider._store_cached_results(cache_key, [result])

    clock[0] = 100.0 + web_provider._SEARCH_CACHE_TTL_SEC + 1.0
    assert web_provider._cached_results(cache_key) is None
    assert web_provider._cached_results(cache_key, allow_stale=True) == [result]

    clock[0] = 100.0 + web_provider._SEARCH_CACHE_STALE_TTL_SEC + 1.0
    assert web_provider._cached_results(cache_key, allow_stale=True) is None


async def test_search_cache_is_reused_across_result_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_page_fetches(_url: str) -> bool:
        return False

    monkeypatch.setattr(web_provider, "_public_destination", reject_page_fetches)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"CrossLimitEntity current result {index}",
                        "url": f"https://crosslimitentity.com/{index}",
                        "content": "CrossLimitEntity current public information.",
                    }
                    for index in range(4)
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await search_searxng(
            SearchRequest(query="CrossLimitEntity current", limit=4),
            client,
            "http://cross-limit-cache-test:8080",
        )
        second = await search_searxng(
            SearchRequest(query="CrossLimitEntity current", limit=2),
            client,
            "http://cross-limit-cache-test:8080",
        )

    assert len(first) == 4
    assert second == first[:2]
    assert calls == 1


def test_match_search_keeps_cross_language_report_without_literal_query_operators() -> None:
    result = SearchResult(
        title="Inter Miami - Cruz Azul: Campeones Cup",
        url="https://sports.example/2026/09/16/inter-miami-cruz-azul",
        snippet="Inter Miami y Cruz Azul disputaron la final el 16 de septiembre de 2026.",
        observed_at="2026-09-17T00:00:00Z",
        content_hash="test",
    )
    assert viable_results("Inter Miami vs Cruz Azul latest result 2026", [result]) == [result]
    assert viable_results("Inter Miami vs another-team latest result 2026", [result]) == []


def test_english_historical_date_range_retains_exact_period_and_rejects_other_week() -> None:
    query = "Miami weather last week September 7-13 2026"
    correct = SearchResult(
        title="Miami historical weather",
        url="https://weather.example/miami/history",
        snippet="Miami observations 2026-09-07 to 2026-09-13: maximum temperature 32 C.",
        observed_at="2026-09-17T00:00:00Z",
        content_hash="test",
    )
    wrong = correct.model_copy(update={"snippet": "Miami observations 2026-09-01 to 2026-09-06."})
    assert viable_results(query, [correct]) == [correct]
    assert viable_results(query, [wrong]) == []


def test_english_weather_query_rejects_tourism_without_weather_data() -> None:
    results = normalize_results(
        {
            "results": [
                {
                    "url": "https://travel.example/miami",
                    "title": "Miami city guide",
                    "content": "Miami museums, restaurants and attractions.",
                },
                {
                    "url": "https://observations.example/miami",
                    "title": "Miami weather history",
                    "content": "Miami weather observations: temperature and precipitation.",
                },
            ]
        },
        5,
    )
    retained = viable_results(
        "Miami historical weather September 7-13 2026", results, allow_undated_historical=True
    )
    assert [item.url for item in retained] == ["https://observations.example/miami"]


def test_historical_us_observation_dates_survive_newer_page_publication():
    item = normalize_results({"results": [{
        "url": "https://weather.example/miami/history", "title": "Miami weather history",
        "content": "Daily weather observations: 9/7/2026 90F; 9/13/2026 92F",
        "publishedDate": "2026-09-17",
    }]}, 1)[0]
    assert temporal_compatible("Miami weather September 7-13 2026", item)
    assert not temporal_compatible("Miami weather September 1-6 2026", item)


def test_english_query_recovery_compacts_intent_without_losing_subject():
    assert "miami weather history" in web_provider._selected_search_queries("Miami historical weather September 2026")
    assert "inter miami results" in web_provider._selected_search_queries("Inter Miami CF latest match result")


async def test_latest_match_follows_bounded_report_links_and_keeps_report_citation(monkeypatch):
    opened = []
    async def fetch(client, url):
        opened.append(url)
        if url.endswith("/team"):
            return web_provider.FetchResponse(
                url=url, content="Index only", content_hash="a" * 64,
                source_links=tuple((f"https://sports.example/report/{i}", f"Inter Miami victory recap {i}") for i in range(5))
                + (("http://127.0.0.1/private", "Inter Miami victory recap"),),
            )
        content = "Inter Miami victory match report September 16, 2026 final score 2-0"
        return web_provider.FetchResponse(url=url, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest())
    monkeypatch.setattr(web_provider, "fetch_public_page", fetch)
    hits = normalize_results({"results": [{"title": "Inter Miami", "url": "https://sports.example/team", "content": "Team index"}]}, 1)
    async with httpx.AsyncClient() as client:
        results = await web_provider.enrich_results("Inter Miami latest match result", hits, client)
    assert len(opened) == 4  # one index plus at most three actual reports
    assert not any("127.0.0.1" in url for url in opened)
    assert results[0].url == "https://sports.example/report/0"
    assert "September 16, 2026" in results[0].snippet
    assert results[0].content_hash == hashlib.sha256(results[0].snippet.encode()).hexdigest()


def test_source_links_are_separate_from_factual_content():
    page = web_provider.FetchResponse(url="https://example.org", content="text", content_hash="a" * 64,
                                     source_links=(("https://example.org/report", "Report"),))
    assert page.content == "text"
    assert page.model_dump()["source_links"] == (("https://example.org/report", "Report"),)


def test_dated_past_weather_discovery_retains_history_intent():
    assert web_provider._discovery_query_variant("Miami weather September 7-13 2020") == "miami weather history"


async def test_official_forecast_directory_rejects_fuzzy_places_and_opens_matching_station(monkeypatch):
    async def fetch(client, url):
        assert url == "https://www.nmc.cn/publish/forecast/AGD/luogang.html"
        return web_provider.FetchResponse(url=url, content="黄埔（广州） 9月18日 晴 34℃ 25℃", content_hash="a" * 64)
    monkeypatch.setattr(web_provider, "fetch_public_page", fetch)
    def handler(request):
        assert request.url.params["q"] == "广州黄埔"
        return httpx.Response(200, json={"data": [
            "id|黄埔（广州）|广东省|/AGD/luogang.html|113|23",
            "wrong|黄平|贵州省|/AGZ/huangping.html|107|26",
            "bad|广州黄埔|广东省|//127.0.0.1/private|0|0",
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await web_provider._opened_official_forecast_hints("广州黄埔 2026-09-18 天气预报", client)
    assert len(results) == 1
    assert results[0].title == "黄埔（广州）天气预报"
    assert "34℃" in results[0].snippet
    assert results[0].content_hash == hashlib.sha256(results[0].snippet.encode()).hexdigest()


async def test_english_news_uses_source_language_market():
    def handler(request):
        assert request.url.params["setlang"] == "en-us"
        assert request.url.params["mkt"] == "en-US"
        return httpx.Response(200, text="<rss><channel/></rss>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await web_provider._search_news_rss("Inter Miami latest match result", client) == []


async def test_sports_directory_discovers_ids_and_only_opens_completed_games(monkeypatch):
    calls = []
    async def fetch(client, url):
        calls.append(url)
        if "/club/" in url:
            return web_provider.FetchResponse(url=url, content="Harbor United", content_hash="a" * 64,
                source_links=(("https://www.espn.com/soccer/report/_/gameId/987654", "HBU 2 FT 0 ABC"),
                              ("https://www.espn.com/soccer/matchstats/_/gameId/987655", "HBU Tomorrow ABC")))
        content = "Harbor United 2-0 ABC (Sep 16, 2026) Game Analysis"
        return web_provider.FetchResponse(url=url, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest())
    monkeypatch.setattr(web_provider, "fetch_public_page", fetch)
    def handler(request):
        assert request.url.params["query"] == "harbor united"
        return httpx.Response(200, json={"results": [{"type": "team", "contents": [{
            "displayName": "Harbor United FC", "link": {"web": "https://www.espn.com/football/club/_/id/999/harbor-united"}
        }]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await web_provider._opened_sports_result_hints("Harbor United FC latest match result", client)
    assert len(calls) == 2
    assert len(result) == 1
    assert result[0].url.endswith("987654")
    assert "Sep 16, 2026" in result[0].snippet
    assert result[0].content_hash == hashlib.sha256(result[0].snippet.encode()).hexdigest()


def test_dated_scorecard_is_relevant_without_query_year_or_full_team_name_in_title():
    result = SearchResult(title="Miami 2-0 Cruz Azul (Sep 16, 2026) Game Analysis - ESPN",
        url="https://www.espn.com/soccer/report/_/gameId/123456",
        snippet="网页正文摘录： Inter Miami CF defeated Cruz Azul 2-0 on September 16, 2026.",
        observed_at="2026-09-17T00:00:00Z", content_hash="a" * 64)
    query = "Inter Miami CF vs Cruz Azul match date"
    assert web_provider._relevance_score(query, result) > 0
    assert viable_results(query, [result]) == [result]
    assert viable_results("Inter Miami CF vs Cruz Azul 2025-09-16 match date", [result]) == []


async def test_json_fetch_retains_all_structure_values_and_hash(monkeypatch):
    async def public(_url):
        return True
    monkeypatch.setattr(web_provider, "_public_destination", public)
    payload = {"periods": ["2026-01", "2026-02"], "inventory": [88, 78],
               "units": "items", "summary": {"maximum": 200}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        page = await fetch_public_page(client, "https://data.example/inventory")
    assert page is not None
    assert json.loads(page.content) == payload
    assert page.content_hash == hashlib.sha256(page.content.encode()).hexdigest()



async def test_chinese_query_does_not_restrict_search_source_language():
    calls = []
    def handler(request):
        if request.url.path == "/search":
            calls.append(request.url.params["language"])
        return httpx.Response(200, json={"results": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await search_searxng(SearchRequest(query="量子纠错研究进展", limit=3), client, "http://cross-language-test:8080")
    assert calls
    assert set(calls) == {"auto"}


async def test_json_fetch_preserves_units_without_inference_or_conversion(monkeypatch):
    async def public(_url):
        return True
    monkeypatch.setattr(web_provider, "_public_destination", public)
    payload = {"measurement": 31, "unit": "kg", "ratio": 0.8, "missing": None}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        page = await fetch_public_page(client, "https://data.example/measurements")
    assert page is not None
    assert json.loads(page.content) == payload
    assert "80%" not in page.content



async def test_fetch_does_not_rewrite_source_endpoint_or_parameters(monkeypatch):
    async def public(_url):
        return True
    monkeypatch.setattr(web_provider, "_public_destination", public)
    urls = ["https://forecast.weather.gov/MapClick.php?textField1=20&textField2=-80",
            "https://data.example/records?unit=kg&period=2026-02"]
    observed = []
    def respond(req):
        observed.append(str(req.url))
        return httpx.Response(200, json={"data": "Source-specific information remains unchanged"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for url in urls:
            page = await fetch_public_page(client, url)
            assert page is not None and page.url == url
    assert observed == urls



async def test_provider_deadline_preserves_completed_sources_and_cancels_pending_work(monkeypatch):
    from tests.fakes.web_provider import SlowSearchPipeline
    content = "Inventory quarterly report: the requested inventory is 42 units, confirmed in the current published record."
    source = SearchResult(title="Inventory quarterly report", url="https://inventory.example/report",
        snippet=content, observed_at="2026-09-17T00:00:00Z", content_hash=hashlib.sha256(content.encode()).hexdigest())
    pipeline = SlowSearchPipeline(source)
    monkeypatch.setattr(web_provider, "_search_searxng_pipeline", pipeline)
    monkeypatch.setattr(web_provider, "SEARCH_PIPELINE_TIMEOUT_SECONDS", 0.02)
    async with httpx.AsyncClient() as client:
        result = await search_searxng(SearchRequest(query="Inventory quarterly report", limit=3), client, "http://bounded-test")
    assert result == [source]
    assert result[0].content_hash == hashlib.sha256(result[0].snippet.encode()).hexdigest()
    assert pipeline.cancelled


def test_body_excludes_navigation_but_discovery_retains_links() -> None:
    from apps.web_provider.main import _VisibleTextParser

    markup = '<nav><a href="/records">Records</a></nav><main><h2>Owner</h2><p>Ada</p></main><footer>Menu</footer>'
    assert extract_visible_text(markup) == "Owner\nAda"
    links = _VisibleTextParser()
    links.feed(markup)
    assert links.links == [("/records", "Records")]


def test_discovery_recovers_subject_leads_without_promoting_them_to_answer_evidence() -> None:
    rows = normalize_results({"results": [
        {"title": "Orion supported endpoints", "url": "https://orion.example/endpoints",
         "content": "Orion provides the alpha and beta services."},
        {"title": "Unrelated directory", "url": "https://other.example/",
         "content": "An unrelated vendor provides services."},
    ]}, 5)
    query = "Orion deployment inventory"
    assert viable_results(query, rows) == []
    recovered = web_provider.discovery_candidates(query, rows)
    assert recovered == [rows[0]]
    assert recovered[0].content_hash == rows[0].content_hash
    assert "网页正文摘录" not in recovered[0].snippet


def test_discovery_does_not_change_an_existing_strict_success() -> None:
    rows = normalize_results({"results": [
        {"title": "Orion deployment inventory", "url": "https://orion.example/inventory",
         "content": "Orion deployment inventory lists alpha and beta."},
        {"title": "Orion unrelated guide", "url": "https://orion.example/guide",
         "content": "Orion travel guide."},
    ]}, 5)
    strict = viable_results("Orion deployment inventory", rows)
    assert strict == [rows[0]]
    assert web_provider.discovery_candidates("Orion deployment inventory", rows) == strict


def test_short_complete_list_is_not_cut_into_keyword_windows():
    content = "Orion member institutions\n" + "\n".join(f"Institute {i}" for i in range(1, 41))
    assert relevant_excerpt("Orion member institutions", content, 5000) == content


def test_document_identity_preserves_resource_path_and_query():
    identity = web_provider._source_identity
    assert identity("https://docs.example/spec/123456") != identity("https://docs.example/manual/123456")
    assert identity("https://docs.example/report?year=2025") != identity("https://docs.example/report?year=2026")
    assert identity("https://docs.example/weather/123456.shtml") != identity("https://docs.example/weather40d/123456.shtml")


def test_discovery_keeps_partial_name_lead_alongside_strict_hit():
    rows = normalize_results({"results": [
        {"title": "Orion Nova specifications", "url": "https://orion.example/index", "content": "Orion Nova specifications index."},
        {"title": "Nova specifications", "url": "https://specifications.example/nova", "content": "Nova specifications list memory and storage."},
        {"title": "Travel diary", "url": "https://other.example/", "content": "Unrelated travel diary."},
    ]}, 5)
    found = web_provider.discovery_candidates("Orion Nova specifications", rows)
    assert rows[0] in found and rows[1] in found
    assert rows[2] not in found


async def test_engine_errors_are_not_successful_empty_search():
    def handler(_request):
        return httpx.Response(200, json={"results": [], "unresponsive_engines": [["engine", "CAPTCHA"]]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SearchBackendUnavailable):
            await search_searxng(SearchRequest(query="unique engine failure inventory", limit=3), client, "http://searxng:8080")


async def test_engine_errors_preserve_partial_results(monkeypatch):
    async def reject(_url):
        return False
    monkeypatch.setattr(web_provider, "_public_destination", reject)
    def handler(_request):
        return httpx.Response(200, json={"results": [{"title": "Orion inventory", "url": "https://orion.example/inventory", "content": "Orion inventory contains Alpha and Beta."}], "unresponsive_engines": [["engine", "rate limit"]]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_searxng(SearchRequest(query="Orion inventory", limit=3), client, "http://partial-engine:8080")
    assert any(item.url == "https://orion.example/inventory" for item in result)


async def test_partial_backend_failures_are_visible_without_discarding_evidence(monkeypatch):
    async def reject(_url):
        return False
    monkeypatch.setattr(web_provider, "_public_destination", reject)
    def handler(_request):
        return httpx.Response(200, json={"results": [{"title": "Orion inventory",
            "url": "https://orion.example/inventory", "content": "Orion inventory contains Alpha and Beta."}],
            "unresponsive_engines": [["engine-a", "CAPTCHA"], ["engine-b", "rate limit"]]})
    diagnostics = []
    token = web_provider._SEARCH_DIAGNOSTICS.set(diagnostics)
    request = SearchRequest(query="Orion inventory", limit=3, queries_managed=True)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await search_searxng(request, client, "http://degraded-inventory:8080")
        assert any(item.url == "https://orion.example/inventory" for item in result)
        assert diagnostics == [{"engine": "engine-a", "reason": "CAPTCHA"},
                               {"engine": "engine-b", "reason": "rate limit"}]
        assert web_provider._search_cache_key("http://degraded-inventory:8080", request) not in web_provider._SEARCH_CACHE
    finally:
        web_provider._SEARCH_DIAGNOSTICS.reset(token)
    assert web_provider._SEARCH_DIAGNOSTICS.get() is None


def test_normalization_keeps_engine_diversity_before_result_limit():
    rows = [{"title": f"Inventory {i}", "url": f"https://a.example/{i}",
             "content": "Division inventory", "engine": "engine-a"} for i in range(30)]
    rows += [{"title": "Exact child record", "url": "https://b.example/child",
              "content": "Division inventory detail", "engines": ["engine-b"]}]
    rows += [{"title": "Same child record", "url": "https://b.example/child",
              "content": "same record", "engine": "engine-c"}]
    result = normalize_results({"results": rows}, 4, query="Division inventory")
    child = next(item for item in result if item.url == "https://b.example/child")
    assert child.engines == ("engine-b", "engine-c")
    assert child.queries == ("Division inventory",)
    assert len(result) == 4
    assert len({item.url for item in result}) == 4
    assert hashlib.sha256(child.snippet.encode()).hexdigest() == child.content_hash


def test_candidate_archive_merges_engine_and_query_origins_without_duplicate_evidence():
    first = normalize_results({"results": [{"title": "Orion specifications", "url": "https://orion.example/spec",
        "content": "Original brief", "engine": "a"}]}, 4, query="Orion specifications")
    second = normalize_results({"results": [{"title": "Orion specifications", "url": "https://orion.example/spec",
        "content": "A longer discovery description", "engine": "b"}]}, 4, query="Orion technical details")
    candidates = web_provider._unselected_candidates("Orion specifications", first + second, [])
    assert len(candidates) == 1
    assert candidates[0].engines == ("a", "b")
    assert candidates[0].queries == ("Orion specifications", "Orion technical details")
    assert "A longer discovery description" in candidates[0].snippet


async def test_enrichment_retains_original_beyond_preview_for_later_reread(monkeypatch):
    content = "Alpha inventory issued 2026-09-18 UTC\n" + "Ordinary source text. " * 700 + "\nFinal record: Beta 17 EUR."
    async def fetch(client, url):
        return web_provider.FetchResponse(url=url, content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest())
    monkeypatch.setattr(web_provider, "fetch_public_page", fetch)
    hits = normalize_results({"results": [{"title": "Alpha inventory", "url": "https://inventory.example/report", "content": "Inventory records"}]}, 1)
    async with httpx.AsyncClient() as client:
        results = await web_provider.enrich_results("Alpha inventory", hits, client)
    assert content in results[0].snippet
    assert "Final record: Beta 17 EUR." in results[0].snippet
    assert results[0].content_hash == hashlib.sha256(results[0].snippet.encode()).hexdigest()



def test_navigation_roles_are_discovery_only_but_breadcrumbs_remain_evidence_context():
    from apps.web_provider.main import _VisibleTextParser, extract_visible_text
    markup = ('<div role="navigation"><div><a href="/other">Unrelated menu</a></div><br></div>'
              '<nav aria-label="Breadcrumb"><a href="/org">Organization</a> &gt; Division Alpha</nav>'
              '<main><h1>Inventory report</h1><p>Updated 2026-09-18. Unit: items.</p>'
              '<table><tr><th>Location</th><th>Count</th></tr><tr><td>Alpha</td><td>31</td></tr></table></main>')
    text = extract_visible_text(markup)
    assert "Unrelated menu" not in text
    assert "Organization > Division Alpha" in text
    assert "Updated 2026-09-18. Unit: items." in text
    assert "Location | Count" in text and "Alpha | 31" in text
    links = _VisibleTextParser()
    links.feed(markup)
    assert ("/other", "Unrelated menu") in links.links


def test_nested_navigation_does_not_swallow_following_body():
    from apps.web_provider.main import extract_visible_text
    markup = '<nav><div role="menu"><div>Menu label</div></div></nav><article>Actual full record.</article>'
    assert extract_visible_text(markup) == "Actual full record."


def test_public_json_blocks_preserve_nested_records_without_executing_scripts():
    markup = '<nav>Unrelated menu</nav><main><h1>Alpha inventory</h1></main>'
    payload = {"entity": {"parent": "Alpha", "branch": "Beta"},
               "rows": [{"date": "2026-09-18", "amount": 0, "unit": "kg"},
                        {"date": "2026-09-19", "amount": None, "unit": "kg"}]}
    markup += '<script type="application/json">' + json.dumps(payload) + '</script>'
    markup += '<script>window.fetch("https://private.example");</script>'
    content = web_provider.extract_public_page_content(markup)
    assert "Unrelated menu" not in content
    assert "window.fetch" not in content
    assert "Alpha inventory" in content
    assert json.loads(content.split("[Embedded public JSON]\n", 1)[1]) == payload


def test_malformed_or_executable_data_does_not_hide_visible_body():
    markup = '<main>Ordinary article text.</main><script type="application/json">{broken}</script>'
    assert web_provider.extract_public_page_content(markup) == "Ordinary article text."


@pytest.mark.parametrize("size,expected", [(749999, False), (750000, False), (750001, True)])
async def test_fetch_marks_byte_truncation_without_changing_retained_hash(monkeypatch, size, expected):
    async def public(_url):
        return True
    monkeypatch.setattr(web_provider, "_public_destination", public)
    body = b"x" * size
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, content=body, headers={"content-type": "text/plain"}))) as client:
        page = await fetch_public_page(client, "https://records.example/data")
    assert page.fetch_truncated is expected
    assert page.read_mode == "TEXT"
    assert len(page.content) == min(size, 750000)
    assert page.content_hash == hashlib.sha256(page.content.encode()).hexdigest()


def test_accessible_icon_values_remain_bound_to_their_table_rows():
    content = extract_visible_text('<nav><img alt="menu icon"></nav><table>'
        '<tr><th>Item</th><th>Status</th><th>Count</th></tr>'
        '<tr><td>Alpha</td><td><img alt="Available" src="ignored.png"></td><td>10</td></tr>'
        '<tr><td>Beta</td><td><svg role="img" aria-label="Delayed"><path /></svg></td><td>0</td></tr>'
        '</table><img alt="" title="decorative"><img aria-hidden="true" alt="hidden">')
    assert 'Alpha | Available | 10' in content
    assert 'Beta | Delayed | 0' in content
    assert 'menu icon' not in content and 'hidden' not in content and 'decorative' not in content
    assert 'ignored.png' not in content
