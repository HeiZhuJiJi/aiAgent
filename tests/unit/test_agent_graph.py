import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from enterprise_agent.api.app import (
    _agent_decision_to_chat_route,
    _validate_planned_research_turn,
)
from enterprise_agent.api.contracts import ChatRouteInput, ResearchTurnCreateInput
from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.contracts.domain import (
    AgentTurnDecision,
    ConversationMessage,
    FailureCode,
    ModelBinding,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import Message
from enterprise_agent.runtime.agent_graph import (
    AgentPlannerContext,
    AgentPlannerDraft,
    AgentPlanningRequest,
    agent_tools_from_capabilities,
    build_agent_planning_graph,
    default_agent_tools,
    materialize_agent_decision,
    plan_agent_turn,
    validate_agent_decision,
)
from enterprise_agent.runtime.context_projection import AgentContextProjection
from enterprise_agent.storage.agent_decisions import AgentTurnDecisionSnapshot


def request(
    *,
    content: str = "你好",
    with_document: bool = False,
    workspace_knowledge_available: bool = False,
) -> AgentPlanningRequest:
    workspace_id = uuid4()
    conversation_id = uuid4()
    history = (
        ConversationMessage(
            message_id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            role="USER",
            content="特朗普是谁？",
            created_at=datetime.now(UTC),
        ),
        ConversationMessage(
            message_id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            role="ASSISTANT",
            content="特朗普是一名美国政治人物。",
            created_at=datetime.now(UTC),
        ),
    )
    return AgentPlanningRequest(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        context_projection=AgentContextProjection(
            current_turn=content,
            completed_messages=history,
            document_version_id=uuid4() if with_document else None,
            workspace_knowledge_available=workspace_knowledge_available,
        ),
        model_binding=ModelBinding(
            config_id=uuid4(),
            provider="OpenAICompatible",
            model_name="selected-model",
            config_version=3,
            selected_by="USER",
        ),
        deadline_at=datetime.now(UTC) + timedelta(seconds=5),
    )


def inline_decision() -> AgentTurnDecision:
    return AgentTurnDecision.model_validate(
        {
            "action": "EXECUTE",
            "intent": {
                "speech_act": "SOCIAL",
                "normalized_question": "你好",
                "temporal": {
                    "direction": "STABLE",
                    "requires_fresh_information": False,
                },
            },
            "execution": {
                "execution_level": "INLINE_CHAT",
                "source_plan": {"preferred_sources": ["MODEL"]},
                "risk": "READ",
                "maximum_tool_steps": 1,
                "confidence": 0.99,
            },
            "tool_plan": [
                {
                    "tool_name": "model.respond",
                    "purpose": "respond to the greeting",
                    "arguments": {},
                    "risk": "READ",
                }
            ],
        }
    )


def test_compact_planner_draft_materializes_ordinary_chat_without_nested_model_json() -> None:
    planning_request = request(content="介绍一下你自己")
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "SOCIAL",
            "normalized_question": "介绍一下你自己",
            "execution_level": "INLINE_CHAT",
        }
    )

    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())

    assert decision.action == "EXECUTE"
    assert decision.execution.execution_level == "INLINE_CHAT"
    assert decision.execution.source_plan.preferred_sources == ("MODEL",)
    assert decision.tool_plan[0].tool_name == "model.respond"
    assert decision.answer_contract is None
    assert len(json.dumps(AgentPlannerDraft.model_json_schema())) < len(
        json.dumps(AgentTurnDecision.model_json_schema())
    ) * 0.6


def test_compact_planner_draft_compiles_current_web_evidence_contract() -> None:
    planning_request = request(content="特朗普最近有什么新闻？")
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "QUESTION",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["WEB"],
            "requires_fresh_information": True,
            "temporal_direction": "CURRENT",
            "temporal_expression": "最近",
            "answer_type": "LIST",
            "required_aspects": ["多个相互独立的近期事件"],
            "web_queries": ["特朗普 最近 新闻"],
            "confidence": 0.93,
        }
    )

    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())

    assert decision.execution.source_plan.required_sources == ("WEB",)
    assert decision.execution.source_plan.forbidden_sources == ("MODEL",)
    assert decision.answer_contract is not None
    assert decision.answer_contract.answer_type == "LIST"
    assert decision.answer_contract.minimum_evidence_items == 2
    assert decision.tool_plan[0].arguments == {"queries": ["特朗普 最近 新闻"]}
    assert validate_agent_decision(decision, planning_request, default_agent_tools()) is None


def test_current_compound_fact_requires_fresh_evidence_but_not_event_dates() -> None:
    planning_request = request(content="现在日本首相和天皇是谁")
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "QUESTION",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["WEB"],
            "requires_fresh_information": True,
            "temporal_direction": "CURRENT",
            "temporal_expression": "现在",
            "answer_type": "FACT",
            "required_aspects": ["日本现任首相", "日本现任天皇"],
            "web_queries": ["日本 现任 首相 2026", "日本 现任 天皇 2026"],
        }
    )

    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())

    assert decision.answer_contract is not None
    assert decision.answer_contract.freshness == "CURRENT"
    assert decision.answer_contract.minimum_answer_items == 2
    assert decision.answer_contract.require_explicit_dates is False


def test_three_subquestions_compile_to_three_answer_slots_and_queries() -> None:
    planning_request = request(
        content="日本首相是谁，那天皇呢？还有澳大利亚最高领导人是谁？"
    )
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "QUESTION",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["WEB"],
            "requires_fresh_information": True,
            "temporal_direction": "CURRENT",
            "answer_type": "FACT",
            "required_aspects": [
                "日本现任首相",
                "日本现任天皇",
                "澳大利亚现任最高领导人及职务",
            ],
            "web_queries": [
                "日本现任首相 官方",
                "日本现任天皇 宫内厅",
                "澳大利亚现任国家元首 政府首脑 官方",
            ],
        }
    )
    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())
    assert decision.answer_contract is not None
    assert decision.answer_contract.minimum_answer_items == 3
    assert len(decision.answer_contract.required_aspects) == 3
    assert decision.tool_plan[0].arguments["queries"] == list(draft.web_queries)


def test_compound_fact_plan_cannot_drop_the_last_search_goal() -> None:
    planning_request = request(content="日本首相是谁，那天皇呢？还有澳大利亚最高领导人是谁？")
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "QUESTION",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["WEB"],
            "requires_fresh_information": True,
            "temporal_direction": "CURRENT",
            "answer_type": "FACT",
            "required_aspects": ["日本现任首相", "日本现任天皇", "澳大利亚现任最高领导人"],
            "web_queries": ["日本现任首相", "日本现任天皇"],
        }
    )
    with pytest.raises(DomainError, match="separate Web query"):
        materialize_agent_decision(draft, planning_request, default_agent_tools())


def test_compact_planner_draft_binds_rag_to_selected_document_version() -> None:
    planning_request = request(content="总结这份文档", with_document=True)
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "COMMAND",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["RAG"],
            "answer_type": "DOCUMENT_SUMMARY",
            "retrieval_queries": ["文档主要内容和结论"],
        }
    )

    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())

    assert decision.execution.source_plan.required_sources == ("RAG",)
    assert decision.answer_contract is not None
    assert decision.answer_contract.answer_type == "DOCUMENT_SUMMARY"
    assert decision.answer_contract.minimum_answer_items is None
    assert decision.tool_plan[0].tool_name == "knowledge.hybrid_search"
    assert decision.tool_plan[0].arguments["document_version_id"] == str(
        planning_request.context_projection.document_version_id
    )


def test_compact_planner_draft_can_use_active_workspace_knowledge() -> None:
    planning_request = request(
        content="公司请假制度是什么？",
        workspace_knowledge_available=True,
    )
    draft = AgentPlannerDraft.model_validate(
        {
            "speech_act": "QUESTION",
            "normalized_question": planning_request.context_projection.current_turn,
            "execution_level": "BOUNDED_QA",
            "information_sources": ["RAG"],
            "answer_type": "DOCUMENT_QA",
            "retrieval_queries": ["请假制度 年假 审批"],
        }
    )

    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())

    assert decision.execution.source_plan.required_sources == ("RAG",)
    assert decision.tool_plan[0].tool_name == "knowledge.hybrid_search"
    assert decision.tool_plan[0].arguments == {"queries": ["请假制度 年假 审批"]}
    assert validate_agent_decision(decision, planning_request, default_agent_tools()) is None


def web_decision() -> AgentTurnDecision:
    return AgentTurnDecision.model_validate(
        {
            "action": "EXECUTE",
            "intent": {
                "speech_act": "QUESTION",
                "normalized_question": "特朗普最近有什么新闻？",
                "entities": ["特朗普"],
                "temporal": {
                    "direction": "CURRENT",
                    "requires_fresh_information": True,
                    "expression": "最近",
                },
            },
            "execution": {
                "execution_level": "BOUNDED_QA",
                "source_plan": {
                    "required_sources": ["WEB"],
                    "forbidden_sources": ["MODEL"],
                },
                "risk": "READ",
                "maximum_tool_steps": 2,
                "confidence": 0.94,
            },
            "answer_contract": {
                "answer_type": "LIST",
                "required_aspects": ["multiple independent recent events"],
                "completeness": "BEST_EFFORT",
                "freshness": "CURRENT",
                "minimum_answer_items": 2,
                "maximum_answer_items": 5,
                "require_explicit_dates": True,
                "minimum_evidence_items": 2,
                "minimum_independent_sources": 2,
                "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
                "organization": "BULLETS",
            },
            "tool_plan": [
                {
                    "tool_name": "web.search",
                    "purpose": "find current independent news reports",
                    "arguments": {"queries": ["特朗普 最近 新闻"]},
                    "risk": "READ",
                }
            ],
        }
    )


def planned_web_research_turn() -> tuple[ResearchTurnCreateInput, AgentTurnDecisionSnapshot]:
    planning_request = request(content="特朗普最近有什么新闻？")
    decision = web_decision()
    user_message_id = uuid4()
    route = _agent_decision_to_chat_route(
        ChatRouteInput(
            workspace_id=planning_request.workspace_id,
            conversation_id=planning_request.conversation_id,
            user_message_id=user_message_id,
            content=planning_request.context_projection.current_turn,
            model_config_id=planning_request.model_binding.config_id,
        ),
        decision,
    )
    assert route.research_plan is not None
    value = ResearchTurnCreateInput.model_validate(
        {
            "workspace_id": planning_request.workspace_id,
            "conversation_id": planning_request.conversation_id,
            "user_message_id": user_message_id,
            "content": planning_request.context_projection.current_turn,
            "goal": {
                "goal": planning_request.context_projection.current_turn,
                "constraints": ["use governed evidence"],
                "deliverables": ["answer"],
                "workspace_id": planning_request.workspace_id,
                "time_budget_sec": 60,
                "token_budget": 24000,
                "cost_budget": 40,
                "requested_skill": "web-research",
                "user_context": json.dumps(
                    {
                        "resolved_request": route.resolved_content,
                        "instruction": route.resolved_content,
                        "research_plan": route.research_plan.model_dump(mode="json"),
                        "agent_decision": decision.model_dump(mode="json"),
                        "model_config_id": str(planning_request.model_binding.config_id),
                    },
                    ensure_ascii=False,
                ),
            },
        }
    )
    snapshot = AgentTurnDecisionSnapshot(
        decision_id=uuid4(),
        workspace_id=planning_request.workspace_id,
        turn_id=uuid4(),
        decision_version=1,
        model_binding=planning_request.model_binding,
        decision=decision,
        content_hash="0" * 64,
    )
    return value, snapshot


@pytest.mark.asyncio
async def test_state_graph_projects_context_and_routes_inline_chat() -> None:
    captured: list[object] = []

    async def planner(messages: object, validator: object) -> AgentTurnDecision:
        captured.append(messages)
        decision = inline_decision()
        assert callable(validator)
        assert validator(decision) is None
        return decision

    result = await plan_agent_turn(
        request(),
        AgentPlannerContext(
            planner=planner,
            tools=default_agent_tools(),
            current_date=datetime.now(UTC).date(),
            timezone="Asia/Shanghai",
        ),
    )
    assert result.execution.execution_level == "INLINE_CHAT"
    assert result.tool_plan[0].tool_name == "model.respond"
    assert captured
    assert "eligible_completed_history" in captured[0][1].content


@pytest.mark.asyncio
async def test_state_graph_uses_summary_only_from_context_projection() -> None:
    planning_request = request(content="继续")
    planning_request = planning_request.model_copy(
        update={
            "context_projection": planning_request.context_projection.model_copy(
                update={"valid_summary": "summary-boundary"}
            )
        }
    )
    captured: list[tuple[Message, ...]] = []

    async def planner(
        messages: tuple[Message, ...],
        _validator: object,
    ) -> AgentTurnDecision:
        captured.append(messages)
        return inline_decision()

    await plan_agent_turn(
        planning_request,
        AgentPlannerContext(
            planner=planner,
            tools=default_agent_tools(),
            current_date=datetime.now(UTC).date(),
            timezone="Asia/Shanghai",
        ),
    )

    assert '"valid_summary": "summary-boundary"' in captured[0][1].content


@pytest.mark.asyncio
async def test_state_graph_accepts_current_web_plan_without_parametric_evidence() -> None:
    async def planner(_messages: object, validator: object) -> AgentTurnDecision:
        decision = web_decision()
        assert callable(validator)
        assert validator(decision) is None
        return decision

    result = await plan_agent_turn(
        request(content="特朗普最近有什么新闻？"),
        AgentPlannerContext(
            planner=planner,
            tools=default_agent_tools(),
            current_date=datetime.now(UTC).date(),
            timezone="Asia/Shanghai",
        ),
    )
    assert result.execution.execution_level == "BOUNDED_QA"
    assert result.execution.source_plan.required_sources == ("WEB",)


def test_planned_research_turn_is_bound_to_the_immutable_agent_decision() -> None:
    value, snapshot = planned_web_research_turn()
    _validate_planned_research_turn(value, snapshot)

    context = json.loads(value.goal.user_context)
    assert isinstance(context, dict)
    research_plan = context["research_plan"]
    assert isinstance(research_plan, dict)
    research_plan["web_queries"] = ["different query"]
    tampered = value.model_copy(
        update={
            "goal": value.goal.model_copy(
                update={"user_context": json.dumps(context, ensure_ascii=False)}
            )
        }
    )
    with pytest.raises(DomainError) as raised:
        _validate_planned_research_turn(tampered, snapshot)
    assert raised.value.code is FailureCode.CONSISTENCY_VIOLATION


def test_policy_rejects_rag_without_selected_document() -> None:
    decision = web_decision().model_copy(
        update={
            "execution": web_decision().execution.model_copy(
                update={
                    "source_plan": web_decision().execution.source_plan.model_copy(
                        update={"required_sources": ("RAG",)}
                    )
                }
            )
        }
    )
    assert (
        validate_agent_decision(decision, request(), default_agent_tools())
        == "RAG requires a selected document or active workspace knowledge"
    )


@pytest.mark.asyncio
async def test_expired_planner_deadline_is_a_typed_terminal_failure() -> None:
    expired = request().model_copy(update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)})

    async def planner(_messages: object, _validator: object) -> AgentTurnDecision:
        raise AssertionError("expired planning must not call the model")

    with pytest.raises(DomainError) as raised:
        await plan_agent_turn(
            expired,
            AgentPlannerContext(
                planner=planner,
                tools=default_agent_tools(),
                current_date=datetime.now(UTC).date(),
                timezone="Asia/Shanghai",
            ),
        )
    assert raised.value.code == FailureCode.BUDGET_EXCEEDED


def test_tool_catalog_exposes_real_capabilities_with_execution_boundaries() -> None:
    tools = {
        item.tool_name: item
        for item in agent_tools_from_capabilities(builtin_descriptors())
    }
    assert "web.search" in tools
    assert "knowledge.hybrid_search" in tools
    assert "artifact.docx" in tools
    assert "model.structured_output" not in tools
    assert tools["web.search"].execution_levels == ("BOUNDED_QA", "DURABLE_TASK")
    assert tools["artifact.docx"].execution_levels == ("DURABLE_TASK",)


@pytest.mark.asyncio
async def test_graph_persists_planner_state_with_a_mainstream_checkpointer() -> None:
    async def planner(_messages: object, validator: object) -> AgentTurnDecision:
        decision = inline_decision()
        assert callable(validator)
        assert validator(decision) is None
        return decision

    graph = build_agent_planning_graph(InMemorySaver())
    thread_id = str(uuid4())
    await plan_agent_turn(
        request(),
        AgentPlannerContext(
            planner=planner,
            tools=default_agent_tools(),
            current_date=datetime.now(UTC).date(),
            timezone="Asia/Shanghai",
        ),
        graph=graph,
        thread_id=thread_id,
    )
    state = await graph.aget_state(
        {
            "configurable": {
                "thread_id": thread_id,
            }
        }
    )
    assert state.values["decision"].execution.execution_level == "INLINE_CHAT"


def test_historical_lookup_does_not_become_current_fact_contract() -> None:
    from enterprise_agent.contracts.domain import AgentInformationSource
    from enterprise_agent.runtime.agent_graph import _answer_contract_payload
    draft = AgentPlannerDraft(
        speech_act="QUESTION", normalized_question="Miami weather last week",
        execution_level="BOUNDED_QA", information_sources=("WEB",),
        requires_fresh_information=True, temporal_direction="HISTORICAL",
        answer_type="SUMMARY", required_aspects=("weather during the requested past week",),
    )
    assert _answer_contract_payload(draft, (AgentInformationSource.WEB,))["freshness"] == "HISTORICAL"


def test_week_summary_has_room_for_all_seven_daily_observations():
    planning_request = request(content="上周每天的天气怎么样")
    draft = AgentPlannerDraft.model_validate({
        "speech_act": "QUESTION", "normalized_question": "2026-09-07至2026-09-13逐日天气",
        "execution_level": "BOUNDED_QA", "information_sources": ["WEB"],
        "requires_fresh_information": True, "temporal_direction": "HISTORICAL",
        "answer_type": "SUMMARY", "required_aspects": ["七天的天气观测"],
        "web_queries": ["daily weather observations 2026-09-07 2026-09-13"],
    })
    decision = materialize_agent_decision(draft, planning_request, default_agent_tools())
    assert decision.answer_contract is not None
    assert decision.answer_contract.maximum_answer_items >= 7
    assert decision.answer_contract.freshness == "HISTORICAL"
