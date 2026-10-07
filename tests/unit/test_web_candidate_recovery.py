import hashlib
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.web_provider import main as provider
from enterprise_agent.capabilities import builtins
from enterprise_agent.runtime.web_provider import (
    WebDiscoveryCandidate,
    WebFetchResult,
)
from tests.fakes.web_provider import ScriptedWebProvider
from tests.unit.test_bounded_qa_fast_paths import _answer_contract, _request
from tests.unit.test_reviewer import review_fixture


def candidate(url='https://records.example/exact'):
    return WebDiscoveryCandidate(url=url, title='Alpha division record',
        snippet='Requested division record appears on the detail page.', reason='lexical_or_temporal_filter')


@pytest.mark.parametrize('prior', [False, True])
async def test_rechecks_candidate_body_before_more_engine_searches(monkeypatch, prior):
    lead = candidate()
    body = 'Alpha division actual dated record: metric 17.'
    page = WebFetchResult(url=lead.url, content=body, content_hash=hashlib.sha256(body.encode()).hexdigest())
    ScriptedWebProvider.configure({}, {lead.url: page}, {'first query': (lead,)})
    context = {'original_request': 'Alpha division metric', 'resolved_request': 'Alpha division metric',
               'search_queries': ['first query', 'second query']}
    if prior:
        context.update(recovery_query='missing division metric', discovery_candidates=[lead.model_dump(mode='json')])
    assessments = []

    async def task_context(*args):
        return context

    async def assess(*args, **kwargs):
        results = kwargs['results']
        assessments.append(bool(results))
        assert builtins._candidate_review_payload(kwargs['context']) or results
        if results:
            assert body in results[0].snippet
            return builtins.WebSearchAssessment(sufficient=True)
        return builtins.WebSearchAssessment(sufficient=False, candidate_urls=(lead.url,))

    monkeypatch.setattr(builtins, '_task_user_context', task_context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request('first query'))
    assert result.outcome == 'SUCCEEDED'
    assert ScriptedWebProvider.calls == ([] if prior else ['first query'])
    assert ScriptedWebProvider.fetch_calls == [lead.url]
    assert assessments == [False, True]
    assert body in result.output['results'][0]['snippet']
    assert 'Requested division record appears' not in result.output['results'][0]['snippet']


async def test_unused_candidate_is_persisted_separately_from_evidence(monkeypatch):
    lead = candidate()
    ScriptedWebProvider.configure({}, candidates={'first query': (lead,)})

    async def context(*args):
        return {'original_request': 'Alpha', 'search_queries': ['first query']}

    async def assess(*args, **kwargs):
        return builtins.WebSearchAssessment(sufficient=False, additional_queries=('second query',))

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request('first query'))
    assert result.output['results'] == []
    assert result.output['candidates'] == [lead.model_dump(mode='json')]


def test_candidate_review_is_bounded_and_keeps_https_boundary():
    values = [candidate(f'https://record{i}.example/page').model_dump(mode='json') for i in range(80)]
    values.insert(0, candidate('http://records.example/plain').model_dump(mode='json'))
    values.insert(0, candidate('https://user:password@records.example/private').model_dump(mode='json'))
    payload = builtins._candidate_review_payload({'discovery_candidates': values})
    assert len(payload) == 16
    assert all(item['url'].startswith('https://record') for item in payload)
    assert all(len(item['snippet']) <= 350 for item in payload)


@pytest.mark.parametrize('count', [5, 16])
def test_historical_evidence_respects_total_content_budget(count):
    _, _, base = review_fixture()
    items = []
    for index in range(count):
        content = f'Alpha record {index}\n' + ('2026-09-07 measurement 17 detail information\n' * 100)
        items.append(base.model_copy(update={'evidence_id': uuid4(), 'source_type': 'WEB',
            'locator': f'https://source{index}.example/record', 'content': content,
            'content_hash': hashlib.sha256(content.encode()).hexdigest()}))
    contract = _answer_contract('SUMMARY').model_copy(update={'freshness': 'HISTORICAL'})
    payload = builtins._analysis_evidence_payload(tuple(items), 'web-research', 'Alpha', contract,
                                                web_character_budget=6500, web_source_limit=count)
    assert len(payload) == count
    assert 0 < sum(len(item['content']) for item in payload) <= 6500


def test_soft_excluded_source_survives_as_lead_and_cache_preserves_it():
    def result(url, text):
        return provider.SearchResult(url=url, title=text, snippet=text, observed_at='2026-09-18T00:00:00Z',
                                     content_hash=hashlib.sha256(text.encode()).hexdigest())
    selected = result('https://alpha.example/main', 'Alpha record')
    excluded = result('https://beta.example/details', 'Beta record dated 2020-01-01')
    leads = provider._unselected_candidates('Alpha 2026-09-07 record', [selected, excluded], [selected])
    assert len(leads) == 1
    assert leads[0].url == excluded.url
    assert leads[0].reason == 'lexical_or_temporal_filter'
    small = provider.SearchRequest(query='isolated cache audit', limit=1)
    large = small.model_copy(update={'limit': 5})
    small_key = provider._search_cache_key('http://test', small)
    large_key = provider._search_cache_key('http://test', large)
    provider._store_cached_results(small_key, [selected], observed=[selected, excluded])
    assert small_key != large_key
    assert provider._cached_results(large_key) is None
    assert provider._SEARCH_CACHE[small_key][2] == (selected, excluded)
    provider._SEARCH_CACHE.pop(small_key)

async def test_failed_candidate_fetch_keeps_new_search_available(monkeypatch):
    from tests.unit.test_bounded_qa_fast_paths import _result
    lead = candidate()
    found = _result(url='https://replacement.example/record', title='Alpha record', body='Actual value 17')
    ScriptedWebProvider.configure({'first query': (found,)})

    async def context(*args):
        return {'original_request': 'Alpha', 'resolved_request': 'Alpha', 'recovery_query': 'missing value',
                'search_queries': ['first query'], 'discovery_candidates': [lead.model_dump(mode='json')]}

    async def assess(*args, **kwargs):
        if kwargs['results']:
            return builtins.WebSearchAssessment(sufficient=True)
        return builtins.WebSearchAssessment(sufficient=False, candidate_urls=(lead.url,))

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request('first query'))
    assert ScriptedWebProvider.fetch_calls == [lead.url]
    assert ScriptedWebProvider.calls == ['first query']
    assert result.output['results'][0]['url'] == found.url
    assert result.output['candidates'][0]['url'] == lead.url

async def test_unopened_links_from_selected_pages_survive_for_replan(monkeypatch):
    from tests.unit.test_bounded_qa_fast_paths import _result
    source = _result(url='https://records.example/index', title='Alpha division index', body='Navigation only.')
    source = source.model_copy(update={'source_links': (('https://records.example/20260907', 'Alpha September 7 record'),)})
    ScriptedWebProvider.configure({'first query': (source,)})

    async def context(*args):
        return {'original_request': 'Alpha division 2026-09-07', 'search_queries': ['first query']}

    async def assess(*args, **kwargs):
        return builtins.WebSearchAssessment(sufficient=False, additional_queries=('second query',))

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request('first query'))
    assert result.output['candidates'][0]['url'] == 'https://records.example/20260907'
    assert result.output['candidates'][0]['reason'] == 'discovered_link_not_opened'
    assert [item['url'] for item in result.output['results']] == [source.url]

@pytest.mark.parametrize('style', ['iso', 'slash', 'us', 'chinese'])
def test_generic_dated_records_keep_requested_rows_not_navigation(style):
    def stamp(day):
        if style == 'iso':
            return f'2026-09-{day:02d} Tuesday'
        if style == 'slash':
            return f'2026/09/{day:02d}'
        if style == 'us':
            return f'9/{day}/2026'
        return f'2026\u5e749\u6708{day}\u65e5'
    body = ('Inventory navigation\n' * 120) + 'Date\nQuantity\nUnit\n'
    body += '\n'.join(f'{stamp(day)}\n{day * 11}\nboxes' for day in range(1, 11))
    body += '\n' + ('Inventory navigation\n' * 120)
    content = 'Alpha stock ledger\n\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a\n' + body
    excerpt = builtins._focused_web_excerpt(content, 'Inventory 2026-09-07 through 2026-09-10', 650)
    assert len(excerpt) <= 650
    for day in range(7, 11):
        assert f'{stamp(day)}\n{day * 11}\nboxes' in excerpt
    assert stamp(1) not in excerpt
    assert 'Quantity' in excerpt
    assert 'Unit' in excerpt


async def test_discovery_checks_are_bounded_without_discarding_new_evidence(monkeypatch):
    from tests.unit.test_bounded_qa_fast_paths import _result
    lead = candidate()
    page = WebFetchResult(url=lead.url, content='Alpha partial record',
                         content_hash=hashlib.sha256(b'Alpha partial record').hexdigest())
    first = _result(url='https://records.example/first', title='Alpha first', body='Metric A 17')
    second = _result(url='https://records.example/second', title='Alpha second', body='Metric B 18')
    first = first.model_copy(update={'source_links': (('https://records.example/detail', 'Alpha detail'),)})
    ScriptedWebProvider.configure({'first query': (first,), 'second query': (second,)}, {lead.url: page})
    calls = []

    async def context(*args):
        return {'original_request': 'Alpha metrics', 'resolved_request': 'Alpha metrics',
                'recovery_query': 'missing metrics', 'search_queries': ['first query', 'second query'],
                'discovery_candidates': [lead.model_dump(mode='json')]}

    async def assess(*args, **kwargs):
        calls.append(kwargs['results'])
        return builtins.WebSearchAssessment(sufficient=False, candidate_urls=(lead.url,))

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request('first query'))
    assert len(calls) == 2
    assert ScriptedWebProvider.calls == ['first query', 'second query']
    assert {item['url'] for item in result.output['results']} >= {lead.url, first.url, second.url}


def test_recovery_checkpoint_keeps_old_and_new_source_bodies_without_mutation():
    from tests.unit.test_bounded_qa_fast_paths import _result
    _, _, base = review_fixture()
    body = 'Alpha division inventory: 17 units.'
    old = base.model_copy(update={'source_type': 'WEB', 'locator': 'https://old.example/record',
        'content': body, 'content_hash': hashlib.sha256(body.encode()).hexdigest()})
    context = {'retained_web_evidence': [old.model_dump(mode='json')]}
    retained = builtins._retained_search_results(context)
    new = tuple(_result(url=f'https://new.example/{i}', title='Beta', body='Beta: 23 units') for i in range(6))
    combined = builtins._coverage_search_results(new, retained)
    assert len(combined) == 6
    assert combined[0] == new[0]
    assert combined[1].snippet == body
    assert combined[1].content_hash == old.content_hash
    assert len(new) == 6
    assert context['retained_web_evidence'][0]['content'] == body
    invalid = old.model_copy(update={'content': 'tampered'})
    assert builtins._retained_search_results({'retained_web_evidence': [invalid.model_dump(mode='json')]}) == ()


@pytest.mark.parametrize('topic', ['inventory', 'product specifications', 'department directory'])
async def test_recovery_executes_gap_query_and_does_not_replay_tried_query(monkeypatch, topic):
    from tests.unit.test_bounded_qa_fast_paths import _result
    lead = candidate()
    gap_query = f'Alpha division {topic} details'
    old_query = f'Alpha {topic}'
    found = _result(url='https://replacement.example/detail', title=topic, body='Alpha division record 17')
    ScriptedWebProvider.configure({gap_query: (found,)})

    async def context(*args):
        return {'original_request': topic, 'recovery_query': 'long missing-fact objective',
            'search_queries': [old_query], 'previously_attempted_queries': [old_query],
            'initial_web_queries': [old_query], 'discovery_candidates': [lead.model_dump(mode='json')]}

    async def assess(*args, **kwargs):
        assert old_query in kwargs['attempted_queries']
        if kwargs['results']:
            return builtins.WebSearchAssessment(sufficient=True)
        return builtins.WebSearchAssessment(sufficient=False, additional_queries=(gap_query,))

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(old_query))
    assert ScriptedWebProvider.calls == [gap_query]
    assert result.output['attempted_queries'] == [gap_query]
    assert result.output['results'][0]['url'] == found.url


def test_many_search_candidates_cannot_evict_relevant_page_details():
    searches = tuple(candidate(f'https://search.example/{i}') for i in range(100))
    detail = candidate('https://records.example/20260909').model_copy(update={
        'title': 'Alpha division 2026-09-09 inventory', 'reason': 'discovered_link_not_opened'})
    ranked = builtins._rank_recovery_candidates('Alpha inventory 2026-09-07 to 2026-09-13', (*searches, detail), 80)
    assert len(ranked) == 80
    assert ranked[0] == detail
    assert any(item.reason != 'discovered_link_not_opened' for item in ranked)
    payload = builtins._candidate_review_payload({'original_request': 'Alpha inventory 2026-09-07 to 2026-09-13',
        'discovery_candidates': [item.model_dump(mode='json') for item in ranked]})
    assert payload[0]['url'] == detail.url


def test_date_range_ranks_middle_records_before_unrelated_site_navigation():
    links = (('https://records.example/navigation', 'Alpha inventory department directory complete historical archive'),
             ('https://records.example/20260909', '09-09'),
             ('https://records.example/20261009', '10-09'))
    ranked = builtins._rank_discovery_links('Alpha inventory 2026-09-07 to 2026-09-13', links)
    assert ranked[0] == links[1]
    assert len(builtins._requested_link_dates('2026-09-07 to 2026-09-13')) == 7
    assert builtins._requested_link_dates('2026-02-30') == ()


def test_auto_navigation_does_not_follow_date_only_unrelated_sections():
    page = WebFetchResult(url='https://records.example/alpha/history', content='Alpha history', content_hash='a' * 64,
        source_links=(('https://records.example/calendar/20260909', '09-09'),
                      ('https://records.example/alpha/20260909', '09-09')))
    assert builtins._web_followup_urls('Alpha 2026-09-07 to 2026-09-13', page) == ('https://records.example/alpha/20260909',)


def test_chinese_target_title_is_ranked_before_unrelated_long_sources():
    _, _, base = review_fixture()
    query = '\u7532\u516c\u53f8\u4e1c\u533a\u5e93\u5b58'
    target_body = query + '\nInventory: 17 units.'
    target = base.model_copy(update={'evidence_id': uuid4(), 'source_type': 'WEB',
        'locator': 'https://record.example/alpha', 'content': target_body,
        'content_hash': hashlib.sha256(target_body.encode()).hexdigest()})
    noise = []
    for i in range(20):
        body = 'General bulletin ' + str(i) + '\n' + 'Unrelated extensive article. ' * 100
        noise.append(base.model_copy(update={'evidence_id': uuid4(), 'source_type': 'WEB',
            'locator': f'https://news.example/{i}', 'content': body,
            'content_hash': hashlib.sha256(body.encode()).hexdigest()}))
    payload = builtins._analysis_evidence_payload((*noise, target), 'web-research', query, _answer_contract('FACT'),
        web_source_limit=1)
    assert payload[0]['evidence_id'] == str(target.evidence_id)


def test_model_candidate_pool_keeps_other_engines_and_query_provenance():
    rows = [candidate(f"https://a.example/{i}").model_copy(update={"engines": ("engine-a",),
            "queries": ("Alpha division metric",)}) for i in range(25)]
    rows.append(candidate("https://b.example/child").model_copy(update={"engines": ("engine-b",),
            "queries": ("Alpha division report",)}))
    payload = builtins._candidate_review_payload({"resolved_request": "Alpha division metric",
        "discovery_candidates": [item.model_dump(mode="json") for item in rows]})
    selected = next(item for item in payload if item["url"] == "https://b.example/child")
    assert selected["engines"] == ["engine-b"]
    assert selected["queries"] == ["Alpha division report"]
    assert selected["reason"] == "lexical_or_temporal_filter"
    assert len(payload) == 16


def test_page_link_flood_does_not_hide_engine_candidate():
    from enterprise_agent.runtime.discovery_pool import diverse_pool

    links = [candidate(f"https://navigation.example/item/{i}") for i in range(100)]
    target = candidate("https://original.example/record").model_copy(update={"engines": ("engine-b",)})
    known = (*links, target)
    ranked = builtins._rank_recovery_candidates("Alpha division", known, len(known))
    from urllib.parse import urlsplit
    retained = diverse_pool(list(ranked), 80,
        lambda item: item.engines or item.queries or (urlsplit(item.url).hostname or item.url,))
    assert target in retained
    payload = builtins._candidate_review_payload({"resolved_request": "Alpha division",
        "discovery_candidates": [item.model_dump(mode="json") for item in retained]})
    assert any(item["url"] == target.url for item in payload)


@pytest.mark.parametrize("gap", ["South branch inventory", "Version Z8 dimensions", "September 25 opening hours"])
def test_filtered_candidate_review_exposes_gap_beyond_original_preview(gap):
    body = "Unrelated directory links. " * 25 + gap + ": see the detailed records below."
    lead = candidate().model_copy(update={"snippet": body})
    context = {"recovery_query": gap, "discovery_candidates": [lead.model_dump(mode="json")]}
    payload = builtins._candidate_review_payload(context)
    assert gap in payload[0]["snippet"]
    assert payload[0]["snippet"] in body
    assert len(payload[0]["snippet"]) <= 350
    assert payload[0]["reason"] == lead.reason
    assert context["discovery_candidates"][0]["snippet"] == body



def test_gap_recovery_keeps_old_relevant_source_beyond_recent_sixteen():
    _, _, base = review_fixture()
    def source(text):
        return base.model_copy(update={"source_type": "WEB", "evidence_id": uuid4(), "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest()})
    original = source("Alpha division inventory: 31 items.")
    later = tuple(source(f"Unrelated source {index}") for index in range(20))
    selected = builtins._retained_evidence_for_gap((original, *later), "Alpha division inventory")
    assert selected[0] == original
    assert len(selected) == 16
    assert original.content == "Alpha division inventory: 31 items."
    assert len(builtins._retained_evidence_for_gap((original, original), "inventory")) == 1
