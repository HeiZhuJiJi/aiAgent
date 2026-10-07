import httpx
import pytest

from apps.web_provider.main import SearchRequest, search_searxng
from enterprise_agent.runtime.agent_graph import (
    AgentPlannerDraft,
    default_agent_tools,
    materialize_agent_decision,
)
from enterprise_agent.runtime.research_scope import anchor_query
from tests.unit.test_agent_graph import request


@pytest.mark.parametrize('scope,query', [
    ('Northbridge University East Campus', 'Northbridge University courses'),
    ('Acme Model X2', 'Acme specifications'),
    ('Operations manual version 3', 'Operations manual process'),
    ('Metro District Alpha', 'Metro historical observations'),
    ('\u5e7f\u5dde\u767d\u4e91\u533a', 'Guangzhou historical weather'),
])
def test_query_rewrite_retains_full_target(scope, query):
    anchored = anchor_query(query, (scope,))
    assert anchored.startswith(scope)
    assert query in anchored
    assert anchor_query(anchored, (scope,)) == anchored


def test_comparison_subquery_does_not_require_other_targets():
    assert anchor_query('Acme X2 specifications', ('Acme X2', 'Beta Y3')) == 'Acme X2 specifications'


def test_scope_and_historical_direction_survive_planning():
    draft = AgentPlannerDraft(speech_act='QUESTION', normalized_question='Metro District Alpha historical records',
        execution_level='BOUNDED_QA', information_sources=('WEB',), requires_fresh_information=True,
        temporal_direction='HISTORICAL', answer_type='SUMMARY', required_aspects=('past records',),
        entity_scope=('Metro District Alpha',), web_queries=('Metro historical records',))
    decision = materialize_agent_decision(draft, request(content=draft.normalized_question), default_agent_tools())
    tool = next(x for x in decision.tool_plan if x.tool_name == 'web.search')
    assert tool.arguments['entity_scope'] == ['Metro District Alpha']
    assert tool.arguments['queries'] == ['Metro District Alpha Metro historical records']
    assert decision.intent.temporal.direction == 'HISTORICAL'


async def test_managed_query_does_not_trigger_hidden_rewrites_or_topic_hints():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        assert request.url.host == 'managed-query-test'
        assert request.url.params['q'] == 'Orion Alpha weather 2026-09-07'
        return httpx.Response(200, json={'results': []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        results = await search_searxng(SearchRequest(query='Orion Alpha weather 2026-09-07', queries_managed=True),
                                      client, 'http://managed-query-test')
    assert results == []
    assert len(calls) == 1


@pytest.mark.parametrize('topic', ['inventory', 'opening hours', 'weather', 'transport timetable'])
def test_empty_temporal_query_broadens_without_topic_specific_rules(topic):
    from enterprise_agent.runtime.research_scope import undated_discovery_query
    assert undated_discovery_query(f'2026-09-07 Alpha {topic}', ('Alpha',)) == f'Alpha {topic}'


def test_discovery_broadening_preserves_entity_version_date():
    from enterprise_agent.runtime.research_scope import undated_discovery_query
    assert undated_discovery_query('Manual 2026-09-07 contents', ('Manual 2026-09-07',)) is None


def test_explicit_full_alias_can_be_used_without_mixing_query_languages():
    canonical = "北桥大学东校区"
    aliases = {canonical: ("Northbridge University East Campus",)}
    query = "Northbridge University East Campus courses"
    assert anchor_query(query, (canonical,), aliases) == query
    assert anchor_query("Northbridge University courses", (canonical,), aliases).startswith(canonical)
    assert anchor_query(query, (canonical,), {"another target": aliases[canonical]}).startswith(canonical)


def test_declared_alias_survives_plan_materialization_and_runtime_queries():
    from enterprise_agent.capabilities.builtins import _planned_web_queries
    from enterprise_agent.contracts.domain import BoundedResearchPlan, SourcePlan
    canonical = "北桥大学东校区"
    query = "Northbridge University East Campus courses"
    draft = AgentPlannerDraft(speech_act="QUESTION", normalized_question=canonical + "课程",
        execution_level="BOUNDED_QA", information_sources=("WEB",), answer_type="SUMMARY",
        entity_scope=(canonical,), entity_aliases={canonical: ("Northbridge University East Campus",)},
        web_queries=(query,))
    decision = materialize_agent_decision(draft, request(content=draft.normalized_question), default_agent_tools())
    tool = next(x for x in decision.tool_plan if x.tool_name == "web.search")
    assert tool.arguments["queries"] == [query]
    assert tool.arguments["entity_aliases"] == {canonical: ["Northbridge University East Campus"]}
    plan = BoundedResearchPlan(entity_scope=draft.entity_scope, entity_aliases=draft.entity_aliases,
        web_queries=(query,), source_plan=SourcePlan(required_sources=("PUBLIC_WEB",), forbidden_sources=("PARAMETRIC_KNOWLEDGE",)),
        result_limit=5, answer_contract=decision.answer_contract)
    from uuid import uuid4

    from enterprise_agent.api.app import _agent_decision_to_chat_route
    from enterprise_agent.api.contracts import ChatRouteInput
    planning = request(content=draft.normalized_question)
    route = _agent_decision_to_chat_route(ChatRouteInput(
        workspace_id=planning.workspace_id, conversation_id=planning.conversation_id,
        user_message_id=uuid4(), content=draft.normalized_question,
        model_config_id=planning.model_binding.config_id), decision)
    assert route.research_plan is not None
    assert route.research_plan.entity_aliases == draft.entity_aliases
    assert route.research_plan.web_queries == (query,)
    restored = BoundedResearchPlan.model_validate_json(plan.model_dump_json())
    assert restored.entity_aliases == draft.entity_aliases
    assert _planned_web_queries({"research_plan": restored.model_dump(mode="json")}, query) == (query,)


@pytest.mark.parametrize("aliases", [{"unknown": ("Name",)}, {"Target": ("",)}, {"Target": ("A", "B", "C", "D")}])
def test_invalid_discovery_aliases_are_rejected(aliases):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentPlannerDraft(speech_act="QUESTION", normalized_question="Target", execution_level="BOUNDED_QA",
                          entity_scope=("Target",), entity_aliases=aliases)
