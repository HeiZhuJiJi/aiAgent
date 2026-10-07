import asyncio
import calendar
import hashlib
import json
import logging
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Awaitable
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any, ClassVar
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from enterprise_agent.runtime.discovery_pool import bounded_labels, diverse_pool
from enterprise_agent.runtime.evidence_records import pack_structured_blocks


class SearchRequest(BaseModel):
    queries_managed: bool = False
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    fetch_truncated: bool = False
    read_mode: str = "UNKNOWN"
    engines: tuple[str, ...] = Field(default=(), max_length=16)
    queries: tuple[str, ...] = Field(default=(), max_length=8)
    title: str
    url: str
    snippet: str
    published_at: str | None = None
    observed_at: str
    content_hash: str
    source_links: tuple[tuple[str, str], ...] = Field(default=(), max_length=2000)


class SearchCandidate(BaseModel):
    engines: tuple[str, ...] = Field(default=(), max_length=16)
    queries: tuple[str, ...] = Field(default=(), max_length=8)
    url: str = Field(max_length=4000)
    title: str = Field(max_length=500)
    snippet: str = Field(max_length=1200)
    reason: str = Field(max_length=100)


_SEARCH_DIAGNOSTICS: ContextVar[list[dict[str, str]] | None] = ContextVar("search_diagnostics", default=None)


class SearchResponse(BaseModel):
    results: list[SearchResult]
    backend_diagnostics: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    candidates: list[SearchCandidate] = Field(default_factory=list, max_length=40)


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


class FetchResponse(BaseModel):
    fetch_truncated: bool = False
    read_mode: str = "UNKNOWN"
    url: str
    content: str
    content_hash: str
    source_links: tuple[tuple[str, str], ...] = Field(default=(), max_length=2000)


class SearchBackendUnavailable(RuntimeError):
    pass


_SEARCH_CACHE_TTL_SEC = 120.0
_SEARCH_CACHE_STALE_TTL_SEC = 900.0
_SEARCH_CACHE_MAX_ENTRIES = 128
_SEARCH_CACHE: OrderedDict[str, tuple[float, tuple[SearchResult, ...], tuple[SearchResult, ...]]] = OrderedDict()


_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_GENERIC_TERMS = {
    "信息",
    "官方信息",
    "最新数据",
    "实时状态",
    "历史记录",
    "事件记录",
}
# Query operators and temporal language are not named entities. Requiring every
# one of these tokens in a page discarded valid cross-language results.
_SEARCH_FUNCTION_WORDS = frozenset(
    {
        "most",
        "cf",
        "fc",
        "representatives",
        "opponent",
        "competition",
        "daily",
        "summary",
        "observed",
        "observations",
        "conditions",
        "condition",
        "high",
        "low",
        "average",
        "recorded",
        "completed",
        "final",
        "recent",
        "chief",
        "justice",
        "president",
        "supreme",
        "court",
        "speaker",
        "house",
        "congress",
        "senate",
        "pro",
        "tempore",
        "leadership",
        "office",
        "holder",
        "head",
        "vs",
        "versus",
        "result",
        "results",
        "score",
        "scores",
        "match",
        "matches",
        "game",
        "games",
        "last",
        "week",
        "weeks",
        "day",
        "days",
        "yesterday",
        "tomorrow",
        "next",
        "past",
        "previous",
        "historical",
        "temperature",
        "temperatures",
        "rainfall",
        "precipitation",
        "for",
        "in",
        "on",
        "the",
        "and",
        "who",
        "what",
        "when",
        "is",
        "are",
        "was",
        "were",
        "with",
        "from",
        "to",
        *(name.casefold() for name in calendar.month_name if name),
        *(name.casefold() for name in calendar.month_abbr if name),
    }
)


_QUERY_REFINEMENT_TERMS = {
    "as",
    "current",
    "latest",
    "of",
    "official",
    "stable",
    "实时",
    "气温",
    "降水",
    "风力",
    "最新消息",
    "最新数据",
    "官方信息",
    "实时状态",
    "历史记录",
    "事件记录",
    "官方",
    "发布",
    "公告",
    "最新发布",
}
_SEMANTIC_REPLACEMENTS = (
    ("全年平均降水量", "年平均降水量"),
    ("全省平均年降水量", "年平均降水量"),
    ("气候监测公报", "气候公报"),
    ("降雨量", "降水量"),
    ("雨量", "降水量"),
)
_HIGH_VALUE_PHRASES = (
    "年平均降水量",
    "年降水量",
    "逐日预报",
    "历史天气",
    "气候公报",
    "水资源公报",
    "统计公报",
    "财务报告",
    "研究报告",
    "最新动态",
    "官方公告",
    "政策文件",
    "发布说明",
)
_FOCUS_DOCUMENT_TERMS = frozenset(
    phrase
    for phrase in _HIGH_VALUE_PHRASES
    if phrase.endswith(("公报", "公告", "报告", "文件", "说明"))
)
_COMPARISON_NOISE = {"统计口径", "口径", "分别说明", "区别", "差异", "对比"}
_ENTITY_NOISE = {
    "act",
    "ai",
    "announcement",
    "announcements",
    "as",
    "chat",
    "current",
    "data",
    "date",
    "dates",
    "effective",
    "forecast",
    "history",
    "implementation",
    "information",
    "latest",
    "model",
    "models",
    "news",
    "official",
    "of",
    "overview",
    "policy",
    "price",
    "product",
    "products",
    "release",
    "released",
    "releases",
    "source",
    "sources",
    "stable",
    "status",
    "timeline",
    "update",
    "updates",
    "version",
    "versions",
    "weather",
    "今天",
    "今日",
    "明天",
    "后天",
    "昨天",
    "昨日",
    "前天",
    "未来",
    "全省",
    "全国",
    "天气",
    "天气预报",
    "实况",
    "预报",
    "新闻",
    "气象",
    "政策",
    "最新消息",
    "最新数据",
    "官方信息",
    "实时状态",
    "历史记录",
    "事件记录",
    "官方",
    "官方来源",
    "现任",
    "当前",
    "现在",
    "目前",
    "发布日期",
    "实施阶段",
    "时间表",
}
_TEMPORAL_TOPIC_NOISE = {
    "今天",
    "今日",
    "明天",
    "后天",
    "昨天",
    "昨日",
    "前天",
    "未来",
    "本周",
    "上周",
}
_QUESTION_FILLERS = (
    "我想知道",
    "帮我查一下",
    "帮我查询",
    "请帮我",
    "查询一下",
    "搜索一下",
    "告诉我",
    "请问",
    "能不能",
    "可不可以",
    "是什么",
    "是谁",
    "有哪些",
    "有什么",
    "是多少",
    "怎么样",
    "如何",
    "怎么",
    "什么",
)
_SEARCH_TOPIC_TERMS = (
    "历史天气",
    "天气预报",
    "逐日预报",
    "人工智能",
    "新能源汽车",
    "最新动态",
    "最新消息",
    "最新新闻",
    "最新进展",
    "动态",
    "消息",
    "进展",
    "事件",
    "政策",
    "产品",
    "新闻",
    "数据",
    "价格",
    "天气",
    "预报",
    "公告",
    "发布",
    "宣布",
    "推出",
    "报告",
    "公报",
    "说明",
)
_TOPIC_FAMILIES = (
    (
        ("天气", "天气预报", "历史天气", "逐日预报", "气温", "降雨", "降水", "台风", "weather"),
        (
            "天气",
            "气象",
            "预报",
            "气温",
            "温度",
            "降雨",
            "降水",
            "雷雨",
            "台风",
            "风力",
            "weather",
            "forecast",
            "temperature",
            "rain",
            "climate",
        ),
    ),
    (
        ("新闻", "动态", "消息", "进展", "事件"),
        ("新闻", "动态", "消息", "进展", "事件", "报道", "宣布", "表示"),
    ),
    (
        ("股价", "股票", "汇率", "价格", "报价", "市值", "指数"),
        ("股价", "股票", "汇率", "价格", "报价", "市值", "指数", "行情"),
    ),
    (
        ("政策", "法规", "标准", "公告"),
        ("政策", "法规", "条例", "标准", "公告", "通知", "发布"),
    ),
)
_AUTHORITATIVE_SPECIALISTS = (
    (
        ("天气", "天气预报", "气温", "降雨", "降水", "台风", "weather"),
        ("weather.com.cn", "nmc.cn", "cma.gov.cn", "weather.gov"),
    ),
    (
        ("证券", "股票", "上市", "财务报告", "监管", "filing"),
        ("sec.gov", "sse.com.cn", "szse.cn", "hkex.com.hk"),
    ),
    (
        ("论文", "研究", "临床", "doi", "paper", "study"),
        ("doi.org", "crossref.org", "pubmed.ncbi.nlm.nih.gov"),
    ),
)
_ESTABLISHED_EDITORIAL_HOSTS = (
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "cnbc.com",
    "chinanews.com.cn",
    "cnr.cn",
    "cctv.cn",
    "cctv.com",
    "dw.com",
    "ft.com",
    "jiemian.com",
    "news.cn",
    "npr.org",
    "nytimes.com",
    "people.com.cn",
    "reuters.com",
    "rfi.fr",
    "rthk.hk",
    "sina.com.cn",
    "theguardian.com",
    "thepaper.cn",
    "voachinese.com",
    "washingtonpost.com",
    "wsj.com",
    "xinhuanet.com",
    "zaobao.com.sg",
)
_USER_GENERATED_HOSTS = (
    "baijiahao.baidu.com",
    "toutiao.com",
)
_DYNAMIC_SOURCE_CUE = re.compile(
    r"(?:实时|当前|今日|今天|最新|动态更新|live|current|latest|dashboard|status)",
    re.IGNORECASE,
)
_ENTITY_NOISE.update(_SEARCH_FUNCTION_WORDS)


_ENTITY_BREAK_TERMS = (
    "最近",
    "最新",
    "发布",
    "宣布",
    "推出",
    "发生",
    "表示",
    "目前",
    "现在",
    "今天",
    "明天",
    "未来",
    "上周",
    "本周",
    "是谁",
    "是什么",
    "有什么",
    "有哪些",
)


def build_query_variants(query: str) -> tuple[str, ...]:
    normalized = " ".join(query.split())
    variants: list[str] = [normalized]
    # Search engines often rank SEO summaries above the entity's own current
    # page. A generic primary-source refinement gives every domain (products,
    # policies, organizations, statistics, etc.) a chance to surface its
    # authoritative source without encoding topic-specific sites.
    if not re.search(r"(?:官方|\bofficial\b)", normalized, re.IGNORECASE):
        variants.append(f"{normalized} 官方")
    localized = _ISO_DATE.sub(
        lambda match: f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日",
        normalized,
    )
    if localized != normalized:
        variants.append(localized)
    parts = normalized.split()
    focuses = [part for part in parts if part in _FOCUS_DOCUMENT_TERMS]
    if len(focuses) >= 2:
        shared = [
            part
            for part in parts
            if part not in _FOCUS_DOCUMENT_TERMS and part not in _COMPARISON_NOISE
        ]
        variants.extend(" ".join((*shared, focus)) for focus in focuses[:3])
    broad = " ".join(
        part
        for part in parts
        if part not in _FOCUS_DOCUMENT_TERMS and part not in _COMPARISON_NOISE
    )
    if broad and broad != normalized:
        variants.append(broad)
    compact = _ISO_DATE.sub(" ", broad or normalized)
    compact = compact.replace(" 至 ", " ")
    compact = " ".join(term for term in compact.split() if term not in _QUERY_REFINEMENT_TERMS)
    if compact and compact != normalized:
        variants.append(compact)
    natural = _natural_query_variant(normalized)
    if natural and natural != normalized:
        variants.append(natural)
    localized = _localized_intent_variant(normalized)
    if localized and localized != normalized:
        variants.append(localized)
    return tuple(dict.fromkeys(variants))


def _localized_intent_variant(query: str) -> str:
    """Give a localized backend a compact bilingual intent rewrite."""
    value = query
    replacements = (
        (r"\bas\s+of\b", "截至"),
        (r"\b(?:current|latest)\b", "最新"),
        (r"\bstable\b", "稳定"),
        (r"\b(?:release|released|releases)\b", "发布"),
        (r"\bimplementation\b", "实施"),
        (r"\btimeline\b", "时间表"),
        (r"\bofficial\b", "官方"),
        (r"\bsource(?:s)?\b", "来源"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return " ".join(value.split())


def _document_query_variant(query: str) -> str:
    semantic = _semantic_text(query)
    documents = tuple(term for term in _FOCUS_DOCUMENT_TERMS if term in semantic)
    if not documents:
        return ""
    years = tuple(dict.fromkeys(re.findall(r"(?:19|20)\d{2}年?", query)))
    return " ".join((*years, *_entity_terms(query), *documents))


def _selected_search_queries(query: str) -> tuple[str, ...]:
    """Bound backend fan-out while preferring compact and localized rewrites."""
    variants = build_query_variants(query)
    primary = variants[0]
    localized_date = _ISO_DATE.sub(
        lambda match: f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日",
        primary,
    )
    compact = _ISO_DATE.sub(" ", primary).replace(" 至 ", " ")
    compact = " ".join(
        term for term in compact.split() if term.casefold() not in _QUERY_REFINEMENT_TERMS
    )
    localized_compact = _localized_intent_variant(compact)
    if localized_compact and _requests_primary_sources(query):
        localized_compact = f"{localized_compact} 官方"
    preferred = (
        _document_query_variant(primary),
        (
            localized_date
            if localized_date != primary and re.search(r"[\u3400-\u9fff]", primary)
            else ""
        ),
        _discovery_query_variant(primary),
        localized_compact,
        _localized_intent_variant(primary),
        _natural_query_variant(primary),
        compact,
        *variants[1:],
    )
    fallbacks = tuple(dict.fromkeys(item for item in preferred if item and item != primary))[:2]
    return (primary, *fallbacks)


def _discovery_query_variant(query: str) -> str:
    """Find the canonical topic page before enforcing detailed evidence coverage."""
    semantic = _semantic_text(query)
    entities = _high_confidence_entities(query)
    if entities and re.search(r"[a-z]", query, re.IGNORECASE):
        topic = None
        if "weather" in semantic:
            topic = "weather history" if (re.search(r"histor|past", semantic) or any(day < datetime.now(UTC).date() for day in _calendar_dates(query))) else "weather"
        elif re.search(r"\b(?:match|game|score|result)s?\b", semantic):
            topic = "results"
        else:
            topic = next((role for role in ("chief justice", "speaker", "president", "leadership") if role in semantic), None)
        if topic:
            return " ".join((*entities, topic))
    matched_topics = [topic for topic in _SEARCH_TOPIC_TERMS if topic.casefold() in semantic]
    most_specific = [
        topic
        for topic in matched_topics
        if not any(topic != other and topic in other for other in matched_topics)
    ]
    if not entities or not most_specific:
        return ""
    selected = most_specific[0]
    localized = all(re.fullmatch(r"[\u3400-\u9fff]+", item) for item in (*entities, selected))
    if localized:
        suffix = "查询" if selected.startswith("历史") else ""
        return f"{''.join(entities)}{selected}{suffix}"
    return " ".join((*entities, selected))


def _natural_query_variant(query: str) -> str:
    """Turn a conversational question into a compact search query without an LLM."""
    value = query
    entities = _high_confidence_entities(value)
    for source, target in _SEMANTIC_REPLACEMENTS:
        value = value.replace(source, target)
    for filler in _QUESTION_FILLERS:
        value = value.replace(filler, " ")
    value = re.sub(r"[？?。！!，,：:；;、]+", " ", value)
    value = re.sub(r"(?<=[A-Za-z0-9])(?=[\u3400-\u9fff])", " ", value)
    value = re.sub(r"(?<=[\u3400-\u9fff])(?=[A-Za-z0-9])", " ", value)
    value = re.sub(r"((?:19|20)\d{2})\s*年", r" \1年 ", value)
    separators = tuple(
        sorted(
            set(_HIGH_VALUE_PHRASES) | set(_SEARCH_TOPIC_TERMS),
            key=len,
            reverse=True,
        )
    )
    value = re.sub(
        "(" + "|".join(map(re.escape, separators)) + ")",
        r" \1 ",
        value,
    )
    if entities:
        value = re.sub(
            "(" + "|".join(map(re.escape, sorted(entities, key=len, reverse=True))) + ")",
            r" \1 ",
            value,
        )
    return " ".join(value.split())


def _semantic_text(value: str) -> str:
    normalized = value.casefold()
    for source, target in _SEMANTIC_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    normalized = re.sub(
        r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        lambda match: f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}",
        normalized,
    )
    return normalized


def _source_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    match = re.search(
        r"((?:19|20)\d{2})\s*[-/年]\s*([01]?\d)\s*[-/月]\s*([0-3]?\d)",
        normalized,
    )
    if match is None:
        return None
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ).isoformat()
    except ValueError:
        return None


def _inferred_page_date(result: SearchResult, page_content: str) -> str | None:
    """Infer article identity date from stable metadata near the start of an opened page."""
    identity_dates = _calendar_dates(f"{result.title}\n{result.url}")
    if identity_dates:
        return identity_dates[0].isoformat()
    # Publication headers normally precede article text. Bounding the scan avoids
    # mistaking dates in recommendations or long historical discussions for the
    # source publication date.
    lead_dates = _calendar_dates(page_content[:2000])
    return lead_dates[0].isoformat() if lead_dates else None


def _calendar_dates(value: str) -> tuple[date, ...]:
    values: list[date] = []
    for year, month, day in re.findall(
        r"((?:19|20)\d{2})\s*[-/年]\s*([01]?\d)\s*[-/月]\s*([0-3]?\d)",
        value,
    ):
        try:
            values.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for month, day, year in re.findall(
        r"(?<!\d)([01]?\d)/([0-3]?\d)/((?:19|20)\d{2})(?!\d)", value
    ):
        try:
            values.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    month_names = {
        name.casefold(): index
        for index in range(1, 13)
        for name in (calendar.month_name[index], calendar.month_abbr[index])
    }
    pattern = (
        r"\b("
        + "|".join(month_names)
        + r")\.?\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?,?\s+((?:19|20)\d{2})\b"
    )
    for month_name, first, last, year in re.findall(pattern, value, re.IGNORECASE):
        for raw_day in (first, last):
            if not raw_day:
                continue
            try:
                values.append(date(int(year), month_names[month_name.casefold()], int(raw_day)))
            except ValueError:
                continue
    return tuple(dict.fromkeys(values))


def _calendar_months(value: str) -> tuple[tuple[int, int], ...]:
    """Extract explicit year/month identities, including compact dates in article URLs."""
    values: list[tuple[int, int]] = []
    patterns = (
        r"((?:19|20)\d{2})\s*[-/年]\s*([01]?\d)(?!\s*[-/月]\s*[0-3]?\d)",
        r"(?<!\d)((?:19|20)\d{2})([01]\d)[0-3]\d(?!\d)",
    )
    for pattern in patterns:
        for year, month in re.findall(pattern, value):
            numeric_month = int(month)
            if 1 <= numeric_month <= 12:
                values.append((int(year), numeric_month))
    values.extend((item.year, item.month) for item in _calendar_dates(value))
    return tuple(dict.fromkeys(values))


def _date_markers(value: date) -> tuple[str, ...]:
    return (
        value.isoformat(),
        f"{value.year}年{value.month}月{value.day}日",
        f"{value.month}月{value.day}日",
        f"{value.month:02d}月{value.day:02d}日",
        f"{value.month:02d}/{value.day:02d}",
        f"{value.month}/{value.day}",
        f"{value.day}日",
    )


def _missing_temporal_boundaries(
    query: str,
    results: list[SearchResult],
) -> tuple[date, ...]:
    requested = _calendar_dates(query)
    if len(requested) < 2 or re.search(
        r"(?:最新|最近|动态|新闻|截至|截止|\bas\s+of\b|\blatest\b|\bcurrent\b)",
        query,
        re.IGNORECASE,
    ):
        return ()
    required = tuple(dict.fromkeys((min(requested), max(requested))))
    evidence_text = _semantic_text(
        "\n".join(f"{item.title}\n{item.snippet}\n{item.url}" for item in results)
    )
    return tuple(
        item
        for item in required
        if not any(_semantic_text(marker) in evidence_text for marker in _date_markers(item))
    )


def _temporal_interval_sufficient(query: str, results: list[SearchResult]) -> bool:
    """Accept live interval pages without pretending that a snippet proves every day."""
    missing = _missing_temporal_boundaries(query, results)
    if not missing:
        return True
    requested = _calendar_dates(query)
    if len(requested) < 2:
        return False
    today = datetime.now(UTC).date()
    start, end = min(requested), max(requested)
    if end < today or re.search(r"(?:历史|过去|上周|昨日|昨天|前天)", query):
        return False
    evidence_text = _semantic_text(
        "\n".join(f"{item.title}\n{item.snippet}\n{item.url}" for item in results)
    )
    if not re.search(
        r"(?:逐日|未来|预报|趋势|日历|时间表|7天|七天|一周|"
        r"daily|forecast|schedule|timeline)",
        evidence_text,
        re.IGNORECASE,
    ):
        return False
    covered: set[date] = set(_calendar_dates(evidence_text))
    for slash_month, slash_day, zh_month, zh_day in re.findall(
        r"(?<!\d)([01]?\d)[/-]([0-3]?\d)(?!\d)|"
        r"(?<!\d)([01]?\d)月([0-3]?\d)日",
        evidence_text,
    ):
        raw_month = slash_month or zh_month
        raw_day = slash_day or zh_day
        try:
            candidate = date(start.year, int(raw_month), int(raw_day))
        except ValueError:
            continue
        if start <= candidate <= end:
            covered.add(candidate)
    return start in covered and len(covered) >= min(3, (end - start).days + 1)


def _focused_temporal_queries(query: str, missing: tuple[date, ...]) -> tuple[str, ...]:
    base = _ISO_DATE.sub(" ", query)
    base = re.sub(r"(?:\s+至\s+|\bto\b)", " ", base, flags=re.IGNORECASE)
    base = " ".join(base.split())
    localized = bool(re.search(r"[\u3400-\u9fff]", query))
    return tuple(
        f"{base} {item.year}年{item.month}月{item.day}日"
        if localized
        else f"{base} {item.isoformat()}"
        for item in missing
    )


def temporal_compatible(
    query: str,
    result: SearchResult,
    *,
    allow_undated_historical: bool = False,
) -> bool:
    """Reject explicit conflicting periods while retaining genuinely undated live pages."""
    requested_dates = _calendar_dates(query)
    requested_months = set(_calendar_months(query))
    requested_years = set(re.findall(r"(?:19|20)\d{2}", query))
    # observed_at proves when the platform retrieved a result; it is not a date
    # asserted by the source and must never make stale source content look current.
    source_snippet = re.sub(r"(?m)^检索时间：[^\n]*(?:\n|$)", "", result.snippet)
    searchable = f"{result.title}\n{source_snippet}\n{result.url}"
    result_dates = _calendar_dates(searchable)
    result_years = set(re.findall(r"(?:19|20)\d{2}", searchable))
    stable_identity = f"{result.title}\n{result.url}"
    identity_years = set(re.findall(r"(?:19|20)\d{2}", stable_identity))
    # A maintained, opened first-party office page often mentions an older
    # accession/election year. That historical date is not the page's validity
    # period; rejecting it here erased the current name from the evidence.
    current_role_page = (
        not requested_dates
        and requested_years <= {str(datetime.now(UTC).year)}
        and _current_role_hint_match(query, result)
    )
    if current_role_page:
        return True
    current_period_request = bool(
        re.search(
            r"(?:最新|最近|当前|现在|今日|今天|新闻|动态|消息|进展|"
            r"latest|recent|current|today|news|update)",
            query,
            re.IGNORECASE,
        )
    )
    source_identity = stable_identity
    if result.published_at is not None:
        source_identity = f"{source_identity}\n{result.published_at}"
    identity_months = set(_calendar_months(source_identity))
    # A live landing page can support a query for today, but must never be used
    # as evidence for yesterday or another nearby historical day merely because
    # it is within an arbitrary tolerance window.
    near_current_request = datetime.now(UTC).date() in requested_dates
    dynamic_current_page = (
        near_current_request
        and result.published_at is None
        and not identity_years
        and bool(_DYNAMIC_SOURCE_CUE.search(searchable))
    )

    # A point-in-time question asks for the newest fact known *by* a cutoff,
    # not for a page published on that exact day. Stable official landing
    # pages and earlier release/policy pages therefore remain eligible, while
    # sources dated after the cutoff are rejected. Exact-day event/history
    # queries keep the stricter matching behavior below.
    as_of_query = bool(re.search(r"(?:截至|截止|as\s+of|\bcurrent\b)", query, re.IGNORECASE))
    if as_of_query and requested_dates:
        cutoff = max(requested_dates)
        if result.published_at is not None:
            try:
                if date.fromisoformat(result.published_at) > cutoff:
                    return False
            except ValueError:
                pass
        explicit_identity_dates = _calendar_dates(stable_identity)
        return not (explicit_identity_dates and min(explicit_identity_dates) > cutoff)

    # For current-information questions scoped to an explicit month, an article's
    # own publication/URL month is authoritative routing metadata. Older articles
    # may contain matching keywords, but cannot establish what happened later.
    if (
        current_period_request
        and requested_months
        and identity_months
        and requested_months.isdisjoint(identity_months)
    ):
        return False

    if (
        len(requested_dates) == 1
        and requested_dates[0] != datetime.now(UTC).date()
        and not result_dates
    ):
        requested = requested_dates[0]
        semantic_source = _semantic_text(searchable)
        has_month_day = any(
            _semantic_text(marker) in semantic_source for marker in _date_markers(requested)[2:]
        )
        if not has_month_day:
            return allow_undated_historical

    if requested_dates and identity_years - requested_years:
        return False

    if requested_dates and result_dates:
        start, end = min(requested_dates), max(requested_dates)
        if not any(start <= item <= end for item in result_dates) and not dynamic_current_page:
            return False
    if (
        requested_years
        and result_years
        and requested_years.isdisjoint(result_years)
        and not dynamic_current_page
    ):
        return False
    return not (
        requested_years
        and result.published_at is not None
        and not result_dates
        and result.published_at[:4] not in requested_years
    )


def _query_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    pattern = r"20\d{2}-\d{2}-\d{2}|[\w\u3400-\u9fff]+"
    for raw_value in re.findall(pattern, _semantic_text(query)):
        value = raw_value.strip("_")
        if len(value) < 2:
            continue
        if _ISO_DATE.fullmatch(value):
            terms.append(value)
            continue
        terms.extend(re.findall(r"(?:19|20)\d{2}", value))
        residual = re.sub(r"(?:19|20)\d{2}年?", " ", value).strip(" -_")
        matched_phrases = [phrase for phrase in _HIGH_VALUE_PHRASES if phrase in residual]
        entity = residual
        for phrase in matched_phrases:
            entity = entity.replace(phrase, " ")
        entity = entity.strip(" -_")
        if entity and entity not in _GENERIC_TERMS:
            terms.append(entity)
        terms.extend(matched_phrases)
        if len(value) >= 4 and value[-1:] in {"区", "市", "县", "省"}:
            terms.extend((value[-3:], value[-3:-1]))
    terms.extend(_high_confidence_entities(query))
    return tuple(dict.fromkeys(terms))


def _term_weight(term: str) -> float:
    if _ISO_DATE.fullmatch(term) or re.fullmatch(r"(?:19|20)\d{2}", term):
        return 5.0
    if term in _HIGH_VALUE_PHRASES:
        return 3.5
    if re.fullmatch(r"[\u3400-\u9fff]{2,10}", term) and term not in {
        "天气",
        "天气预报",
        "新闻",
    }:
        return 3.0
    return 1.5


def _entity_terms(query: str) -> tuple[str, ...]:
    return _high_confidence_entities(query)


def _entity_aliases(entity: str) -> tuple[str, ...]:
    """Return conservative aliases for administrative-name spelling differences."""
    aliases = [entity]
    if entity in {"united", "states"}:
        aliases.extend(("u.s.", "usa", "美国"))
    compact = re.sub(r"(?:特别行政区|自治区|自治州|省|市|区|县|镇|州)$", "", entity)
    if len(compact) >= 2 and compact != entity:
        aliases.append(compact)
    return tuple(dict.fromkeys(aliases))


def _entity_matches(entity: str, text: str, compact_hostname: str) -> bool:
    if entity in {"united", "states"} and compact_hostname.endswith("gov"):
        return True
    for alias in _entity_aliases(entity):
        if alias in text:
            return True
        compact_alias = re.sub(r"[^a-z0-9]", "", alias)
        if compact_alias and compact_alias in compact_hostname:
            return True
    return False


def _topic_terms(query: str) -> tuple[str, ...]:
    entities = set(_high_confidence_entities(query))
    topics: list[str] = []
    for role in ("chief justice", "president", "speaker", "leadership"):
        if role in query.casefold():
            topics.append(role)
    if re.search(
        r"\b(?:weather|forecast|temperature|rainfall|precipitation)\b", query, re.IGNORECASE
    ):
        topics.append("weather")
    for token in _natural_query_variant(query).casefold().split():
        token = re.sub(r"^(?:最新|最近|当前|目前)", "", token)
        token = re.sub(r"(?:最新|最近|当前|目前)$", "", token)
        token = token.rstrip("的了")
        if (
            len(token) < 2
            or token.isdigit()
            or re.fullmatch(r"\d{1,2}[-–/]\d{1,2}", token)
            or token in entities
            or token in _ENTITY_NOISE
            or token in _TEMPORAL_TOPIC_NOISE
            or token in _GENERIC_TERMS
            or _ISO_DATE.fullmatch(token)
            or re.fullmatch(r"(?:19|20)\d{2}年?", token)
        ):
            continue
        topics.append(token)
    return tuple(dict.fromkeys(topics))


def _topic_matches(topic: str, text: str) -> bool:
    if topic in text or _cjk_overlap(topic, text) >= 0.35:
        return True
    return any(
        any(cue in topic for cue in query_cues) and any(cue in text for cue in evidence_cues)
        for query_cues, evidence_cues in _TOPIC_FAMILIES
    )


def _high_confidence_entities(query: str) -> tuple[str, ...]:
    """Extract only entities safe enough to use as hard relevance constraints."""
    semantic = _semantic_text(query)
    without_dates = re.sub(r"(?:19|20)\d{2}(?:年|-[01]\d-[0-3]\d)?", " ", semantic)
    without_dates = re.sub(
        r"^(?:今天|明天|昨天|前天|后天|未来(?:一周|几天)?|上周[一二三四五六日天]?|本周[一二三四五六日天]?)",
        "",
        without_dates,
    )
    entities: list[str] = []
    office_roles = r"(?:首相|总理|总统|天皇|国王|女王|最高领导人)"
    for match in re.finditer(rf"([\u3400-\u9fff]{{2,8}}?){office_roles}", without_dates):
        subject = re.sub(r"^(?:现任|当前|现在|目前|今年)", "", match.group(1))
        if len(subject) >= 2:
            entities.append(subject)
    entities.extend(
        match.group(0)
        for match in re.finditer(
            r"(?<![a-z0-9_.])[a-z][a-z0-9._+-]{1,}(?![a-z0-9_.])",
            semantic,
        )
        if match.group(0) not in _ENTITY_NOISE
    )
    entities.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:^|[^\u3400-\u9fff])([\u3400-\u9fff]{1,8}?(?:省|市|区|县|镇|国|州(?!市)))",
            without_dates,
        )
    )
    for chunk in without_dates.split():
        if not re.fullmatch(r"[\u3400-\u9fff]{2,8}", chunk):
            continue
        if (
            chunk in _ENTITY_NOISE
            or chunk in _COMPARISON_NOISE
            or re.search(office_roles, chunk)
            or any(term in chunk for term in _SEARCH_TOPIC_TERMS)
        ):
            continue
        if any(term in chunk for term in _HIGH_VALUE_PHRASES):
            continue
        entities.append(chunk)
    leading = re.split("|".join(map(re.escape, _ENTITY_BREAK_TERMS)), without_dates, maxsplit=1)[0]
    leading = re.sub(r"[^a-z0-9\u3400-\u9fff._+-]", "", leading)
    if (
        re.fullmatch(r"[\u3400-\u9fff]{2,8}", leading)
        and leading not in _ENTITY_NOISE
        and leading not in _COMPARISON_NOISE
        and not re.search(office_roles, leading)
        and not any(term in leading for term in _SEARCH_TOPIC_TERMS)
        and not any(term in leading for term in _HIGH_VALUE_PHRASES)
    ):
        entities.append(leading)
    unique = tuple(dict.fromkeys(entity for entity in entities if entity))
    return tuple(
        entity
        for entity in unique
        if not any(entity != other and other.startswith(entity) for other in unique)
    )


def _cjk_overlap(term: str, text: str) -> float:
    if not re.fullmatch(r"[\u3400-\u9fff]{4,}", term):
        return 0.0
    grams = {term[index : index + 2] for index in range(len(term) - 1)}
    if not grams:
        return 0.0
    return sum(gram in text for gram in grams) / len(grams)


def _relevance_score(query: str, result: SearchResult) -> float:
    title = _semantic_text(result.title)
    text = _semantic_text(f"{title}\n{result.snippet}")
    score = 0.0
    for term in _query_terms(query):
        weight = _term_weight(term)
        if term in text:
            score += weight
            if term in title:
                score += weight
        elif (overlap := _cjk_overlap(term, text)) >= 0.35:
            score += weight * overlap
        elif weight >= 3.0:
            score -= weight
    requested_years = set(re.findall(r"(?:19|20)\d{2}", query))
    title_years = set(re.findall(r"(?:19|20)\d{2}", title))
    range_years: set[str] = set()
    for start, end in re.findall(
        r"((?:19|20)\d{2})\s*(?:[-~—–至到])\s*((?:19|20)\d{2})",
        title,
    ):
        if start in requested_years or end in requested_years:
            range_years.update((start, end))
    conflicting_years = (title_years - requested_years - range_years) if requested_years else set()
    score -= 8.0 * len(conflicting_years)
    hostname = (urlsplit(result.url).hostname or "").casefold()
    compact_hostname = re.sub(r"[^a-z0-9]", "", hostname)
    for entity in _entity_terms(query):
        if not _entity_matches(entity, title, compact_hostname):
            score -= 8.0
    for term in _query_terms(query):
        if re.fullmatch(r"[a-z][a-z0-9.-]+", term) and term in hostname.casefold():
            score += 1.5
    if hostname.endswith((".gov", ".gov.cn", ".edu", ".edu.cn")) or re.search(
        r"(?:^|\.)(?:go|gov|gouv)\.[a-z]{2,}$", hostname
    ):
        score += 4.0
    score += _source_specialization_score(query, result)
    if _primary_source(query, result):
        score += 6.0
        current_request = bool(
            re.search(
                r"(?:最新|当前|截至|\bcurrent\b|\blatest\b|\bas\s+of\b)",
                query,
                re.IGNORECASE,
            )
        )
        path = urlsplit(result.url).path.strip("/")
        shallow_current_hub = (
            current_request
            and len([part for part in path.split("/") if part]) <= 2
            and _semantic_version(result.title) is None
            and not _calendar_dates(f"{result.title}\n{result.url}")
        )
        if shallow_current_hub:
            score += 8.0
    return score


def _source_specialization_score(query: str, result: SearchResult) -> float:
    """Prefer a source whose public identity matches the requested information class."""
    semantic_query = _semantic_text(query)
    parsed = urlsplit(result.url)
    identity = _semantic_text(f"{result.title}\n{parsed.hostname or ''}\n{parsed.path}")
    score = 0.0
    if _authoritative_specialist(query, result):
        score += 14.0
    families = (
        (
            ("天气", "气温", "温度", "降雨", "降水", "台风", "weather"),
            ("天气", "气象", "weather", "forecast", "tianqi", "qixiang"),
        ),
        (
            ("新闻", "动态", "消息", "进展", "事件", "news"),
            ("新闻", "报道", "news", "press", "release", "updates"),
        ),
        (
            ("股价", "股票", "汇率", "价格", "报价", "市值", "指数"),
            ("财经", "金融", "行情", "market", "finance", "quote", "investor"),
        ),
        (
            ("政策", "法规", "条例", "标准", "公告", "诉讼", "法律"),
            ("政策", "法规", "条例", "标准", "公告", "法院", "law", "legal", "policy"),
        ),
    )
    for query_cues, source_cues in families:
        if any(cue in semantic_query for cue in query_cues):
            score += 4.0 if any(cue in identity for cue in source_cues) else -2.0
            break
    if re.search(r"(?:新闻|动态|消息|进展|事件|news)", semantic_query, re.IGNORECASE):
        score += _editorial_source_score(result)
        if _listing_page(result):
            score -= 12.0
    current_or_future = bool(
        re.search(
            r"(?:今天|今日|明天|未来|最新|当前|现在|实时|"
            r"today|tomorrow|future|latest|current|live)",
            semantic_query,
            re.IGNORECASE,
        )
    )
    if current_or_future and re.search(
        r"(?:历史|往年|回顾|archive|history|lishi)",
        identity,
        re.IGNORECASE,
    ):
        score -= 12.0
    if result.published_at is not None and re.search(
        r"(?:新闻|动态|消息|进展|事件|news)",
        semantic_query,
        re.IGNORECASE,
    ):
        score += 2.0
    freshness_requested = bool(
        re.search(
            r"(?:最新|最近|当前|现在|实时|今日|今天|"
            r"latest|recent|current|today|live)",
            semantic_query,
            re.IGNORECASE,
        )
    )
    if freshness_requested:
        dated_identity = _calendar_dates(f"{result.title}\n{result.url}")
        if result.published_at is not None:
            try:
                dated_identity = (*dated_identity, date.fromisoformat(result.published_at))
            except ValueError:
                pass
        if dated_identity:
            age_days = (datetime.now(UTC).date() - max(dated_identity)).days
            if 0 <= age_days <= 7:
                score += 8.0
            elif age_days <= 31:
                score += 5.0
            elif age_days <= 90:
                score += 1.0
            elif age_days <= 365:
                score -= 6.0
            elif age_days > 365:
                score -= 12.0
    return score


def _editorial_source_score(result: SearchResult) -> float:
    """Apply a bounded publisher prior without overriding relevance or freshness."""
    parsed = urlsplit(result.url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")

    def matches(known: str) -> bool:
        return hostname == known or hostname.endswith("." + known)

    if any(matches(known) for known in _ESTABLISHED_EDITORIAL_HOSTS):
        return 9.0
    if any(matches(known) for known in _USER_GENERATED_HOSTS):
        return -7.0
    # Large portals mix newsroom reporting with self-published channels. Penalize
    # only the channel-shaped paths; their edited newsroom pages remain neutral.
    if (matches("163.com") and parsed.path.startswith("/dy/")) or (
        matches("sohu.com") and re.match(r"/(?:a|mil)/", parsed.path)
    ):
        return -5.0
    return 0.0


def _publisher_identity(value: str) -> str:
    """Return the publisher, not a newsroom subdomain, for diversity checks."""
    hostname = (urlsplit(value).hostname or value).casefold().removeprefix("www.")
    for known in (*_ESTABLISHED_EDITORIAL_HOSTS, *_USER_GENERATED_HOSTS):
        if hostname == known or hostname.endswith("." + known):
            return known
    labels = tuple(part for part in hostname.split(".") if part)
    if (
        len(labels) >= 3
        and len(labels[-1]) == 2
        and labels[-2]
        in {
            "ac",
            "co",
            "com",
            "edu",
            "gov",
            "net",
            "org",
        }
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else hostname


def _listing_page(result: SearchResult) -> bool:
    """Identify discovery hubs that are useful for leads but weak as final evidence."""
    parsed = urlsplit(result.url)
    identity = _semantic_text(f"{result.title}\n{parsed.path}")
    return bool(
        re.search(
            r"(?:24小时|滚动更新|实时动态|最新消息|专题|标签|"
            r"topic|topics|tag|tags|search|live[-_ ]?updates?)",
            identity,
            re.IGNORECASE,
        )
    )


def _authoritative_specialist(query: str, result: SearchResult) -> bool:
    semantic_query = _semantic_text(query)
    hostname = (urlsplit(result.url).hostname or "").casefold().removeprefix("www.")
    return any(
        any(cue in semantic_query for cue in query_cues)
        and any(hostname == host or hostname.endswith("." + host) for host in hosts)
        for query_cues, hosts in _AUTHORITATIVE_SPECIALISTS
    )


def rank_results(
    query: str,
    results: list[SearchResult],
    limit: int,
) -> list[SearchResult]:
    indexed = list(enumerate(results))
    latest_requested = bool(
        re.search(
            r"(?:最新|当前|截至|\bcurrent\b|\blatest\b|\bas\s+of\b)",
            query,
            re.IGNORECASE,
        )
    )
    versions = [_semantic_version(item.title) for item in results]
    newest_version = max((item for item in versions if item is not None), default=None)
    indexed.sort(
        key=lambda item: (
            -(
                _relevance_score(query, item[1])
                + (6.0 if _primary_source(query, item[1]) else 0.0)
                + (
                    5.0
                    if latest_requested
                    and newest_version is not None
                    and versions[item[0]] == newest_version
                    else 0.0
                )
            ),
            item[0],
        ),
    )
    return [item for _, item in indexed[:limit]]


def _semantic_version(value: str) -> tuple[int, int, int] | None:
    versions = [
        (int(major), int(minor), int(patch or 0))
        for major, minor, patch in re.findall(
            r"(?<![\d.-])(\d{1,2})\.(\d{1,3})(?:\.(\d{1,4}))?(?![\d.-])",
            value,
        )
    ]
    return max(versions, default=None)


def rank_diverse_results(
    query: str,
    results: list[SearchResult],
    limit: int,
    *,
    max_per_host: int = 2,
) -> list[SearchResult]:
    ranked = rank_results(query, results, len(results))
    selected: list[SearchResult] = []
    deferred: list[SearchResult] = []
    host_counts: dict[str, int] = {}
    for item in ranked:
        publisher = _publisher_identity(item.url)
        if host_counts.get(publisher, 0) >= max_per_host:
            deferred.append(item)
            continue
        selected.append(item)
        host_counts[publisher] = host_counts.get(publisher, 0) + 1
        if len(selected) == limit:
            return selected
    selected.extend(deferred[: limit - len(selected)])
    return selected


def _primary_source(query: str, result: SearchResult) -> bool:
    hostname = (urlsplit(result.url).hostname or "").casefold()
    entities = _entity_terms(query)
    institutional = hostname.endswith((".gov", ".gov.cn", ".edu", ".edu.cn")) or bool(
        re.search(r"(?:^|\.)(?:go|gov|gouv)\.[a-z]{2,}$", hostname)
    )
    source_text = _semantic_text(f"{result.title}\n{result.snippet}\n{result.url}")
    topics = _topic_terms(query)
    topic_relevant = (
        _current_role_hint_match(query, result)
        or not topics
        or sum(_topic_matches(topic, source_text) for topic in topics)
        >= max(1, (len(topics) + 1) // 2)
    )
    if _authoritative_specialist(query, result) and topic_relevant:
        return True
    entity_domain = any(
        re.fullmatch(r"[a-z][a-z0-9._+-]{1,}", entity) and _credible_entity_domain(hostname, entity)
        for entity in entities
    )
    if not _requests_primary_sources(query):
        return (institutional and topic_relevant) or entity_domain
    latin_entities = tuple(
        entity for entity in entities if re.fullmatch(r"[a-z][a-z0-9._+-]{1,}", entity)
    )
    if latin_entities:
        return entity_domain
    return (
        institutional
        and topic_relevant
        and bool(entities)
        and all(entity in source_text for entity in entities)
    )


def _credible_entity_domain(hostname: str, entity: str) -> bool:
    labels = hostname.removeprefix("www.").split(".")
    if len(labels) < 2:
        return False
    public_suffix = labels[-1]
    common_second_level = {"ac", "co", "com", "edu", "gov", "net", "org"}
    stem_index = -3 if len(labels) >= 3 and labels[-2] in common_second_level else -2
    stem = re.sub(r"[^a-z0-9]", "", labels[stem_index])
    compact_entity = re.sub(r"[^a-z0-9]", "", entity.casefold())
    organizational_suffix = len(public_suffix) == 2 or public_suffix in {
        "ai",
        "cloud",
        "com",
        "dev",
        "eu",
        "int",
        "io",
        "net",
        "org",
    }
    return bool(
        compact_entity
        and organizational_suffix
        and (
            stem == compact_entity
            or (
                len(compact_entity) <= 3
                and public_suffix == compact_entity
                and stem.startswith(compact_entity)
            )
        )
    )


def _requests_primary_sources(query: str) -> bool:
    return bool(re.search(r"(?:官方|\bofficial\b)", query, re.IGNORECASE))


def _requests_current_events(query: str) -> bool:
    semantic = _semantic_text(query)
    current = bool(
        re.search(
            r"(?:最新|最近|当前|现在|今日|今天|本周|latest|recent|current|today)",
            semantic,
            re.IGNORECASE,
        )
    )
    event = bool(
        re.search(
            r"(?:新闻|动态|消息|进展|事件|报道|比赛|比分|news|update|event|match|game|score|result)",
            semantic,
            re.IGNORECASE,
        )
    )
    return current and event


def _requests_current_officeholder(query: str) -> bool:
    semantic = _semantic_text(query)
    current_year = str(datetime.now(UTC).year)
    current_cue = bool(
        re.search(r"(?:当前|现在|现任|目前|current|incumbent)", semantic, re.IGNORECASE)
        or (
            re.search(rf"(?<!\d){current_year}(?:年)?(?!\d)", semantic)
            and not re.search(
                r"(?:当时|此前|曾任|former|previous|as of|截至)", semantic, re.IGNORECASE
            )
        )
    )
    return bool(
        current_cue
        and re.search(
            r"(?:首相|总理|总统|天皇|国王|女王|国家元首|政府首脑|"
            r"prime minister|president|emperor|monarch|head of state|head of government)",
            semantic,
            re.IGNORECASE,
        )
    )


def _official_role_source_hints(query: str) -> tuple[tuple[str, str], ...]:
    """Suggest maintained first-party role pages, never an incumbent's name.

    Search engines frequently return a ministry homepage that lacks the actual
    person's name. These pages are fetched and checked like any other public
    candidate; they are not an answer or a trusted instruction.
    """
    if not _requests_current_officeholder(query):
        return ()
    semantic = _semantic_text(query)
    pages: list[tuple[str, str]] = []
    if re.search(r"(?:日本|japan)", semantic, re.IGNORECASE):
        if re.search(r"(?:天皇|emperor)", semantic, re.IGNORECASE):
            pages.append(
                (
                    "日本天皇（宫内厅官方人物资料）",
                    "https://www.kunaicho.go.jp/en/learn/about/history/history01.html",
                )
            )
        if re.search(r"(?:首相|总理|prime minister)", semantic, re.IGNORECASE):
            pages.append(
                (
                    "日本首相（首相官邸现职与历代内阁）",
                    "https://www.kantei.go.jp/jp/rekidainaikaku/",
                )
            )
    if re.search(r"(?:澳大利亚|australia)", semantic, re.IGNORECASE):
        if re.search(
            r"(?:总统|总理|首相|政府首脑|prime minister|president)", semantic, re.IGNORECASE
        ):
            pages.append(
                ("澳大利亚总理（官方人物资料）", "https://www.pm.gov.au/about-prime-minister")
            )
        if re.search(
            r"(?:总统|国家元首|总督|head of state|president|governor.general)",
            semantic,
            re.IGNORECASE,
        ):
            pages.append(
                (
                    "澳大利亚国家元首与总督（官方公民制度资料）",
                    "https://www.gg.gov.au/about-governor-general/civics-and-democracy",
                )
            )
    return tuple(pages)


def _current_role_hint_match(query: str, result: SearchResult) -> bool:
    """A live first-party page can explain a mistaken office title (e.g. president)."""
    return bool(
        _requests_current_officeholder(query)
        and "网页正文摘录：" in result.snippet
        and result.url.removesuffix("/index.html").rstrip("/")
        in {
            url.removesuffix("/index.html").rstrip("/")
            for _title, url in _official_role_source_hints(query)
        }
    )


async def _opened_sports_result_hints(query: str, client: httpx.AsyncClient) -> list[SearchResult]:
    """Discover a team's current completed-results links without fixed team/game IDs."""
    if not re.search(r"\b(?:match|game|score|result)s?\b", query, re.IGNORECASE):
        return []
    subject = re.split(r"\bvs\.?\b|\bversus\b", query, flags=re.IGNORECASE)[0]
    terms = _high_confidence_entities(subject)
    if not terms or not all(re.fullmatch(r"[a-z0-9 -]+", term, re.IGNORECASE) for term in terms):
        return []
    team_cache_key = _search_cache_key("espn-completed", SearchRequest(query=" ".join(terms), limit=3))
    cached = _cached_results(team_cache_key)
    if cached is not None:
        return cached
    try:
        response = await client.get(
            "https://site.web.api.espn.com/apis/search/v2",
            params={"query": " ".join(terms), "limit": 3, "type": "team"},
            timeout=4, follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    teams = [item for group in payload.get("results", []) if isinstance(group, dict)
             and group.get("type") == "team" for item in group.get("contents", [])
             if isinstance(item, dict)]
    for team in teams[:3]:
        name = str(team.get("displayName", ""))
        if not all(term.casefold() in name.casefold() for term in terms):
            continue
        if set(_high_confidence_entities(name)) - set(terms):
            continue  # Never switch to a reserve/youth team when the senior lookup fails.
        link = team.get("link")
        url = link.get("web") if isinstance(link, dict) else None
        if not isinstance(url, str) or urlsplit(url).hostname != "www.espn.com":
            continue
        page = await fetch_public_page(client, url)
        if page is None:
            continue
        reports = tuple(dict.fromkeys(
            href for href, label in page.source_links
            if re.search(r"\bFT\b|\bFinal\b", label, re.IGNORECASE)
            and urlsplit(href).hostname == "www.espn.com"
            and re.search(r"report|matchstats|game", urlsplit(href).path, re.IGNORECASE)
        ))[:3]
        opened = await asyncio.gather(*(fetch_public_page(client, href) for href in reports))
        results = []
        for report in opened:
            if report is None:
                continue
            observed = datetime.now(UTC).isoformat()
            title = report.content.splitlines()[0]
            snippet = f"{title}\n检索时间：{observed}\n\n网页正文摘录：\n{report.content}"
            results.append(SearchResult(title=title[:500], url=report.url, snippet=snippet,
                observed_at=observed, content_hash=hashlib.sha256(snippet.encode()).hexdigest()))
        if results:
            _store_cached_results(team_cache_key, results)
            return results
    return []


async def _opened_official_forecast_hints(query: str, client: httpx.AsyncClient) -> list[SearchResult]:
    """Resolve Chinese forecast locations through the meteorological site's own directory."""
    if not re.search(r"天气|预报|气温", query) or re.search(r"历史|上周|过去|去年", query):
        return []
    cleaned = re.sub(r"(?:19|20)\d{2}(?:年|[-/])\d{1,2}(?:月|[-/])\d{1,2}日?", "", query)
    cleaned = re.sub(r"明天|今天|后天|未来|官方|查询|\s|[（）()]", "", cleaned)
    match = re.search(r"([\u3400-\u9fff]{2,8}?)(?:的)?(?:天气|预报|气温)", cleaned)
    if match is None:
        return []
    place = match[1]
    try:
        response = await client.get("https://www.nmc.cn/essearch/api/autocomplete", params={"q": place, "limit": 10}, timeout=4, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    urls: list[tuple[str, str]] = []
    for raw in payload.get("data", [])[:10] if isinstance(payload, dict) else []:
        fields = raw.split("|") if isinstance(raw, str) else []
        if len(fields) < 4 or not re.fullmatch(r"/A[A-Z]{2}/[a-z0-9_-]+\.html", fields[3]):
            continue
        station_place = re.sub(r"[（）()\s]", "", fields[1])
        if set(place) != set(station_place):
            continue  # The directory includes fuzzy suggestions; never silently change places.
        urls.append((fields[1], "https://www.nmc.cn/publish/forecast" + fields[3]))
    pages = await asyncio.gather(*(fetch_public_page(client, url) for _, url in urls[:2]))
    results = []
    for (name, _url), page in zip(urls[:2], pages, strict=True):
        if page is None:
            continue
        observed = datetime.now(UTC).isoformat()
        snippet = f"{name}天气预报\n检索时间：{observed}\n\n网页正文摘录：\n{page.content}"
        results.append(SearchResult(title=f"{name}天气预报", url=page.url, snippet=snippet, observed_at=observed, content_hash=hashlib.sha256(snippet.encode()).hexdigest()))
    return results


async def _opened_official_role_hints(query: str, client: httpx.AsyncClient) -> list[SearchResult]:
    hints = _official_role_source_hints(query)
    if not hints:
        return []
    opened = await asyncio.gather(
        *(fetch_public_page(client, url) for _title, url in hints),
        return_exceptions=True,
    )
    results: list[SearchResult] = []
    for (title, url), page in zip(hints, opened, strict=True):
        if not isinstance(page, FetchResponse):
            continue
        excerpt = page.content
        if urlsplit(url).hostname == "www.gg.gov.au":
            # A year-scoped search otherwise selects dated event links and cuts
            # out the constitutional role sentence that answers the question.
            role_line = next(
                (
                    line.strip()
                    for line in page.content.splitlines()
                    if "Governor-General of Australia" in line and "Head of State" in line
                ),
                "",
            )
            if role_line:
                excerpt = f"{role_line[:450]}\n{excerpt}"[:1800]
        if not excerpt:
            continue
        observed = datetime.now(UTC).isoformat()
        snippet = (
            f"{title}\n来源发布日期：未提供\n检索时间：{observed}\n\n网页正文摘录：\n{excerpt}"
        )
        results.append(
            SearchResult(
                title=title,
                url=page.url,
                snippet=snippet,
                observed_at=observed,
                content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
            )
        )
    return results


def _news_subject_query(query: str) -> str:
    if _requests_current_officeholder(query):
        value = re.sub(
            r"(?:是谁|谁是|叫什么|哪一位|哪位|何人|\bwho\s+is\b)", " ", query, flags=re.IGNORECASE
        )
        value = re.sub(rf"(?<!\d){datetime.now(UTC).year}(?:年)?(?!\d)", " ", value)
        return " ".join(value.split())
    entities = _entity_terms(query)
    if entities:
        return " ".join(entities)
    value = re.sub(
        r"(?:\u6700\u65b0|\u6700\u8fd1|\u5f53\u524d|\u73b0\u5728|"
        r"\u65b0\u95fb|\u52a8\u6001|\u6d88\u606f|\u8fdb\u5c55|"
        r"latest|recent|current|news|updates?)",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    return " ".join(value.split())


def _rss_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return _source_date(value)


def _bing_news_target(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.hostname in {"bing.com", "www.bing.com"}:
        targets = parse_qs(parsed.query).get("url", [])
        if targets:
            return _safe_public_url(targets[0])
    return _safe_public_url(value)


async def _search_news_rss(
    query: str, client: httpx.AsyncClient, *, limit: int = 12
) -> list[SearchResult]:
    """Retrieve dated news candidates from a public news vertical within this Provider."""
    subject = _news_subject_query(query)
    if not subject:
        return []
    try:
        response = await client.get(
            "https://www.bing.com/news/search",
            params={
                "q": subject, "format": "rss",
                "setlang": "zh-cn" if re.search(r"[\u3400-\u9fff]", query) else "en-us",
                "mkt": "zh-CN" if re.search(r"[\u3400-\u9fff]", query) else "en-US",
            },
            headers={"User-Agent": "Mozilla/5.0 (compatible; EnterpriseAgentResearch/1.0)"},
            timeout=8,
            follow_redirects=True,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError, OSError):
        return []
    observed_at = datetime.now(UTC).isoformat()
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        target = _bing_news_target(item.findtext("link"))
        description = extract_visible_text(item.findtext("description") or "")
        published_at = _rss_date(item.findtext("pubDate"))
        if not title or target is None or target in seen or not description:
            continue
        seen.add(target)
        temporal_note = (
            f"\u6765\u6e90\u53d1\u5e03\u65e5\u671f\uff1a{published_at}\n"
            f"\u68c0\u7d22\u65f6\u95f4\uff1a{observed_at}"
        )
        snippet = f"{title[:500]}\n{temporal_note}\n{description[:1200]}"
        results.append(
            SearchResult(
                title=title[:500],
                url=target,
                snippet=snippet,
                published_at=published_at,
                observed_at=observed_at,
                content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
            )
        )
    return diverse_pool(results, limit, lambda item: item.engines)


def _corroboration_query(result: SearchResult) -> str:
    """Turn one discovered event headline into a bounded verification query."""
    title = re.split(r"\s+[|_-]\s+", result.title, maxsplit=1)[0]
    title = re.sub(r"[\[【（(].{0,30}?[\]】）)]", " ", title)
    title = re.sub(r"(?:最新动态|最新消息|最新新闻|滚动更新)", " ", title)
    title = re.sub(r"[：:，,。！!？?]+", " ", title)
    return " ".join(title.split())[:160]


def _event_verification_queries(
    query: str,
    results: list[SearchResult],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """Extract concrete event leads from opened current-event pages.

    Search result pages and rolling topic hubs are discovery inputs, not proof.  A
    second query for each concrete headline gives the final ranking stage a chance
    to find an article, press release, or independent editorial report about the
    same event.
    """
    entities = _entity_terms(query)
    candidates: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    position = 0
    discard = re.compile(
        r"(?:返回首页|加载更多|查看更多|自动更新|正在读取|下载客户端|"
        r"登录|反馈|许可证|版权所有|24小时滚动|实时动态|最新消息)$",
        re.IGNORECASE,
    )
    dated = re.compile(
        r"(?:20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}天前|"
        r"\d{1,2}小时前|今天|昨日|昨天)",
        re.IGNORECASE,
    )
    event_cues = re.compile(
        r"(?:表示|宣布|称|发布|回应|决定|签署|会晤|访问|获批|拒绝|"
        r"启动|结束|发生|达成|起诉|裁定|警告|承诺|reports?|announc|"
        r"launch|release|sign|meet|visit|approve|reject)",
        re.IGNORECASE,
    )

    for result in results:
        text = result.snippet.split("网页正文摘录：", 1)[-1]
        segments = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
        lines = [" ".join(line.split()).strip(" -•\t") for line in segments]
        title_candidate = _corroboration_query(result)
        if not _listing_page(result) and title_candidate:
            lines.insert(0, title_candidate)
        for index, line in enumerate(lines):
            if not 10 <= len(line) <= 180 or discard.search(line):
                continue
            semantic = _semantic_text(line)
            if entities and not all(_entity_matches(entity, semantic, "") for entity in entities):
                continue
            if _semantic_text(line) == _semantic_text(result.title):
                continue
            fingerprint = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", semantic)
            if len(fingerprint) < 8 or fingerprint in seen:
                continue
            seen.add(fingerprint)
            nearby = " ".join(lines[max(0, index - 1) : min(len(lines), index + 2)])
            score = 2.0 if event_cues.search(line) else 0.0
            score += 2.0 if dated.search(line) else 1.0 if dated.search(nearby) else 0.0
            score += min(len(line), 90) / 180.0
            candidates.append((score, position, line))
            position += 1

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2][:160] for item in candidates[:limit])


def source_corroboration_sufficient(
    query: str,
    results: list[SearchResult],
) -> bool:
    """Require a primary source or independently hosted sources with opened-page text."""
    if any(_primary_source(query, item) for item in results):
        return True
    if _requests_primary_sources(query):
        return False
    hosts = {_publisher_identity(item.url) for item in results}
    has_opened_page = any("网页正文摘录：" in item.snippet for item in results)
    return len(hosts) >= 2 and has_opened_page


def viable_results(
    query: str,
    results: list[SearchResult],
    *,
    allow_undated_historical: bool = False,
    allow_partial_authoritative_entities: bool = False,
) -> list[SearchResult]:
    """Drop candidates that do not match any material part of the request."""
    entities = _entity_terms(query)
    topics = _topic_terms(query)
    semantic_query = _semantic_text(query)
    requested_documents = tuple(term for term in _FOCUS_DOCUMENT_TERMS if term in semantic_query)
    viable: list[SearchResult] = []
    for item in results:
        text = _semantic_text(f"{item.title}\n{item.snippet}")
        hostname = (urlsplit(item.url).hostname or "").casefold()
        compact_hostname = re.sub(r"[^a-z0-9]", "", hostname)
        if not temporal_compatible(
            query,
            item,
            allow_undated_historical=allow_undated_historical,
        ):
            continue
        if entities:
            entity_matches = tuple(
                _entity_matches(entity, text, compact_hostname) for entity in entities
            )
            if not all(entity_matches) and not (
                allow_partial_authoritative_entities
                and _authoritative_specialist(query, item)
                and any(entity_matches)
            ):
                continue
        if requested_documents and not any(document in text for document in requested_documents):
            continue
        topic_matches = sum(_topic_matches(topic, text) for topic in topics)
        relevance = _relevance_score(query, item)
        # An entity match alone is not enough: a city name on an unrelated
        # company page must not satisfy a weather request, and the same rule
        # applies to every other topic family.
        if (
            topics
            and topic_matches < max(1, (len(topics) + 1) // 2)
            and not _current_role_hint_match(query, item)
        ):
            continue
        if relevance > 0.0:
            viable.append(item)
    return viable


def discovery_candidates(query: str, results: list[SearchResult]) -> list[SearchResult]:
    """Keep bounded leads when guessed lexical constraints reject every result.

    These are still untrusted retrieval records, not accepted answer support. Exact
    entity/time/claim coverage remains the runtime semantic review's responsibility.
    """
    strict = viable_results(query, results, allow_undated_historical=True,
                            allow_partial_authoritative_entities=True)
    entities = _entity_terms(query)
    topics = _topic_terms(query)
    anchor = entities[0] if entities else None
    leads = list(strict)
    for item in results:
        if not temporal_compatible(query, item, allow_undated_historical=True):
            continue
        body = _semantic_text(f"{item.title}\n{item.snippet}")
        if item in strict:
            continue
        if entities and not any(_entity_matches(entity, body, "") for entity in entities):
            continue
        if strict and not topics and sum(term in body for term in _query_terms(query)) < 2:
            continue
        if topics and not any(_topic_matches(topic, body) for topic in topics):
            continue
        if not anchor and not topics and not any(term in body for term in _query_terms(query)):
            continue
        leads.append(item)
    return leads


def _search_cache_key(base_url: str, value: SearchRequest) -> str:
    canonical = f"{base_url.rstrip('/')}\0{' '.join(value.query.split())}\0{value.limit}\0{value.queries_managed}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _cached_results(cache_key: str, *, allow_stale: bool = False) -> list[SearchResult] | None:
    cached = _SEARCH_CACHE.get(cache_key)
    if cached is None:
        return None
    stored_at, results, _observed = cached
    age = time.monotonic() - stored_at
    if age > _SEARCH_CACHE_STALE_TTL_SEC:
        del _SEARCH_CACHE[cache_key]
        return None
    if age > _SEARCH_CACHE_TTL_SEC and not allow_stale:
        return None
    _SEARCH_CACHE.move_to_end(cache_key)
    return list(results)


def _store_cached_results(
    cache_key: str, results: list[SearchResult], *, observed: list[SearchResult] | None = None,
) -> None:
    _SEARCH_CACHE[cache_key] = (time.monotonic(), tuple(results), tuple((observed or results)[:80]))
    _SEARCH_CACHE.move_to_end(cache_key)
    while len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX_ENTRIES:
        _SEARCH_CACHE.popitem(last=False)


def _safe_public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return normalized


def _source_identity(url: str) -> str:
    """Keep resource paths and query parameters distinct; fragments are not resources."""
    parsed = urlsplit(url)
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path or '/'}?{parsed.query}"


def _corroborated_alias_key(item: SearchResult) -> tuple[str, str, str, str] | None:
    # A shared numeric ID alone is not proof of identity across collections.
    # Preserve article aliases only when a named leaf AND the search description agree.
    parsed = urlsplit(item.url)
    leaf = parsed.path.rsplit("/", 1)[-1]
    if not re.search(r"[a-zA-Z]", leaf.split(".", 1)[0]) or not re.search(r"\d{6,}", leaf):
        return None
    parts = item.snippet.split("\n", 3)
    if len(parts) != 4 or not parts[3].strip():
        return None
    return parsed.netloc.casefold(), leaf, parsed.query, parts[3].strip()


class _VisibleTextParser(HTMLParser):
    _ignored: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "noscript", "svg", "canvas", "template"}
    )
    _blocks: ClassVar[frozenset[str]] = frozenset(
        {
            "article",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "main",
            "p",
            "section",
            "table",
            "td",
            "th",
            "tr",
        }
    )

    def __init__(self, *, exclude_navigation: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._excluded_tags = self._ignored | ({"nav", "footer"} if exclude_navigation else set())
        self._ignored_depth = 0
        self._exclude_navigation = exclude_navigation
        self._element_stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor: str | None = None
        self._anchor_text: list[str] = []
        self._table_depth = 0
        self._row_cells: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_span = ""
        self._list_stack: list[tuple[str, list[str]]] = []
        self._closed_inline = False
        self._sibling_inline = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and not self._ignored_depth:
            self._anchor = dict(attrs).get("href")
            self._anchor_text = []
        attributes = dict(attrs)
        breadcrumb = bool(re.search(r"breadcrumb|面包屑|当前位置", " ".join(
            attributes.get(key) or "" for key in ("aria-label", "class", "id")), re.IGNORECASE))
        navigation = self._exclude_navigation and attributes.get("role", "") in {"navigation", "menubar", "menu"}
        excluded = tag in self._excluded_tags or navigation
        if breadcrumb and tag not in self._ignored:
            excluded = False
        # Non-text records often encode their actual status in an accessible
        # icon label. Keep it in the current cell, never derive it from a filename.
        if (not self._ignored_depth and attributes.get("aria-hidden") != "true"
            and (tag == "img" or attributes.get("role") == "img" or tag == "svg")):
            label = attributes.get("alt") if tag == "img" else None
            if label is None:
                label = attributes.get("aria-label") or attributes.get("title")
            if label:
                self.handle_data(label)
        void = tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
        if not void:
            self._element_stack.append((tag, excluded))
        if excluded and not void:
            self._ignored_depth += 1
        elif not self._ignored_depth:
            inline = {"span", "b", "strong", "em", "i", "small"}
            self._sibling_inline = self._closed_inline and tag in inline
            self._closed_inline = False
            if tag in {"ul", "ol"} and self._cell_parts is None:
                self._list_stack.append((tag, self.parts))
                self.parts = []
            if tag == "li" and self._list_stack and self._cell_parts is None:
                self.parts.append("\n<li>")
                return
            if tag == "table":
                self._table_depth += 1
            if self._table_depth == 1 and tag == "tr":
                self._row_cells = []
            elif self._table_depth == 1 and tag in {"td", "th"} and self._row_cells is not None:
                self._cell_parts = []
                spans = [(key, value) for key, value in attrs if key in {"rowspan", "colspan"}
                         and value is not None and value.isdigit() and value != "1"]
                self._cell_span = "".join(f" [{key}={value}]" for key, value in spans)
            elif self._cell_parts is not None and tag in self._blocks:
                self._cell_parts.append(" ")
            elif tag in self._blocks:
                self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor:
            self.links.append((self._anchor, " ".join(self._anchor_text)))
            self._anchor = None
        matching = next((i for i in range(len(self._element_stack) - 1, -1, -1)
                         if self._element_stack[i][0] == tag), None)
        closed_exclusions = 0
        if matching is not None:
            closed_exclusions = sum(excluded for _, excluded in self._element_stack[matching:])
            del self._element_stack[matching:]
        if closed_exclusions:
            self._ignored_depth = max(0, self._ignored_depth - closed_exclusions)
        elif not self._ignored_depth:
            self._closed_inline = tag in {"span", "b", "strong", "em", "i", "small"}
            self._sibling_inline = False
            if tag in {"ul", "ol"} and self._cell_parts is None and self._list_stack and self._list_stack[-1][0] == tag:
                body = "".join(self.parts).strip()
                _, self.parts = self._list_stack.pop()
                self.parts.append(f"\n<{tag}>\n{body}\n</{tag}>\n")
            if tag == "li" and self._list_stack and self._cell_parts is None:
                self.parts.append("</li>\n")
                return
            if self._table_depth == 1 and tag in {"td", "th"} and self._cell_parts is not None:
                cell = " ".join("".join(self._cell_parts).split()) + self._cell_span
                if self._row_cells is not None:
                    self._row_cells.append(cell)
                self._cell_parts = None
            elif self._table_depth == 1 and tag == "tr" and self._row_cells is not None:
                self.parts.append("\n" + " | ".join(self._row_cells) + "\n")
                self._row_cells = None
            elif self._cell_parts is not None and tag in self._blocks:
                self._cell_parts.append(" ")
            elif tag in self._blocks:
                self.parts.append("\n")
            if tag == "table":
                self._table_depth = max(0, self._table_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            target = self._cell_parts if self._cell_parts is not None else self.parts
            previous = next((part.rstrip() for part in reversed(target) if part.strip()), "")
            if self._sibling_inline and re.search(r"\d$", previous) and re.match(r"\s*[+-]?\d", data):
                target.append(" | ")
            if data.strip():
                self._sibling_inline = False
                self._closed_inline = False
            if self._cell_parts is not None:
                self._cell_parts.append(data)
            else:
                self.parts.append(data)
            if self._anchor:
                self._anchor_text.append(data)


class _PublicJsonParser(HTMLParser):
    """Read inert public JSON data blocks, never execute page scripts."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts: list[str] = []
        self.records: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.active = str(dict(attrs).get("type", "")).split(";", 1)[0].strip().lower() in {
                "application/json", "application/ld+json"}
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or not self.active:
            return
        self.active = False
        try:
            value = json.loads("".join(self.parts))
        except (ValueError, RecursionError):
            return
        if isinstance(value, (dict, list)):
            self.records.append(json.dumps(value, ensure_ascii=False, indent=2))


def extract_public_page_content(markup: str) -> str:
    visible = extract_visible_text(markup)
    parser = _PublicJsonParser()
    parser.feed(markup)
    # Separate records keep their original field hierarchy, identity, dates and
    # units. They are publisher data, not independent corroborating sources.
    return "\n\n".join(filter(None, (visible, *("[Embedded public JSON]\n" + record
                                              for record in parser.records))))


def extract_visible_text(markup: str) -> str:
    parser = _VisibleTextParser(exclude_navigation=True)
    parser.feed(markup)
    while parser._list_stack:
        tag, parent = parser._list_stack.pop()
        parent.append(f"\n<{tag}>\n" + "".join(parser.parts) + f"\n</{tag}>\n")
        parser.parts = parent
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def relevant_excerpt(query: str, content: str, limit: int = 1200) -> str:
    # Repeated cells are meaningful in tables and timelines (for example the
    # same status or value on adjacent dates). Global de-duplication detached
    # those values from their row labels and left the model with dates but no
    # corresponding facts.
    if len(content) <= limit:
        return content
    structured = pack_structured_blocks(content, query, limit)
    if structured is not None:
        return structured
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    terms = _query_terms(query)
    normalized_lines = [_semantic_text(line) for line in lines]
    requested_dates = _calendar_dates(query)
    if len(requested_dates) == 2:
        start, end = min(requested_dates), max(requested_dates)
        span_days = (end - start).days
        if 0 < span_days <= 31:
            requested_dates = tuple(
                start + timedelta(days=offset) for offset in range(span_days + 1)
            )
    date_markers = tuple(
        dict.fromkeys(
            _semantic_text(marker)
            for requested in requested_dates
            for marker in _date_markers(requested)
        )
    )
    scored = [
        (
            sum(_term_weight(term) if term in normalized_lines[index] else 0.0 for term in terms)
            + sum(5.0 for marker in date_markers if marker in normalized_lines[index])
            + int(bool(re.search(r"\d", line))),
            index,
            line,
        )
        for index, line in enumerate(lines)
    ]
    anchors = sorted(
        (item for item in scored if item[0] > 0),
        key=lambda item: (-item[0], item[1]),
    )[:12]
    if not anchors:
        return "\n".join(lines)[:limit]
    # Preserve the highest-value evidence first.  Sorting every selected line
    # back into document order allowed long navigation/header text to consume
    # the entire budget before an exact requested date near the end of a page.
    selected: list[str] = []
    selected_indexes: set[int] = set()
    used = 0
    for _, index, _ in anchors:
        # Prefer following cells because HTML tables are commonly flattened as
        # row label -> value -> qualifier. Keep a little leading context for
        # headings while preserving the row's complete structured payload.
        for nearby in range(max(0, index - 2), min(len(lines), index + 7)):
            if nearby in selected_indexes:
                continue
            line = lines[nearby]
            addition = len(line) + (1 if selected else 0)
            if selected and used + addition > limit:
                continue
            selected.append(line)
            selected_indexes.add(nearby)
            used += addition
            if used >= limit:
                break
        if used >= limit:
            break
    return "\n".join(selected)[:limit]


async def _public_destination(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or hostname.casefold() in {"localhost", "host.docker.internal"}
        or hostname.casefold().endswith(".local")
    ):
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    try:
        return ip_address(hostname).is_global
    except ValueError:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError:
            return False
        addresses = tuple(dict.fromkeys(ip_address(record[4][0]) for record in records))
    return bool(addresses) and all(address.is_global for address in addresses)


async def fetch_public_page(
    client: httpx.AsyncClient,
    url: str,
) -> FetchResponse | None:
    current = url
    for _ in range(3):
        if not await _public_destination(current):
            return None
        try:
            async with client.stream(
                "GET",
                current,
                headers={
                    "User-Agent": ("Mozilla/5.0 (compatible; EnterpriseAgentResearch/1.0)"),
                    "Accept": "application/json,text/html,text/plain;q=0.9",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                },
                timeout=4,
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                if response.is_error:
                    return None
                content_type = response.headers.get("content-type", "").casefold()
                if not content_type.startswith(("text/html", "text/plain", "application/json")):
                    return None
                chunks: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = 750_000 - size
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(chunk) > remaining:
                        truncated = True
                    chunks.append(chunk[:remaining])
                    size += len(chunks[-1])
                raw = b"".join(chunks)
                encoding = response.charset_encoding
                if encoding is None:
                    charset = re.search(
                        rb"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
                        raw[:16_384],
                        re.IGNORECASE,
                    )
                    encoding = (
                        charset.group(1).decode("ascii", errors="ignore")
                        if charset is not None
                        else "utf-8"
                    )
                if encoding.casefold() in {"gbk", "gb2312"}:
                    encoding = "gb18030"
        except (httpx.HTTPError, OSError):
            return None
        try:
            decoded = raw.decode(encoding, errors="replace")
        except LookupError:
            decoded = raw.decode("utf-8", errors="replace")
        content = (
            extract_public_page_content(decoded)
            if content_type.startswith("text/html")
            else "\n".join(line.strip() for line in decoded.splitlines() if line.strip())
        )
        if content_type.startswith("application/json"):
            try:
                json.loads(decoded)
            except ValueError:
                return None
            content = json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
        if len(content) < 20:
            return None
        link_parser = _VisibleTextParser()
        if content_type.startswith("text/html"):
            link_parser.feed(decoded)
        links = tuple((urljoin(current, href), label) for href, label in link_parser.links)[:2000]
        return FetchResponse(
            source_links=links,
            fetch_truncated=truncated,
            read_mode="HTML_STATIC" if content_type.startswith("text/html") else "JSON" if content_type.startswith("application/json") else "TEXT",
            url=current,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )
    return None


async def enrich_results(
    query: str,
    results: list[SearchResult],
    client: httpx.AsyncClient,
) -> list[SearchResult]:
    # Retain fetched source text before runtime context selection. A provider
    # excerpt is irreversible: later rereads cannot recover omitted headings,
    # dates or prose. fetch_public_page bounds bytes; model context is bounded
    # separately in the runtime, not by discarding the retained original here.
    pending_indexes = [
        index for index, item in enumerate(results[:4]) if "网页正文摘录：" not in item.snippet
    ]
    fetched = await asyncio.gather(
        *(fetch_public_page(client, results[index].url) for index in pending_indexes),
        return_exceptions=True,
    )
    fetched_by_index = dict(zip(pending_indexes, fetched, strict=True))
    enriched: list[SearchResult] = []
    # A team/index page is a discovery lead. Open its actual report links once;
    # links retain the same public-destination and redirect checks as search hits.
    if re.search(r"latest|recent|最新|最近", query, re.IGNORECASE) and re.search(
        r"match|game|result|score|比赛|比分|结果", query, re.IGNORECASE
    ):
        entities = _high_confidence_entities(query)
        candidates: dict[str, tuple[int, str]] = {}
        for page in fetched_by_index.values():
            if not isinstance(page, FetchResponse):
                continue
            for url, label in page.source_links:
                if not _safe_public_url(url) or urlsplit(url).hostname != urlsplit(page.url).hostname:
                    continue
                text = (label + " " + urlsplit(url).path).casefold()
                hits = sum(entity.casefold() in text for entity in entities)
                if hits < min(2, len(entities)) or not entities:
                    continue
                if not re.search(r"recap|report|gameid|story|victory|win|score|result", text):
                    continue
                score = hits + (5 if re.search(r"recap|gameid|victory|win", text) else 0)
                candidates[url] = (score, label)
        selected_links = sorted(candidates, key=lambda url: -candidates[url][0])[:3]
        opened_links = await asyncio.gather(
            *(fetch_public_page(client, url) for url in selected_links), return_exceptions=True
        )
        for url, page in zip(selected_links, opened_links, strict=True):
            if not isinstance(page, FetchResponse):
                continue
            title = candidates[url][1].strip() or page.content.splitlines()[0]
            observed = datetime.now(UTC).isoformat()
            excerpt = page.content
            snippet = f"{title}\n检索时间：{observed}\n\n网页正文摘录：\n{excerpt}"
            enriched.append(SearchResult(
                title=title[:500], url=page.url, snippet=snippet, observed_at=observed,
                fetch_truncated=page.fetch_truncated, read_mode=page.read_mode,
                content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
            ))
    for index, item in enumerate(results):
        page = fetched_by_index.get(index)
        if isinstance(page, FetchResponse):
            excerpt = page.content
            if excerpt:
                published_at = item.published_at or _inferred_page_date(item, page.content)
                snippet_header = item.snippet[:600]
                if published_at is not None and item.published_at is None:
                    snippet_header = re.sub(
                        r"(?m)^来源发布日期：未提供$",
                        f"来源发布日期：{published_at}",
                        snippet_header,
                    )
                snippet = f"{snippet_header}\n\n网页正文摘录：\n{excerpt}"
                item = item.model_copy(
                    update={
                        "snippet": snippet,
                        "url": page.url,
                        "source_links": page.source_links,
                        "fetch_truncated": page.fetch_truncated,
                        "read_mode": page.read_mode,
                        "published_at": published_at,
                        "content_hash": hashlib.sha256(snippet.encode()).hexdigest(),
                    }
                )
        enriched.append(item)
    return enriched


def normalize_results(payload: object, limit: int, *, query: str = "") -> list[SearchResult]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SearchBackendUnavailable("search backend returned an invalid response")
    results: list[SearchResult] = []
    seen: dict[str, int] = {}
    for raw in payload["results"][:200]:
        if not isinstance(raw, dict):
            continue
        url = _safe_public_url(raw.get("url"))
        if url is None:
            continue
        raw_engines = raw.get("engines", [])
        engine_values = [value for value in raw_engines if isinstance(value, str)] if isinstance(raw_engines, list) else []
        if isinstance(raw.get("engine"), str):
            engine_values.append(raw["engine"])
        engines = bounded_labels(engine_values, 16, 80)
        if url in seen:
            old = results[seen[url]]
            results[seen[url]] = old.model_copy(update={"engines": bounded_labels((*old.engines, *engines), 16, 80)})
            continue
        title_value = raw.get("title")
        content_value = raw.get("content")
        title = title_value.strip() if isinstance(title_value, str) else ""
        content = content_value.strip() if isinstance(content_value, str) else ""
        if not title or not content:
            continue
        published_at = next(
            (
                parsed
                for parsed in (
                    _source_date(raw.get("publishedDate")),
                    _source_date(raw.get("published_date")),
                    _source_date(raw.get("date")),
                )
                if parsed is not None
            ),
            None,
        )
        observed_at = datetime.now(UTC).isoformat()
        temporal_note = (
            f"来源发布日期：{published_at}\n检索时间：{observed_at}"
            if published_at is not None
            else f"来源发布日期：未提供\n检索时间：{observed_at}"
        )
        snippet = f"{title[:500]}\n{temporal_note}\n{content[:1200]}"
        seen[url] = len(results)
        results.append(
            SearchResult(
                engines=engines, queries=(query[:1000],) if query else (),
                title=title[:500],
                url=url,
                snippet=snippet,
                published_at=published_at,
                observed_at=observed_at,
                content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
            )
        )
    return diverse_pool(results, limit, lambda item: item.engines)


SEARCH_PIPELINE_TIMEOUT_SECONDS = 10.5


def _unselected_candidates(
    query: str, observed: list[SearchResult], selected: list[SearchResult],
) -> list[SearchCandidate]:
    """Soft exclusions are revisitable leads, never accepted factual support."""
    selected_urls = {item.url for item in selected}
    unique: dict[str, SearchResult] = {}
    for item in observed:
        parsed = urlsplit(item.url)
        if (item.url in selected_urls or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment or len(item.url) > 4000):
            continue
        previous = unique.get(item.url)
        if previous is None:
            unique[item.url] = item
        else:
            chosen = item if len(item.snippet) > len(previous.snippet) else previous
            unique[item.url] = chosen.model_copy(update={
                "engines": bounded_labels((*previous.engines, *item.engines), 16, 80),
                "queries": bounded_labels((*previous.queries, *item.queries), 8, 1000),
            })
    eligible = {item.url for item in discovery_candidates(query, observed)}
    return [SearchCandidate(
        url=item.url, title=item.title[:500], snippet=item.snippet[:1200],
        engines=item.engines, queries=item.queries,
        reason="ranking_or_result_limit" if item.url in eligible else "lexical_or_temporal_filter",
    ) for item in diverse_pool(list(unique.values()), 40, lambda item: item.engines)]


async def search_searxng(
    value: SearchRequest, client: httpx.AsyncClient, base_url: str,
    *, candidates: list[SearchCandidate] | None = None,
) -> list[SearchResult]:
    """Return completed discovery before the caller's bounded wait expires."""
    observed: list[SearchResult] = []
    pending: list[asyncio.Task[list[SearchResult]]] = []
    selected: list[SearchResult] = []
    try:
        selected = await asyncio.wait_for(
            _search_searxng_pipeline(value, client, base_url, observed, pending),
            timeout=SEARCH_PIPELINE_TIMEOUT_SECONDS,
        )
        return selected
    except TimeoutError:
        # Preserve source identity/content hashes. Do not upgrade a snippet to
        # an opened page, invent missing facts, or cache incomplete coverage.
        by_url = {item.url: item for item in observed}
        selected = rank_diverse_results(
            value.query, discovery_candidates(value.query, list(by_url.values())),
            value.limit,
        )
        return selected
    finally:
        if candidates is not None:
            candidates.extend(_unselected_candidates(value.query, observed, selected))
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _search_searxng_pipeline(
    value: SearchRequest,
    client: httpx.AsyncClient,
    base_url: str,
    observed: list[SearchResult],
    pending: list[asyncio.Task[list[SearchResult]]],
) -> list[SearchResult]:
    cache_key = _search_cache_key(base_url, value)
    cached = _cached_results(cache_key)
    if cached is not None:
        observed.extend(_SEARCH_CACHE[cache_key][2])
        return cached[: value.limit]
    # A larger cached response may satisfy a smaller request, never the reverse.
    for larger_limit in range(value.limit + 1, 21):
        larger_key = _search_cache_key(base_url, value.model_copy(update={"limit": larger_limit}))
        larger = _cached_results(larger_key)
        if larger is not None and len(larger) >= value.limit:
            observed.extend(_SEARCH_CACHE[larger_key][2])
            return larger[:value.limit]

    async def observe_hint(coroutine: Awaitable[list[SearchResult]]) -> list[SearchResult]:
        result = await coroutine
        observed.extend(result)
        return result

    def launch_hint(coroutine: Awaitable[list[SearchResult]]) -> asyncio.Task[list[SearchResult]]:
        task = asyncio.create_task(observe_hint(coroutine))
        pending.append(task)
        return task

    # Independent discovery runs concurrently. A slow general engine must not
    # hide records already retrieved by another source.
    sports_results_task = launch_hint(_opened_sports_result_hints(value.query, client)) if not value.queries_managed else None
    async def search_once(query: str) -> list[SearchResult]:
        language = "auto"  # Query language must not restrict source languages.
        response = await client.get(
            base_url.rstrip("/") + "/search",
            params={
                "q": query,
                "format": "json",
                "language": language,
                "safesearch": 1,
            },
            timeout=8,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload: Any = response.json()
        failures = payload.get("unresponsive_engines", []) if isinstance(payload, dict) else []
        if isinstance(failures, list):
            diagnostics = _SEARCH_DIAGNOSTICS.get()
            for failure in failures[:20]:
                if not isinstance(failure, list) or len(failure) < 2:
                    continue
                item = {"engine": str(failure[0])[:80], "reason": str(failure[1])[:160]}
                logging.getLogger(__name__).warning("search backend degraded engine=%s reason=%s", item["engine"], item["reason"])
                if diagnostics is not None and len(diagnostics) < 20 and item not in diagnostics:
                    diagnostics.append(item)
        normalized = normalize_results(payload, 40, query=query)
        observed.extend(normalized)
        results = normalized  # Do not discard other engines before candidate review.
        if not results and isinstance(payload, dict) and payload.get("unresponsive_engines"):
            raise SearchBackendUnavailable("search engines returned no results while reporting failures")
        return results

    merged: list[SearchResult] = []
    seen: set[str] = set()
    seen_sources: set[str] = set()
    seen_aliases: set[tuple[str, str, str, str]] = set()
    successful_calls = 0
    variants = (value.query,) if value.queries_managed else _selected_search_queries(value.query)
    current_events = _requests_current_events(value.query)
    # First-party role pages are independent of SearXNG. Open them while the
    # primary and bounded fallback searches run instead of adding another
    # serial network stage to multi-role questions.
    official_hints_task = (
        launch_hint(_opened_official_role_hints(value.query, client))
        if not value.queries_managed and _requests_current_officeholder(value.query)
        else None
    )
    forecast_hints_task = launch_hint(_opened_official_forecast_hints(value.query, client)) if not value.queries_managed else None
    broad_request = current_events or bool(
        re.search(
            r"(?:哪些|列出|总结|盘点|比较|对比|为什么|原因|如何|怎么|"
            r"未来(?:一周|7天|七天)|list|summary|compare|why|how)",
            value.query,
            re.IGNORECASE,
        )
    )

    async def collect(query: str) -> list[SearchResult] | None:
        nonlocal successful_calls
        try:
            results = await search_once(query)
            successful_calls += 1
            observed.extend(results)
            return results
        except (httpx.HTTPError, SearchBackendUnavailable, ValueError):
            return None

    primary = await collect(variants[0])
    batches: list[list[SearchResult] | None] = [primary]
    viable_primary = viable_results(
        value.query,
        primary or [],
        allow_undated_historical=True,
        allow_partial_authoritative_entities=True,
    )
    if (
        primary is None
        or len(viable_primary) < (value.limit if broad_request else 1)
        or not any(_primary_source(value.query, item) for item in viable_primary)
    ):
        # Search fallbacks are independent. Running them concurrently keeps the
        # response-time ceiling bounded instead of multiplying it by the number
        # of deterministic query rewrites.
        batches.extend(await asyncio.gather(*(collect(query) for query in variants[1:])))

    def merge_batch(batch: list[SearchResult] | None) -> None:
        if batch is None:
            return
        for result in batch:
            source_identity = _source_identity(result.url)
            alias = _corroborated_alias_key(result)
            if result.url in seen or source_identity in seen_sources or (alias is not None and alias in seen_aliases):
                continue
            if alias is not None:
                seen_aliases.add(alias)
            seen.add(result.url)
            seen_sources.add(source_identity)
            merged.append(result)

    for batch in batches:
        merge_batch(batch)
    if official_hints_task is not None:
        merge_batch(await official_hints_task)
    if forecast_hints_task is not None:
        merge_batch(await forecast_hints_task)
    if sports_results_task is not None:
        merge_batch(await sports_results_task)
    if not value.queries_managed and (current_events or (
        _requests_current_officeholder(value.query) and not viable_results(value.query, merged)
    )):
        news_batch = await _search_news_rss(value.query, client)
        if news_batch:
            merge_batch(news_batch)
    if not successful_calls and not merged:
        stale = _cached_results(cache_key, allow_stale=True)
        if stale is not None:
            observed.extend(_SEARCH_CACHE[cache_key][2])
            return stale[: value.limit]
        raise SearchBackendUnavailable("search backend unavailable")
    if not merged:
        return []
    missing_boundaries = _missing_temporal_boundaries(
        value.query,
        viable_results(value.query, merged),
    )
    if missing_boundaries and not value.queries_managed:
        focused_batches = await asyncio.gather(
            *(
                collect(query)
                for query in _focused_temporal_queries(value.query, missing_boundaries)
            )
        )
        for batch in focused_batches:
            merge_batch(batch)
    if current_events and not value.queries_managed:
        discovery = rank_diverse_results(
            value.query,
            viable_results(
                value.query,
                merged,
                allow_undated_historical=True,
                allow_partial_authoritative_entities=True,
            ),
            4,
            max_per_host=1,
        )
        opened_discovery = await enrich_results(value.query, discovery, client)
        opened_by_url = {item.url: item for item in opened_discovery}
        merged = [opened_by_url.get(item.url, item) for item in merged]
        merge_batch([item for item in opened_discovery if item.url not in seen])
        verification_queries = tuple(
            dict.fromkeys(
                (
                    *_event_verification_queries(value.query, opened_discovery),
                    *(
                        verification
                        for item in opened_discovery
                        if (verification := _corroboration_query(item)) and not _listing_page(item)
                    ),
                )
            )
        )[:3]
        verification_batches = await asyncio.gather(
            *(collect(query) for query in verification_queries)
        )
        for batch in verification_batches:
            merge_batch(batch)
    candidate_limit = min(
        len(merged),
        max(value.limit * 2, 6) if broad_request else value.limit,
    )
    viable_candidates = discovery_candidates(value.query, merged)
    candidates = rank_diverse_results(
        value.query,
        viable_candidates,
        candidate_limit,
        max_per_host=1 if current_events else 2,
    )
    enriched = await enrich_results(value.query, candidates, client)
    observed.extend(enriched)
    final_results = rank_diverse_results(
        value.query,
        discovery_candidates(value.query, enriched),
        value.limit,
        max_per_host=1 if current_events else 2,
    )
    if not final_results:
        return []
    # Retrieval returns relevant, opened evidence even when coverage is partial or
    # only one trustworthy source is available. Corroboration and answer scope are
    # claim-level decisions; collapsing partial evidence into an empty result made
    # the model produce false "nothing was found" refusals.
    if not _SEARCH_DIAGNOSTICS.get():
        _store_cached_results(cache_key, final_results, observed=observed)
    return final_results


app = FastAPI(title="Enterprise Agent Web Search Provider")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/search", response_model=SearchResponse)
async def search(value: SearchRequest) -> SearchResponse:
    base_url = os.environ.get("SEARXNG_BASE_URL", "http://searxng:8080")
    diagnostics: list[dict[str, str]] = []
    token = _SEARCH_DIAGNOSTICS.set(diagnostics)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            candidates: list[SearchCandidate] = []
            results = await search_searxng(value, client, base_url, candidates=candidates)
    except SearchBackendUnavailable:
        raise HTTPException(status_code=503, detail={"message": "search backend unavailable",
            "backend_diagnostics": diagnostics[:20]}) from None
    finally:
        _SEARCH_DIAGNOSTICS.reset(token)
    return SearchResponse(results=results, candidates=candidates, backend_diagnostics=diagnostics)


@app.post("/v1/fetch", response_model=FetchResponse)
async def fetch(value: FetchRequest) -> FetchResponse:
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await fetch_public_page(client, value.url)
    if result is None:
        raise HTTPException(status_code=503, detail="web page unavailable")
    return result
