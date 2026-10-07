import json
from datetime import UTC, date, datetime
from importlib import import_module
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_agent.api.app import (
    _bounded_chat_prompt,
    _deterministic_clock_response,
    _deterministic_social_response,
    _direct_chat_output_limit,
    _fallback_agent_planner_draft,
    _model_assisted_chat_route,
    _model_router_needed,
    _research_limit,
    _research_query,
    _route_chat_turn,
    _validate_research_turn_context,
)
from enterprise_agent.api.contracts import ChatRouteInput, ChatRouteView
from enterprise_agent.contracts.domain import (
    BoundedResearchPlan,
    ConversationMessage,
    ProjectMemory,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import ModelGateway
from enterprise_agent.runtime.context_projection import AgentContextProjection


class StubRoutingGateway:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def structured(
        self,
        _messages: object,
        schema: Any,
        _classification: object,
        **kwargs: object,
    ) -> tuple[object, object]:
        result = schema.model_validate(self.payload)
        validator = kwargs.get("validate_result")
        if callable(validator):
            assert validator(result) is None
        return result, object()


class RepairingRoutingGateway:
    def __init__(self, payloads: tuple[dict[str, object], ...]) -> None:
        self.payloads = payloads
        self.validation_errors: list[str] = []

    async def structured(
        self,
        _messages: object,
        schema: Any,
        _classification: object,
        **kwargs: object,
    ) -> tuple[object, object]:
        validator = kwargs.get("validate_result")
        for payload in self.payloads:
            result = schema.model_validate(payload)
            error = validator(result) if callable(validator) else None
            if error is None:
                return result, object()
            self.validation_errors.append(error)
        raise AssertionError("no valid bounded research decision was provided")


class FailIfCalledRoutingGateway:
    async def structured(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        raise AssertionError("semantic planner must not run on the narrow fast path")


def bounded_plan(
    route_name: str,
    *,
    web_queries: tuple[str, ...] = (),
    retrieval_queries: tuple[str, ...] = (),
    answer_type: str = "FACT",
    minimum_answer_items: int | None = 1,
    maximum_answer_items: int | None = 1,
    require_explicit_dates: bool = False,
) -> dict[str, object]:
    required_sources = {
        "WEB": ["PUBLIC_WEB"],
        "RAG": ["SELECTED_DOCUMENT"],
        "MIXED": ["PUBLIC_WEB", "SELECTED_DOCUMENT"],
    }[route_name]
    preferred_sources = (
        ["SELECTED_DOCUMENT"]
        if route_name == "RAG"
        else ["SELECTED_DOCUMENT", "OFFICIAL_OR_FIRST_PARTY", "INDEPENDENT_MEDIA"]
        if route_name == "MIXED"
        else ["OFFICIAL_OR_FIRST_PARTY", "INDEPENDENT_MEDIA"]
    )
    evidence_minimum = 1 if route_name == "RAG" else 2
    return {
        "web_queries": list(web_queries),
        "retrieval_queries": list(retrieval_queries),
        "source_plan": {
            "required_sources": required_sources,
            "preferred_sources": preferred_sources,
            "fallback_sources": [] if route_name == "RAG" else ["GENERAL_WEB"],
            "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
        },
        "result_limit": 4,
        "answer_contract": {
            "answer_type": answer_type,
            "required_aspects": ["direct answer", "material supporting evidence"],
            "completeness": "BEST_EFFORT",
            "freshness": "SOURCE_BOUND" if route_name == "RAG" else "CURRENT",
            "minimum_answer_items": minimum_answer_items,
            "maximum_answer_items": maximum_answer_items,
            "require_explicit_dates": require_explicit_dates,
            "minimum_evidence_items": evidence_minimum,
            "minimum_independent_sources": 1 if route_name == "RAG" else 2,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "CONCISE",
        },
    }


def message(content: str, role: str = "USER") -> ConversationMessage:
    workspace_id = uuid4()
    return ConversationMessage.model_validate(
        {
            "message_id": uuid4(),
            "workspace_id": workspace_id,
            "conversation_id": uuid4(),
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC),
        }
    )


def route(
    content: str,
    *,
    history: tuple[ConversationMessage, ...] = (),
    web_mode: str = "AUTO",
    with_document: bool = False,
    workspace_knowledge_available: bool = False,
) -> ChatRouteView:
    return _route_chat_turn(
        ChatRouteInput.model_validate(
            {
                "workspace_id": uuid4(),
                "conversation_id": uuid4(),
                "content": content,
                "web_mode": web_mode,
                "document_version_id": str(uuid4()) if with_document else None,
            }
        ),
        history,
        workspace_knowledge_available=workspace_knowledge_available,
    )


def test_server_router_keeps_direct_chat_fast_and_routes_changing_information() -> None:
    assert route("晚上好").route == "DIRECT"
    assert route("解释一下汇率是如何形成的").route == "DIRECT"
    assert route("帮我润色这段文字").route == "DIRECT"
    current = route("今天广州有什么最新新闻")
    assert current.route == "WEB"
    assert current.search_query
    assert current.research_plan is not None
    assert current.research_plan.source_plan.required_sources == ("PUBLIC_WEB",)
    assert current.research_plan.answer_contract.minimum_answer_items == 2
    assert current.research_plan.answer_contract.require_explicit_dates
    assert "广州" in current.search_query
    assert route("总结今天的国际新闻").route == "WEB"
    assert route("Python 3.14 发布了吗").route == "WEB"
    assert route("查询当前 Python 稳定版本").route == "WEB"
    assert route("介绍量子力学", web_mode="FORCE").route == "WEB"
    assert route("今天广州有什么最新新闻", web_mode="OFF").route == "DIRECT"


def test_workspace_policy_question_routes_to_active_knowledge_without_planner() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "我想了解一下请假制度",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )

    result = _route_chat_turn(value, (), workspace_knowledge_available=True)

    assert result.route == "RAG"
    assert result.reason == "WORKSPACE_KNOWLEDGE"
    assert result.research_plan is not None
    assert result.research_plan.source_plan.required_sources == ("WORKSPACE_KNOWLEDGE",)
    assert result.research_plan.web_queries == ()
    assert not _model_router_needed(value, result)


def test_exact_social_turn_has_immediate_response_and_small_generation_budget() -> None:
    assert _deterministic_social_response("晚上好") == "晚上好！有什么我可以帮你的吗？"
    assert _direct_chat_output_limit("晚上好", 4096) == 96
    assert _deterministic_social_response("请详细解释这个方案") is None
    assert _direct_chat_output_limit("请详细解释这个方案", 4096) == 1600


def test_exact_clock_turn_uses_authoritative_fast_path_without_planner() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "中午好，现在几点",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )

    result = _route_chat_turn(value, ())

    assert result.route == "DIRECT"
    assert result.reason == "DIRECT_REQUEST"
    assert not _model_router_needed(value, result)
    response = _deterministic_clock_response(value.content)
    assert response is not None
    assert "现在是" in response
    assert "Asia/Shanghai" in response


def test_planner_failure_recovery_preserves_direct_web_and_document_boundaries() -> None:
    base = {
        "workspace_id": uuid4(),
        "conversation_id": uuid4(),
        "model_config_id": uuid4(),
    }
    direct = _fallback_agent_planner_draft(
        ChatRouteInput.model_validate({**base, "content": "介绍一下你自己"}),
        (),
    )
    assert direct.execution_level == "INLINE_CHAT"
    assert direct.information_sources == ()

    current = _fallback_agent_planner_draft(
        ChatRouteInput.model_validate({**base, "content": "今天广州有什么最新新闻"}),
        (),
    )
    assert current.execution_level == "BOUNDED_QA"
    assert current.information_sources == ("WEB",)
    assert current.requires_fresh_information

    document = _fallback_agent_planner_draft(
        ChatRouteInput.model_validate(
            {**base, "content": "总结这份文档", "document_version_id": uuid4()}
        ),
        (),
    )
    assert document.information_sources == ("RAG",)
    assert document.answer_type == "DOCUMENT_SUMMARY"


def test_server_router_resolves_follow_up_subjects_and_refuses_to_guess_missing_context() -> None:
    resolved = route(
        "他最近有什么动态？",
        history=(message("特朗普是谁？"), message("他是一位美国政治人物。", "ASSISTANT")),
    )
    assert resolved.route == "WEB"
    assert resolved.context_resolved
    assert "特朗普" in resolved.resolved_content

    missing = route("未来一周天气怎么样？")
    assert missing.route == "DIRECT"
    assert missing.reason == "MISSING_CONTEXT"
    assert not missing.context_resolved

    topic_switch = route(
        "未来一周天气怎么样？",
        history=(message("特朗普是谁？"), message("他是一位美国政治人物。", "ASSISTANT")),
    )
    assert topic_switch.route == "DIRECT"
    assert topic_switch.reason == "MISSING_CONTEXT"
    assert topic_switch.resolved_content == "未来一周天气怎么样？"
    assert not topic_switch.context_resolved

    same_topic_follow_up = route(
        "那未来一周天气怎么样？",
        history=(
            message("今天东莞的天气怎么样？"),
            message("今天东莞有阵雨。", "ASSISTANT"),
        ),
    )
    assert same_topic_follow_up.route == "WEB"
    assert same_topic_follow_up.context_resolved
    assert "东莞" in same_topic_follow_up.resolved_content
    assert same_topic_follow_up.search_query is not None
    assert "东莞" in same_topic_follow_up.search_query


def test_picture_conversation_keeps_the_latest_topic_after_a_compound_history_question() -> None:
    history = (
        message("上周五东莞的天气怎么样"),
        message("上周五东莞天气晴朗。", "ASSISTANT"),
        message("过去一周东莞的天气怎么样"),
        message("过去一周东莞以多云为主。", "ASSISTANT"),
        message("未来一周广州的天气怎么样"),
        message("未来一周广州可能有雷雨。", "ASSISTANT"),
        message("我刚刚问了哪个城市上周五的天气，另外特朗普是谁"),
        message("你问的是东莞；特朗普是美国政治人物。", "ASSISTANT"),
    )

    recall = route(
        "我刚刚问了哪个城市上周五的天气，另外特朗普是谁",
        history=history[:-2],
    )
    assert recall.route == "DIRECT"

    follow_up = route("他最近有什么新动态？", history=history)
    assert follow_up.route == "WEB"
    assert follow_up.context_resolved
    assert "特朗普" in follow_up.resolved_content
    assert "广州" not in follow_up.resolved_content
    assert "东莞" not in follow_up.resolved_content
    assert follow_up.search_query is not None
    assert "特朗普" in follow_up.search_query
    assert "有什么" not in follow_up.search_query
    assert "：" not in follow_up.search_query
    assert "2026-" not in follow_up.search_query
    assert follow_up.search_query == "特朗普 最新动态"


def test_topic_switch_and_explicit_correction_do_not_reuse_an_older_relation() -> None:
    history = (
        message("你知道迈克尔杰克逊吗"),
        message("知道。", "ASSISTANT"),
        message("他的音乐版权现在每年还有收入吗，是谁在收这些钱"),
        message("有收入，由相关权利方管理。", "ASSISTANT"),
        message("那你知道特朗普吗"),
        message("知道。", "ASSISTANT"),
    )

    follow_up = route("他最近有什么新动态", history=history)
    assert follow_up.route == "WEB"
    assert follow_up.resolved_content == "关于特朗普：最近有什么新动态"
    assert follow_up.search_query == "特朗普 最新动态"

    correction = route("我是说特朗普的新动态", history=history[:-2])
    assert correction.route == "WEB"
    assert correction.search_query == "特朗普 最新动态"

    corrected_follow_up = route(
        "那他有什么最新新闻吗",
        history=(*history[:-2], message("我是说特朗普的新动态")),
    )
    assert corrected_follow_up.route == "WEB"
    assert corrected_follow_up.search_query == "特朗普 最新新闻"


def test_relation_follow_up_resolves_the_entity_before_building_a_search_query() -> None:
    result = route(
        "他的音乐版权现在每年还有收入吗，是谁在收这些钱",
        history=(message("你知道迈克尔杰克逊吗"),),
    )

    assert result.route == "WEB"
    assert result.context_resolved
    assert result.resolved_content.startswith("关于迈克尔杰克逊：")
    assert result.search_query is not None
    assert result.search_query.startswith("迈克尔杰克逊 音乐版权")
    assert not result.search_query.startswith("他 ")


def test_broad_or_missing_location_forecasts_request_clarification() -> None:
    for content in ("未来一周天气怎么样", "未来一周美国的天气怎么样"):
        result = route(content)
        assert result.route == "DIRECT"
        assert result.reason == "MISSING_CONTEXT"
        assert not result.context_resolved

    specific = route("美国华盛顿未来一周的天气怎么样")
    assert specific.route == "WEB"
    assert specific.search_query is not None
    assert "美国华盛顿" in specific.search_query


def test_time_sensitive_ownership_and_income_questions_use_current_evidence() -> None:
    result = route(
        "他的音乐版权现在每年还有收入吗，是谁在收这些钱",
        history=(message("你知道迈克尔杰克逊吗"),),
    )

    assert result.route == "WEB"
    assert result.reason == "CURRENT_INFORMATION"


def test_server_router_separates_selected_document_and_mixed_evidence() -> None:
    rag = route("总结附件", with_document=True)
    mixed = route("结合附件和最新公开信息核对变化", with_document=True)

    assert rag.route == "RAG"
    assert rag.research_plan is not None
    assert rag.research_plan.source_plan.required_sources == ("SELECTED_DOCUMENT",)
    assert rag.research_plan.answer_contract.minimum_answer_items is None
    assert rag.research_plan.answer_contract.maximum_answer_items is None
    assert mixed.route == "MIXED"
    assert mixed.research_plan is not None
    assert set(mixed.research_plan.source_plan.required_sources) == {
        "PUBLIC_WEB",
        "SELECTED_DOCUMENT",
    }


async def test_model_router_handles_ambiguous_stable_knowledge_without_forcing_web() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "你知道广州大学吗？",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )
    result = await _model_assisted_chat_route(
        value,
        (),
        cast(
            ModelGateway,
            StubRoutingGateway(
                {
                    "resolved_request": "介绍广州大学的基本情况",
                    "needs_fresh_information": False,
                    "missing_information": [],
                    "research_plan": None,
                    "confidence": 0.95,
                }
            ),
        ),
    )

    assert result.route == "DIRECT"
    assert result.reason == "MODEL_ROUTER"
    assert result.resolved_content == "介绍广州大学的基本情况"


async def test_model_router_plans_a_clean_current_information_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        import_module("enterprise_agent.api.app"),
        "_local_today",
        lambda: date(2026, 9, 14),
    )
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "今天广州黄埔的天气怎么样？",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )
    result = await _model_assisted_chat_route(
        value,
        (),
        cast(
            ModelGateway,
            StubRoutingGateway(
                {
                    "resolved_request": "查询今天广州黄埔区的天气",
                    "needs_fresh_information": True,
                    "missing_information": [],
                    "research_plan": bounded_plan(
                        "WEB",
                        web_queries=(
                            "广州黄埔区 今日天气预报 气象",
                            "广州黄埔区 天气预报",
                            "Guangzhou Huangpu weather forecast",
                        ),
                        answer_type="FORECAST",
                        minimum_answer_items=1,
                        maximum_answer_items=7,
                        require_explicit_dates=True,
                    ),
                    "confidence": 0.98,
                }
            ),
        ),
    )

    assert result.route == "WEB"
    assert result.search_query is not None
    assert "广州黄埔区" in result.search_query
    assert "天气" in result.search_query
    assert "怎么样" not in result.search_query
    assert result.search_queries == (
        "广州黄埔区 今日天气预报 气象 2026-09-14",
        "广州黄埔区 天气预报",
        "Guangzhou Huangpu weather forecast",
    )
    assert result.research_plan is not None
    assert result.research_plan.answer_contract.require_explicit_dates


async def test_model_router_cannot_change_the_users_reply_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        import_module("enterprise_agent.api.app"),
        "_local_today",
        lambda: date(2026, 9, 14),
    )
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "日本首相最近有哪些公开活动？",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )
    result = await _model_assisted_chat_route(
        value,
        (),
        cast(
            ModelGateway,
            StubRoutingGateway(
                {
                    "resolved_request": "What recent public activities involved Japan's PM?",
                    "needs_fresh_information": True,
                    "missing_information": [],
                    "research_plan": bounded_plan(
                        "WEB",
                        web_queries=(
                            "Japan prime minister recent public activities",
                            "日本 首相 最近 公开活动",
                        ),
                        answer_type="LIST",
                        minimum_answer_items=2,
                        maximum_answer_items=4,
                        require_explicit_dates=True,
                    ),
                    "confidence": 0.97,
                }
            ),
        ),
    )

    assert result.route == "WEB"
    assert result.resolved_content == "日本首相最近有哪些公开活动？"
    assert result.search_queries[0].startswith("Japan prime minister recent public activities")


@pytest.mark.parametrize(
    ("content", "route_name", "needs_fresh_information", "web_queries"),
    [
        ("总结附件", "RAG", False, ()),
        (
            "结合附件和最新公开信息核对变化",
            "MIXED",
            True,
            ("附件主题 最新公开变化", "附件主题 官方更新"),
        ),
    ],
)
async def test_model_plan_preserves_selected_document_route_and_source_boundary(
    content: str,
    route_name: str,
    needs_fresh_information: bool,
    web_queries: tuple[str, ...],
) -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": content,
            "web_mode": "AUTO",
            "document_version_id": uuid4(),
            "model_config_id": uuid4(),
        }
    )
    plan = bounded_plan(
        route_name,
        web_queries=web_queries,
        retrieval_queries=("检索附件中与用户问题直接相关的段落",),
        answer_type="DOCUMENT_SUMMARY" if route_name == "RAG" else "COMPARISON",
        minimum_answer_items=None if route_name == "RAG" else 2,
        maximum_answer_items=None if route_name == "RAG" else 4,
        require_explicit_dates=route_name == "MIXED",
    )

    result = await _model_assisted_chat_route(
        value,
        (),
        cast(
            ModelGateway,
            StubRoutingGateway(
                {
                    "resolved_request": content,
                    "needs_fresh_information": needs_fresh_information,
                    "missing_information": [],
                    "research_plan": plan,
                    "confidence": 0.96,
                }
            ),
        ),
    )

    assert result.route == route_name
    assert result.research_plan is not None
    assert result.research_plan.retrieval_queries
    assert set(result.research_plan.source_plan.required_sources) == set(
        plan["source_plan"]["required_sources"]  # type: ignore[index]
    )


async def test_model_plan_repairs_an_attempt_to_escape_the_selected_document_boundary() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "总结附件",
            "web_mode": "AUTO",
            "document_version_id": uuid4(),
            "model_config_id": uuid4(),
        }
    )
    invalid_plan = bounded_plan(
        "RAG",
        retrieval_queries=("总结附件",),
        answer_type="DOCUMENT_SUMMARY",
        minimum_answer_items=None,
        maximum_answer_items=None,
    )
    invalid_plan["source_plan"] = {
        "required_sources": ["PUBLIC_WEB"],
        "preferred_sources": ["OFFICIAL_OR_FIRST_PARTY"],
        "fallback_sources": ["GENERAL_WEB"],
        "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
    }
    valid_plan = bounded_plan(
        "RAG",
        retrieval_queries=("总结附件",),
        answer_type="DOCUMENT_SUMMARY",
        minimum_answer_items=None,
        maximum_answer_items=None,
    )
    gateway = RepairingRoutingGateway(
        (
            {
                "resolved_request": "总结附件",
                "needs_fresh_information": False,
                "missing_information": [],
                "research_plan": invalid_plan,
                "confidence": 0.95,
            },
            {
                "resolved_request": "总结附件",
                "needs_fresh_information": False,
                "missing_information": [],
                "research_plan": valid_plan,
                "confidence": 0.95,
            },
        )
    )

    result = await _model_assisted_chat_route(
        value,
        (),
        cast(ModelGateway, gateway),
    )

    assert result.route == "RAG"
    assert result.research_plan is not None
    assert result.research_plan.source_plan.required_sources == ("SELECTED_DOCUMENT",)
    assert gateway.validation_errors == [
        "required_sources must exactly match the deterministic research route"
    ]


async def test_model_plan_cannot_downgrade_an_explicit_document_summary() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "总结附件",
            "web_mode": "AUTO",
            "document_version_id": uuid4(),
            "model_config_id": uuid4(),
        }
    )
    wrong_type = bounded_plan(
        "RAG",
        retrieval_queries=("附件主要内容",),
        answer_type="DOCUMENT_QA",
    )
    valid_summary = bounded_plan(
        "RAG",
        retrieval_queries=("附件主要内容",),
        answer_type="DOCUMENT_SUMMARY",
        minimum_answer_items=None,
        maximum_answer_items=None,
    )
    gateway = RepairingRoutingGateway(
        (
            {
                "resolved_request": "总结附件",
                "needs_fresh_information": False,
                "missing_information": [],
                "research_plan": wrong_type,
                "confidence": 0.95,
            },
            {
                "resolved_request": "总结附件",
                "needs_fresh_information": False,
                "missing_information": [],
                "research_plan": valid_summary,
                "confidence": 0.95,
            },
        )
    )

    result = await _model_assisted_chat_route(
        value,
        (),
        cast(ModelGateway, gateway),
    )

    assert result.research_plan is not None
    assert result.research_plan.answer_contract.answer_type == "DOCUMENT_SUMMARY"
    assert gateway.validation_errors == [
        "an explicit selected-document summary must remain DOCUMENT_SUMMARY"
    ]


def test_bounded_research_plan_rejects_executable_or_permission_fields() -> None:
    payload = bounded_plan("WEB", web_queries=("Acme current official announcement",))
    payload["capability_id"] = "web.search"
    with pytest.raises(ValidationError):
        BoundedResearchPlan.model_validate(payload)

    missing_item_bounds = bounded_plan(
        "WEB",
        web_queries=("Acme current official announcement",),
        minimum_answer_items=None,
        maximum_answer_items=None,
    )
    with pytest.raises(ValidationError):
        BoundedResearchPlan.model_validate(missing_item_bounds)

    reversed_item_bounds = bounded_plan(
        "WEB",
        web_queries=("Acme current official announcement",),
        minimum_answer_items=5,
        maximum_answer_items=2,
    )
    with pytest.raises(ValidationError):
        BoundedResearchPlan.model_validate(reversed_item_bounds)

    source_plan = cast(dict[str, object], payload["source_plan"])
    source_plan["permission"] = "ALLOW_WRITE"
    payload.pop("capability_id")
    with pytest.raises(ValidationError):
        BoundedResearchPlan.model_validate(payload)


def test_research_turn_revalidates_plan_route_and_document_boundary() -> None:
    valid_web = {"research_plan": bounded_plan("WEB", web_queries=("today news",))}
    _validate_research_turn_context(json.dumps(valid_web), "web-research")

    tampered: dict[str, object] = {
        "research_plan": bounded_plan("RAG", retrieval_queries=("summary",))
    }
    with pytest.raises(DomainError):
        _validate_research_turn_context(json.dumps(tampered), "web-research")
    with pytest.raises(DomainError):
        _validate_research_turn_context(json.dumps(tampered), "document-analysis")

    tampered["document_version_id"] = str(uuid4())
    _validate_research_turn_context(json.dumps(tampered), "document-analysis")

    workspace_plan = bounded_plan("RAG", retrieval_queries=("请假制度",))
    workspace_plan["source_plan"] = {
        "required_sources": ["WORKSPACE_KNOWLEDGE"],
        "preferred_sources": ["WORKSPACE_KNOWLEDGE"],
        "fallback_sources": [],
        "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
    }
    workspace_context: dict[str, object] = {"research_plan": workspace_plan}
    _validate_research_turn_context(json.dumps(workspace_context), "document-analysis")
    workspace_context["document_version_id"] = str(uuid4())
    with pytest.raises(DomainError, match="cannot include a selected document"):
        _validate_research_turn_context(json.dumps(workspace_context), "document-analysis")


def test_high_confidence_direct_chat_does_not_call_the_semantic_planner() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "晚上好",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )
    deterministic = _route_chat_turn(value, ())

    assert deterministic.route == "DIRECT"
    assert not _model_router_needed(value, deterministic)


@pytest.mark.parametrize(
    ("content", "with_document"),
    [
        ("Python 当前版本是什么？", False),
        ("附件中的工作时间是什么？", True),
    ],
)
def test_narrow_research_fact_uses_the_rule_fast_path(
    content: str,
    with_document: bool,
) -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": content,
            "web_mode": "AUTO",
            "document_version_id": uuid4() if with_document else None,
            "model_config_id": uuid4(),
        }
    )
    deterministic = _route_chat_turn(value, ())

    assert deterministic.route == ("RAG" if with_document else "WEB")
    assert not _model_router_needed(value, deterministic)


def test_three_part_elliptical_question_uses_the_semantic_planner() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "日本首相是谁，那天皇呢？还有澳大利亚最高领导人是谁？",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )
    deterministic = _route_chat_turn(value, ())
    assert _model_router_needed(value, deterministic)


@pytest.mark.parametrize(
    ("content", "with_document"),
    [
        ("特朗普最近有哪些新闻？", False),
        ("明天广州黄埔天气怎么样？", False),
        ("总结附件", True),
    ],
)
def test_complex_research_keeps_the_upfront_semantic_planner(
    content: str,
    with_document: bool,
) -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": content,
            "web_mode": "AUTO",
            "document_version_id": uuid4() if with_document else None,
            "model_config_id": uuid4(),
        }
    )
    deterministic = _route_chat_turn(value, ())

    assert deterministic.route in {"WEB", "RAG"}
    assert _model_router_needed(value, deterministic)


async def test_narrow_fact_does_not_invoke_the_semantic_planner_gateway() -> None:
    value = ChatRouteInput.model_validate(
        {
            "workspace_id": uuid4(),
            "conversation_id": uuid4(),
            "content": "Python 当前版本是什么？",
            "web_mode": "AUTO",
            "model_config_id": uuid4(),
        }
    )

    result = await _model_assisted_chat_route(
        value,
        (),
        cast(ModelGateway, FailIfCalledRoutingGateway()),
    )

    assert result.route == "WEB"
    assert result.research_plan is not None
    assert result.research_plan.answer_contract.answer_type == "FACT"


def test_chat_prompt_is_bounded_and_keeps_the_latest_turns() -> None:
    history = tuple(message(f"turn-{index}-" + "x" * 100) for index in range(30))
    prompt = _bounded_chat_prompt(history, max_messages=4, max_characters=1000)

    assert prompt[0].role == "system"
    assert len(prompt) == 5
    assert prompt[-1].content.startswith("turn-29-")
    assert all("turn-0-" not in item.content for item in prompt)


def test_chat_prompt_can_add_one_turn_clarification_guidance() -> None:
    prompt = _bounded_chat_prompt(
        (message("未来一周天气怎么样"),),
        turn_guidance="Ask exactly one brief clarification question.",
    )

    assert "For this turn only" in prompt[0].content
    assert "exactly one brief clarification question" in prompt[0].content


def test_chat_prompt_consumes_summary_and_structured_memory_from_projection() -> None:
    workspace_id = uuid4()
    projection = AgentContextProjection(
        current_turn="继续",
        valid_summary="用户正在讨论请假制度。",
        structured_memory=(
            ProjectMemory(
                memory_id=uuid4(),
                workspace_id=workspace_id,
                content="用户偏好简洁回答",
                type="PREFERENCE",
                source="completed-turn",
                confidence=0.9,
                created_at=datetime.now(UTC),
            ),
        ),
    )

    prompt = _bounded_chat_prompt(
        (message("继续"),),
        context_projection=projection,
    )

    assert prompt[1].role == "user"
    assert prompt[1].content.startswith("<context_data>")
    assert "用户正在讨论请假制度" in prompt[1].content
    assert "用户偏好简洁回答" in prompt[1].content
    assert "background data only" in prompt[0].content


def test_research_query_resolves_time_without_topic_specific_rewrites() -> None:
    today = date(2026, 9, 13)

    assert (
        _research_query(
            "请联网核实 Acme 明天发布什么产品？",
            today=today,
        )
        == "Acme 发布什么产品 2026-09-14"
    )
    assert _research_query(
        "查询最近一周广州的人工智能政策",
        today=today,
    ).endswith("2026-09-07 至 2026-09-13")
    assert (
        _research_query(
            "上周五东莞的天气怎么样",
            today=today,
        )
        == "东莞 天气 历史天气 2026-09-11"
    )
    assert (
        _research_query(
            "未来一周广州的天气怎么样",
            today=today,
        )
        == "广州 天气 天气预报 逐日预报 2026-09-13 至 2026-09-19"
    )


def test_research_query_infers_authoritative_document_class_without_a_site() -> None:
    assert _research_query("2024年广东的年降雨量是多少？") == (
        "2024年广东 年降雨量 年平均降水量 气候公报"
    )
    assert "统计公报" in _research_query("2024年广东人口是多少？")
    assert "财务报告" in _research_query("Acme 2025年度净利润是多少？")
    assert "政策文件" in _research_query("这项人工智能政策的原文是什么？")
    assert "发布说明" in _research_query("Acme 3.2版本更新了什么？")


def test_research_depth_is_based_on_request_shape_not_topic() -> None:
    assert _research_limit("比较三家公司的最新财报并说明差异") == 5
    assert _research_limit("Acme 的现任 CEO 是谁？") == 3
    assert _research_limit("说明 Acme 最近的产品变化") == 4


@pytest.mark.parametrize("mode,content", [
    ("AUTO", "请联网查询某研究机构如何解释两种现象的区别，用中文说明"),
    ("FORCE", "解释这两种公开技术方案的区别"),
])
def test_explicit_research_explanation_keeps_query_planning(mode, content):
    value = ChatRouteInput(workspace_id=uuid4(), conversation_id=uuid4(),
                           model_config_id=uuid4(), web_mode=mode, content=content)
    deterministic = _route_chat_turn(value, ())
    assert deterministic.route == "WEB"
    assert _model_router_needed(value, deterministic)


async def test_planner_model_timeout_leaves_outer_deadline_for_recovery():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from enterprise_agent.api.app import _await_planner_model
    from enterprise_agent.contracts.domain import FailureCode
    from enterprise_agent.domain.states import DomainError
    cancelled = asyncio.Event()
    async def stalled():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    deadline = datetime.now(UTC) + timedelta(seconds=0.2)
    async with asyncio.timeout(0.3):
        with pytest.raises(DomainError) as error:
            await _await_planner_model(stalled, deadline)
        assert error.value.code == FailureCode.LLM_TIMEOUT
        assert cancelled.is_set()
        assert datetime.now(UTC) < deadline


async def test_planner_model_success_and_non_timeout_failure_are_preserved():
    from datetime import UTC, datetime, timedelta

    from enterprise_agent.api.app import _await_planner_model
    async def succeeds():
        return "real result"
    async def fails():
        raise ValueError("invalid configuration")
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    assert await _await_planner_model(succeeds, deadline) == "real result"
    with pytest.raises(ValueError, match="invalid configuration"):
        await _await_planner_model(fails, deadline)
