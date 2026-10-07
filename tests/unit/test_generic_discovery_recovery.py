import hashlib
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.capabilities import builtins
from enterprise_agent.runtime.web_provider import WebFetchResult
from tests.fakes.web_provider import ScriptedWebProvider
from tests.unit.test_bounded_qa_fast_paths import _request, _result


@pytest.mark.parametrize('query,label', [
    ('Alpha institute departments', 'Alpha institute departments'),
    ('Acme X2 specifications', 'Acme X2 specifications'),
    ('Operations handbook version 3', 'Operations handbook version 3'),
    ('Metro Alpha observations', 'Metro Alpha observations'),
])
def test_followup_links_use_relevance_not_topic_or_path_templates(query, label):
    page = WebFetchResult(url='https://index.example/root', content='Index', content_hash='a' * 64,
        source_links=(('https://index.example/arbitrary-record-id', label),
                      ('https://index.example/unrelated', 'Unrelated content'),
                      ('https://other.example/detail', label),
                      ('https://user:secret@index.example/private', label)))
    assert builtins._web_followup_urls(query, page) == ('https://index.example/arbitrary-record-id',)


@pytest.mark.parametrize('host', ['records.example', 'catalog.example', 'university.example'])
async def test_https_recovery_is_identical_for_every_publisher(monkeypatch, host):
    query = 'Alpha record'
    original = f'http://{host}/arbitrary-record'
    secure = f'https://{host}/arbitrary-record'
    body = 'Alpha actual record value 17'
    page = WebFetchResult(url=secure, content=body, content_hash=hashlib.sha256(body.encode()).hexdigest())
    ScriptedWebProvider.configure({query: (_result(url=original, title='Alpha', body='Short preview'),)}, {secure: page})

    async def context(*args):
        return {'original_request': query, 'search_queries': [query]}

    async def assess(*args, **kwargs):
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(query))
    assert ScriptedWebProvider.fetch_calls == [secure]
    assert any(body in item['snippet'] for item in result.output['results'])
    assert all(url.startswith('https://') for url in ScriptedWebProvider.fetch_calls)


async def test_search_pipeline_never_invokes_legacy_topic_templates(monkeypatch):
    query = 'Alpha requested records'
    ScriptedWebProvider.configure({})

    async def context(*args):
        return {'original_request': query, 'search_queries': [query]}

    async def assess(*args, **kwargs):
        return builtins.WebSearchAssessment(sufficient=False)

    def legacy(*args, **kwargs):
        raise AssertionError('Legacy topic-specific template must not execute')

    for name in ('_verified_official_role_pages', '_historical_weather_source_urls',
                 '_forecast_requested_places', '_undated_forecast_query'):
        monkeypatch.setattr(builtins, name, legacy)
    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(query))
    assert ScriptedWebProvider.calls == [query]
    assert ScriptedWebProvider.fetch_calls == []
    assert result.output['results'] == []


@pytest.mark.parametrize('breadcrumb', [
    'Location: University B > East Campus > Courses',
    '\u5f53\u524d\uff1a\nCatalog>\nManufacturer B>\nModel Alpha\nSpecifications',
    'Home > Organization B > Division Alpha',
])
def test_source_identity_survives_relevance_compression(breadcrumb):
    from uuid import uuid4

    from tests.unit.test_bounded_qa_fast_paths import _answer_contract
    from tests.unit.test_reviewer import review_fixture

    _, _, base = review_fixture()
    body = 'Popular links: Organization A\n' + ('Navigation\n' * 160) + breadcrumb
    body += '\n' + ('2026-09-07 value 17 units\n' * 120)
    content = 'Shared child name\n\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a\n' + body
    item = base.model_copy(update={'evidence_id': uuid4(), 'source_type': 'WEB',
        'locator': 'https://records.example/arbitrary-id', 'content': content,
        'content_hash': hashlib.sha256(content.encode()).hexdigest()})
    contract = _answer_contract('SUMMARY').model_copy(update={'freshness': 'HISTORICAL'})
    payload = builtins._analysis_evidence_payload((item,), 'web-research',
        'Organization A 2026-09-07', contract, web_character_budget=2000)
    assert breadcrumb in payload[0]['source_identity_context']
    assert 'Popular links' not in payload[0]['source_identity_context']
    assert len(payload[0]['source_identity_context']) <= 500
    assert len(payload[0]['content']) <= 2000
    assert payload[0]['content_hash'] == item.content_hash


@pytest.mark.parametrize('query', ['Alpha department roster', 'Alpha version specification', 'Alpha quarterly records'])
def test_followup_batch_does_not_let_first_page_monopolize_slots(query):
    early = WebFetchResult(url='https://early.example/index', content='Index', content_hash='a' * 64,
        source_links=tuple((f'https://early.example/item-{i}', 'Alpha general information') for i in range(3)))
    target = 'https://later.example/detail'
    later = WebFetchResult(url='https://later.example/index', content='Index', content_hash='a' * 64,
        source_links=((target, query), ('https://other.example/unsafe-cross-host', query)))
    batch = builtins._web_followup_batch(query, (early, later), set())
    assert batch[0] == target
    assert len(batch) == 3
    assert 'https://other.example/unsafe-cross-host' not in batch
    assert target not in builtins._web_followup_batch(query, (early, later), {target})


def test_followup_batch_removes_visited_links_before_applying_limit():
    urls = tuple(f'https://index.example/record-{i}' for i in range(4))
    page = WebFetchResult(url='https://index.example/root', content='Index', content_hash='a' * 64,
        source_links=tuple((url, 'Alpha records') for url in urls))
    assert builtins._web_followup_batch('Alpha records', (page,), set(urls[:3])) == (urls[3],)


@pytest.mark.parametrize('topic', ['inventory', 'department roster', 'product specifications'])
async def test_search_opened_page_is_refined_before_new_engine_query(monkeypatch, topic):
    query = f'Alpha {topic}'
    resolved = f'Alpha {topic} 2026-09-07'
    detail = 'https://index.example/record-20260907'
    entry = _result(url='https://index.example/root', title=query, body='Entry page with record links').model_copy(
        update={'source_links': ((detail, resolved),)})
    body = resolved + '\nRecord contains 12 units and its applicable scope.'
    page = WebFetchResult(url=detail, content=body, content_hash=hashlib.sha256(body.encode()).hexdigest())
    ScriptedWebProvider.configure({query: (entry,)}, {detail: page})

    async def context(*args):
        return {'original_request': resolved, 'resolved_request': resolved,
                'search_queries': [query, query + ' alternative']}

    async def assess(*args, **kwargs):
        assert ScriptedWebProvider.fetch_calls == [detail]
        assert any(body in result.snippet for result in kwargs['results'])
        return builtins.WebSearchAssessment(sufficient=True)

    monkeypatch.setattr(builtins, '_task_user_context', context)
    monkeypatch.setattr(builtins, '_assess_web_search', assess)
    monkeypatch.setattr(builtins, 'HttpWebProvider', ScriptedWebProvider)
    monkeypatch.setattr(builtins, 'load_web_provider_config', lambda: object())
    result = await builtins._web_search_handler(cast(async_sessionmaker[AsyncSession], object()), _request(query))
    assert ScriptedWebProvider.calls == [query]
    assert ScriptedWebProvider.fetch_calls == [detail]
    assert any(item['url'] == detail for item in result.output['results'])
