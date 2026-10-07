import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from enterprise_agent.api.app import _web_task_result_content
from enterprise_agent.capabilities.builtins import (
    DocumentAnswerStep,
    DocumentStepsAnswer,
    EvidenceSupport,
    GroundedAnswerClaim,
    WebStructuredAnswer,
    _analysis_evidence_payload,
    _analysis_system_prompt,
    _answer_core_coverage_error,
    _apply_research_source_plan,
    _apply_web_evidence_selection,
    _current_role_refusal_ignores_official_evidence,
    _default_current_event_bounds,
    _document_steps_to_grounded_answer,
    _document_summary_coverage,
    _extractive_document_fallback,
    _extractive_document_summary_fallback,
    _extractive_official_forecast_fallback,
    _extractive_web_fallback,
    _forecast_evidence_excerpt,
    _grounded_answer_error,
    _grounded_partial_answer,
    _grounded_validation_fallback,
    _invalid_web_envelope_fallback,
    _normalize_web_analysis,
    _normalize_web_citations,
    _requested_answer_bounds,
    _requested_answer_minimum,
    _resolved_web_query,
    _web_evidence_substantive_characters,
)
from enterprise_agent.contracts.domain import (
    AnswerContract,
    BoundedResearchPlan,
    Evidence,
    SourcePlan,
    StructuredAnalysis,
)
from enterprise_agent.domain.states import DomainError


def evidence(
    *,
    evidence_id: UUID,
    workspace_id: UUID,
    task_id: UUID,
    source_type: str,
    source_id: str,
    locator: str,
    content: str,
    derived_from: tuple[UUID, ...] = (),
) -> Evidence:
    digest = hashlib.sha256(content.encode()).hexdigest()
    return Evidence.model_validate(
        {
            "evidence_id": evidence_id,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "step_id": uuid4(),
            "source_type": source_type,
            "source_id": source_id,
            "locator": locator,
            "content": content,
            "content_hash": digest,
            "source_hash": digest,
            "source_version": "1.0.0",
            "confidence": 1,
            "relevance": 1,
            "retrieved_at": datetime.now(UTC),
            "generated_by": uuid4(),
            "derived_from": derived_from,
        }
    )


def persisted_analysis_payload(analysis: StructuredAnalysis) -> dict[str, object]:
    payload = analysis.model_dump(mode="json")
    claims = payload["claims"]
    assert isinstance(claims, list)
    payload["claims"] = [
        {**claim, "claim_id": str(uuid4())} for claim in claims if isinstance(claim, dict)
    ]
    return payload


def research_plan(
    *,
    required_sources: tuple[str, ...],
    preferred_sources: tuple[str, ...],
    fallback_sources: tuple[str, ...] = (),
    forbidden_sources: tuple[str, ...] = ("PARAMETRIC_KNOWLEDGE",),
) -> BoundedResearchPlan:
    return BoundedResearchPlan.model_validate(
        {
            "web_queries": ["current Acme announcement"]
            if "PUBLIC_WEB" in required_sources
            else [],
            "retrieval_queries": ["selected document Acme policy"]
            if "SELECTED_DOCUMENT" in required_sources
            else [],
            "source_plan": SourcePlan.model_validate(
                {
                    "required_sources": required_sources,
                    "preferred_sources": preferred_sources,
                    "fallback_sources": fallback_sources,
                    "forbidden_sources": forbidden_sources,
                }
            ),
            "result_limit": 4,
            "answer_contract": AnswerContract.model_validate(
                {
                    "answer_type": "FACT",
                    "required_aspects": ["direct answer"],
                    "completeness": "BEST_EFFORT",
                    "freshness": "CURRENT",
                    "minimum_answer_items": 1,
                    "maximum_answer_items": 1,
                    "require_explicit_dates": False,
                    "minimum_evidence_items": 1,
                    "minimum_independent_sources": 1,
                    "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
                    "organization": "CONCISE",
                }
            ),
        }
    )


def test_source_plan_only_narrows_and_prioritizes_eligible_evidence() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    document = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="document-version-1",
        locator="page:1",
        content="Selected document policy text.",
    )
    editorial = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://www.reuters.com/world/acme",
        locator="https://www.reuters.com/world/acme",
        content="Acme current announcement reported by an independent publisher.",
    )
    user_generated = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://baijiahao.baidu.com/acme",
        locator="https://baijiahao.baidu.com/acme",
        content="A user-generated retelling of the Acme announcement.",
    )
    plan = research_plan(
        required_sources=("PUBLIC_WEB",),
        preferred_sources=("INDEPENDENT_MEDIA",),
        fallback_sources=("GENERAL_WEB",),
        forbidden_sources=("USER_GENERATED", "PARAMETRIC_KNOWLEDGE"),
    )

    selected = _apply_research_source_plan(
        (document, user_generated, editorial),
        plan,
        requested_skill="web-research",
        query="Acme latest announcement",
    )

    assert selected == (editorial,)
    with pytest.raises(DomainError, match="does not match the frozen Task skill"):
        _apply_research_source_plan(
            (document, editorial),
            plan,
            requested_skill="document-analysis",
            query="Acme policy",
        )


def test_grounding_normalization_preserves_the_models_natural_answer_shape() -> None:
    workspace_id, task_id, evidence_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=evidence_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://www.reuters.com/world/acme",
        locator="https://www.reuters.com/world/acme",
        content="Acme announced Alpha on 2026-09-14 and Beta on 2026-09-15.",
    )
    natural_answer = (
        "最新两项进展：\n\n"
        "- **Alpha**：Acme 于 2026-09-14 公布。\n"
        "- **Beta**：Acme 于 2026-09-15 公布。"
    )
    candidate = WebStructuredAnswer(
        answer=natural_answer,
        answer_status="ANSWERED",
        evidence_ids=(evidence_id,),
        claims=(
            GroundedAnswerClaim(
                content="Acme 于 2026-09-14 公布 Alpha。",
                supports=(
                    EvidenceSupport(
                        evidence_id=evidence_id,
                        quote="Acme announced Alpha on 2026-09-14",
                    ),
                ),
            ),
            GroundedAnswerClaim(
                content="Acme 于 2026-09-15 公布 Beta。",
                supports=(
                    EvidenceSupport(
                        evidence_id=evidence_id,
                        quote="Beta on 2026-09-15",
                    ),
                ),
            ),
        ),
    )

    assert _normalize_web_citations(candidate, (source,)).answer == natural_answer


def test_web_task_result_is_built_from_real_evidence_and_citations() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    web = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://weather.cma.cn/web/weather/59287.html",
        locator="https://weather.cma.cn/web/weather/59287.html",
        content="广州今天有阵雨，最高气温 29 摄氏度。",
    )
    payload = persisted_analysis_payload(
        _normalize_web_analysis(
            WebStructuredAnswer(
                answer="广州今天有阵雨。",
                answer_status="ANSWERED",
                evidence_ids=(source_id,),
                claims=(
                    GroundedAnswerClaim(
                        content="广州今天有阵雨，最高气温约 29 摄氏度。",
                        supports=(
                            EvidenceSupport(
                                evidence_id=source_id,
                                quote="广州今天有阵雨，最高气温 29 摄氏度。",
                            ),
                        ),
                    ),
                ),
            )
        )
    )
    model = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DERIVED",
        source_id="model.structured_output",
        locator="model:model.structured_output",
        content=json.dumps(payload, ensure_ascii=False),
        derived_from=(source_id,),
    )

    result = _web_task_result_content((web, model), task_id)

    assert "广州今天有阵雨" in result
    assert "要点：" not in result
    assert "依据：" not in result
    assert "https://weather.cma.cn/web/weather/59287.html" in result
    assert str(task_id) not in result


def test_web_task_result_rejects_missing_model_evidence() -> None:
    with pytest.raises(DomainError, match="no model result Evidence"):
        _web_task_result_content((), uuid4())


def test_web_no_results_is_a_grounded_outcome_without_source_links() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    web = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://irrelevant.example/item",
        locator="https://irrelevant.example/item",
        content="This result does not identify the requested subject.",
    )
    raw = WebStructuredAnswer(
        answer="没有找到足够可靠的信息。",
        answer_status="NEEDS_CLARIFICATION",
        evidence_ids=(source_id,),
    )
    analysis = _normalize_web_analysis(raw)
    assert analysis.decision == "ADMIT"
    assert analysis.evidence_sufficient is True
    assert analysis.missing_information == ("The question requires additional context.",)
    payload = persisted_analysis_payload(analysis)
    model = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DERIVED",
        source_id="model.structured_output",
        locator="model:model.structured_output",
        content=json.dumps(payload, ensure_ascii=False),
        derived_from=(source_id,),
    )

    result = _web_task_result_content((web, model), task_id)

    assert "补充更具体的对象、地点或时间范围" in result
    assert "irrelevant.example" not in result


def test_resolved_web_query_preserves_normalized_search_context() -> None:
    assert (
        _resolved_web_query(
            json.dumps(
                {
                    "query": "东莞 历史天气 2026-09-04",
                    "model_config_id": str(uuid4()),
                }
            )
        )
        == "东莞 历史天气 2026-09-04"
    )
    assert _resolved_web_query("{}") is None
    assert _resolved_web_query("not-json") is None


def test_no_reliable_results_does_not_add_an_unrelated_clarification() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    web = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://example.com/monthly",
        locator="https://example.com/monthly",
        content="Only a monthly figure is available.",
    )
    analysis = _normalize_web_analysis(
        WebStructuredAnswer(
            answer="没有找到口径匹配的年度数据。",
            answer_status="NO_RELIABLE_RESULTS",
            evidence_ids=(source_id,),
        )
    )
    assert analysis.evidence_sufficient is False
    model = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DERIVED",
        source_id="model.structured_output",
        locator="model:model.structured_output",
        content=json.dumps(persisted_analysis_payload(analysis), ensure_ascii=False),
        derived_from=(source_id,),
    )

    result = _web_task_result_content((web, model), task_id)

    assert result == "没有找到口径匹配的年度数据。"


def test_no_results_with_dated_query_persists_no_claim() -> None:
    source_id = uuid4()
    analysis = _normalize_web_analysis(
        WebStructuredAnswer(
            answer=("无法确认现在的日本首相是谁；检索日本首相 2026年9月没有返回结果。"),
            answer_status="NO_RELIABLE_RESULTS",
            evidence_ids=(source_id,),
        )
    )

    assert analysis.summary.startswith("无法确认现在的日本首相")
    assert analysis.claims == ()


def test_grounding_repair_fallback_is_honest_and_uses_only_real_evidence() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    sources = (
        evidence(
            evidence_id=first_id,
            workspace_id=workspace_id,
            task_id=task_id,
            source_type="WEB",
            source_id="web.search",
            locator="https://one.example/topic",
            content="A related result without enough corroboration.",
        ),
        evidence(
            evidence_id=second_id,
            workspace_id=workspace_id,
            task_id=task_id,
            source_type="WEB",
            source_id="web.search",
            locator="https://two.example/other",
            content="Another related result that does not support the same fact.",
        ),
    )

    fallback = _grounded_validation_fallback("他最近有什么新动态？", sources)

    assert fallback.answer_status == "NO_RELIABLE_RESULTS"
    assert fallback.claims == ()
    assert fallback.evidence_ids == (first_id, second_id)
    assert "独立交叉验证" in fallback.answer
    assert _grounded_answer_error(fallback, sources, "某人 最新动态") is None


def test_grounding_repair_fallback_matches_english_question_language() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://example.com/topic",
        content="A related result.",
    )

    fallback = _grounded_validation_fallback("What changed recently?", (source,))

    assert fallback.answer_status == "NO_RELIABLE_RESULTS"
    assert fallback.answer.startswith("I found public pages")


def test_document_model_timeout_retains_relevant_procedure_with_exact_citation() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator='{"mode":"DOCUMENT_NEIGHBORHOOD","selected_ordinals":[7,8]}',
        content=(
            "# 请假申请\n\n"
            "员工请假应先填写申请表，提交直属主管审批，并在批准后通知人力资源部备案。"
            "病假还应提供医疗证明，紧急情况可先联系主管，返岗后补齐材料。\n\n"
            "其他与请假无关的说明仅用于检查检索片段筛选是否保持问题相关。"
        ),
    )

    extracted = _extractive_document_fallback((source,), "请假流程是什么？")
    assert extracted is not None
    assert _grounded_answer_error(extracted, (source,), "请假流程是什么？") is None
    fallback = _invalid_web_envelope_fallback(
        (source,), "请假流程是什么？", "请假流程是什么？", None, maximum_items=None
    )

    assert fallback.answer_status == "ANSWERED"
    assert "提交直属主管审批" in fallback.answer
    assert "公开页面" not in fallback.answer
    assert fallback.evidence_ids == (source_id,)
    assert _grounded_answer_error(fallback, (source,), "请假流程是什么？") is None


def test_compact_document_steps_become_a_grounded_answer_without_duplicate_json() -> None:
    item = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator="page:2",
        content="员工请假应先提交申请。部门负责人审批后人力资源部复核。",
    )
    result = _document_steps_to_grounded_answer(
        DocumentStepsAnswer(
            steps=(
                DocumentAnswerStep(
                    content="请假应先提交申请。",
                    evidence_id=item.evidence_id,
                    quote="员工请假应先提交申请。",
                ),
                DocumentAnswerStep(
                    content="部门负责人审批后由人力资源部复核。",
                    evidence_id=item.evidence_id,
                    quote="部门负责人审批后人力资源部复核。",
                ),
            )
        )
    )

    assert result.answer_status == "ANSWERED"
    assert result.answer.count("申请") == 1
    assert _grounded_answer_error(result, (item,), "请假流程是什么？") is None


def test_two_city_forecast_cannot_pass_with_only_one_city_named() -> None:
    source_id = uuid4()
    contract = AnswerContract.model_validate(
        {
            "answer_type": "FORECAST",
            "required_aspects": ["2026年9月18日东莞天气", "2026年9月18日广州天气"],
            "completeness": "BEST_EFFORT",
            "freshness": "CURRENT",
            "minimum_answer_items": 1,
            "maximum_answer_items": 7,
            "require_explicit_dates": False,
            "minimum_evidence_items": 1,
            "minimum_independent_sources": 1,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "STRUCTURED_SUMMARY",
        }
    )
    partial = WebStructuredAnswer(
        answer="东莞天气明天多云，25℃至33℃。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="东莞明天多云，25℃至33℃。",
                supports=(EvidenceSupport(evidence_id=source_id, quote="东莞多云25℃33℃"),),
            ),
        ),
    )

    assert "广州" in str(_answer_core_coverage_error(partial, "东莞和广州天气", contract))
    assert (
        _answer_core_coverage_error(
            partial.model_copy(update={"answer": "东莞天气明天多云；广州天气暂未核实。"}),
            "东莞和广州天气",
            contract,
        )
        is None
    )


def test_forecast_planner_optional_metrics_do_not_erase_grounded_weather() -> None:
    source = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="WEB",
        source_id="https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
        locator="https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
        content=(
            "广州-天气预报\n来源发布日期：未提供\n网页正文摘录：\n"
            "广州-天气预报\n发布时间：09-17 08:00\n"
            "09/18\n周五\n晴\n无持续风向\n微风\n34℃\n25℃\n"
        ),
    )
    contract = AnswerContract.model_validate(
        {
            "answer_type": "FORECAST",
            "required_aspects": ["广州 2026-09-18 的天气预报（天气状况、气温、降水等）"],
            "completeness": "BEST_EFFORT",
            "freshness": "CURRENT",
            "minimum_answer_items": 1,
            "maximum_answer_items": 7,
            "require_explicit_dates": False,
            "minimum_evidence_items": 1,
            "minimum_independent_sources": 1,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "STRUCTURED_SUMMARY",
        }
    )

    candidate = _invalid_web_envelope_fallback(
        (source,),
        "明天广州的天气怎么样？",
        "广州 2026年9月18日 天气预报",
        contract,
        maximum_items=None,
    )

    assert candidate.answer_status == "ANSWERED"
    assert "广州9月18日天气预报晴" in candidate.answer
    assert _grounded_answer_error(candidate, (source,), "广州 2026年9月18日 天气预报") is None
    assert _answer_core_coverage_error(candidate, "明天广州降水是多少？", contract) is not None


def test_forecast_prompt_excerpt_keeps_dated_row_after_navigation() -> None:
    content = (
        "广州-天气预报\n网页正文摘录：\n"
        + "热门城市 天气公报 数值预报 " * 90
        + "\n09/18\n多云\n32℃\n26℃\n"
    )

    excerpt = _forecast_evidence_excerpt(content, "2026年9月18日广州天气", 900)

    assert len(excerpt) <= 900
    assert excerpt.startswith("广州-天气预报")
    assert "09/18\n多云\n32℃\n26℃" in excerpt


def test_document_timeout_fallback_orders_sections_by_source_position() -> None:
    shared_source = str(uuid4())
    training = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="DOCUMENT",
        source_id=shared_source,
        locator='{"selected_ordinals":[6]}',
        content="# 六、入职培训\n\n新员工应在入职后五个工作日内完成基础培训。",
    )
    materials = evidence(
        evidence_id=uuid4(),
        workspace_id=training.workspace_id,
        task_id=training.task_id,
        source_type="DOCUMENT",
        source_id=shared_source,
        locator='{"selected_ordinals":[4]}',
        content="# 四、入职材料\n\n新员工应在入职前提交身份证明和必要资料。",
    )

    result = _extractive_document_fallback((training, materials), "入职流程是什么？")

    assert result is not None
    assert result.answer.index("入职材料") < result.answer.index("入职培训")


def test_document_heading_without_procedure_is_not_promoted_to_answer() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    source = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator='{"mode":"DOCUMENT_NEIGHBORHOOD","selected_ordinals":[7]}',
        content="# 请假流程",
    )

    assert _extractive_document_fallback((source,), "请假流程是什么？") is None
    fallback = _invalid_web_envelope_fallback(
        (source,), "请假流程是什么？", "请假流程是什么？", None, maximum_items=None
    )
    assert fallback.answer_status == "NO_RELIABLE_RESULTS"
    assert "知识库片段" in fallback.answer


def test_document_timeout_fallback_keeps_relevant_policy_steps_not_cover_page() -> None:
    source = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator="page:2",
        content=(
            "员工请假管理制度\n文档编号 HR-02\n"
            "四、申请材料\n请假申请应写明假期类型、起止日期、工作交接人及联系方式。"
            "不同假期的证明材料另有规定。\n"
            "五、审批层级\n请假超过1天但不超过3天，由部门负责人审批。"
            "连续请假3天以上，在部门负责人审批后还需人力资源部复核。\n"
            "七、销假\n员工返岗后应在当日完成销假。具体申请记录应归档保存。"
        ),
    )

    fallback = _extractive_document_fallback((source,), "请假流程是什么？")

    assert fallback is not None
    assert fallback.answer_status == "ANSWERED"
    assert "申请材料" in fallback.answer
    assert "审批层级" in fallback.answer
    assert "销假" in fallback.answer
    assert "文档编号" not in fallback.answer
    assert "原文摘录" not in fallback.answer
    assert _grounded_answer_error(fallback, (source,), "请假流程是什么？") is None


def test_document_prompt_answers_supported_shared_policy_before_missing_detail() -> None:
    source = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator="section:leave",
        content="请假申请应先提交主管审批。不同假期类型的证明材料另有规定。",
    )

    prompt = _analysis_system_prompt((source,), "document-analysis")
    assert "answer the requested fact or procedure first" in prompt
    assert "state the shared steps" in prompt
    assert "do not invent a unique category or refuse the entire question" in prompt


def test_document_summary_timeout_returns_bounded_section_overview() -> None:
    item = evidence(
        evidence_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        source_type="DOCUMENT",
        source_id=str(uuid4()),
        locator="selected-document:summary",
        content=(
            "封面\n版本号 V2.0\n一、适用范围\n本制度覆盖员工各类请假申请和工作交接。"
            "具体范围由人力资源部核实。\n二、假期类别\n包含年假、事假、病假等假期，"
            "分别规定资格和证明。\n三、申请材料\n所有申请写明类型、起止日期、"
            "交接人及联系方式。\n四、审批层级\n主管和部门负责人按请假天数审批，"
            "长期假期还需人力资源部复核。\n五、销假\n员工返岗当日完成销假，"
            "延期需按总天数重新审批。\n六、记录保护\n医疗证明由获授权的人力资源"
            "人员访问，不得公开传播。\n七、附则与修订说明\n本制度修订了审批规则。"
        ),
    )
    result = _extractive_document_summary_fallback((item,))
    assert result is not None
    assert result.answer_status == "ANSWERED"
    assert 3 <= len(result.claims) <= 5
    assert "封面" not in result.answer
    assert "附则" not in result.answer
    assert _grounded_answer_error(result, (item,), "总结这份文档") is None


def test_current_role_blanket_refusal_is_invalid_with_opened_official_page() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.kantei.go.jp/jp/rekidainaikaku/index.html",
        content="日本首相（首相官邸现职与历代内阁）\n网页正文摘录：\n現職\n第105代\n高市 早苗",
    )
    refusal = _grounded_validation_fallback("现在的日本首相是谁？", (source,))
    assert _current_role_refusal_ignores_official_evidence(
        refusal, (source,), "2026 日本首相", "现在的日本首相是谁？"
    )
    assert not _current_role_refusal_ignores_official_evidence(
        refusal,
        (source.model_copy(update={"content": "日本首相搜索结果摘要：高市 早苗"}),),
        "2026 日本首相",
        "现在的日本首相是谁？",
    )


def test_current_multi_role_timeout_retains_only_opened_first_party_facts() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    pages = (
        (
            "https://www.kunaicho.go.jp/en/learn/about/history/history01.html",
            "日本天皇（官方）\n网页正文摘录：\nHis Majesty the Emperor Naruhito",
        ),
        (
            "https://www.kantei.go.jp/jp/rekidainaikaku/index.html",
            "日本首相（官方）\n网页正文摘录：\n現職\n第105代\n高市 早苗",
        ),
        (
            "https://www.pm.gov.au/about-prime-minister",
            "澳大利亚总理（官方）\n网页正文摘录：\nThe Hon Anthony Albanese MP",
        ),
        (
            "https://www.gg.gov.au/about-governor-general/civics-and-democracy",
            (
                "澳大利亚总督（官方）\n网页正文摘录：\n"
                "The Governor-General of Australia is His Majesty The King's "
                "representative and Australia's Head of State."
            ),
        ),
    )
    sources = tuple(
        evidence(
            evidence_id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            source_type="WEB",
            source_id="web.search",
            locator=locator,
            content=content,
        )
        for locator, content in pages
    )
    instruction = "现在的日本天皇是谁 首相呢 澳大利亚总统呢"
    answer = _invalid_web_envelope_fallback(
        sources, instruction, "2026 日本天皇", None, maximum_items=3
    )
    assert answer.answer_status == "ANSWERED"
    assert len(answer.claims) == 3
    assert "Naruhito" in answer.answer
    assert "高市早苗" in answer.answer
    assert "没有总统" in answer.answer
    assert "Anthony Albanese" in answer.answer
    assert _grounded_answer_error(answer, sources, "2026 日本天皇") is None

    highest = "现在的日本首相是谁？天皇呢？还有澳大利亚最高领导人是谁？"
    highest_answer = _invalid_web_envelope_fallback(
        sources, highest, highest, None, maximum_items=3
    )
    assert highest_answer.answer_status == "ANSWERED"
    assert len(highest_answer.claims) == 3
    assert "Anthony Albanese" in highest_answer.answer
    assert "没有总统" not in highest_answer.answer


def test_grounded_answer_uses_atomic_claims_with_verbatim_support() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://example.com/release",
        locator="https://example.com/release",
        content="Acme 于 2026-09-12 发布了 Atlas 2，公开价格为 99 元。",
    )
    raw = WebStructuredAnswer(
        answer="Acme 于 2026-09-12 发布 Atlas 2，公开价格为 99 元。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="Acme 于 2026-09-12 发布了 Atlas 2。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="Acme 于 2026-09-12 发布了 Atlas 2",
                    ),
                ),
            ),
            GroundedAnswerClaim(
                content="公开价格为 99 元。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="公开价格为 99 元",
                    ),
                ),
            ),
        ),
    )

    assert _grounded_answer_error(raw, (source,)) is None
    analysis = _normalize_web_analysis(raw)
    assert len(analysis.claims) == 2
    assert all(claim.evidence_ids == (source_id,) for claim in analysis.claims)


def test_grounded_answer_rejects_non_verbatim_support_and_unsupported_values() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="document:v1",
        locator="page:1",
        content="报告记载合同总额为 80 万元。",
    )
    bad_quote = WebStructuredAnswer(
        answer="合同总额为 80 万元。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="合同总额为 80 万元。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="合同金额大约为八十万元",
                    ),
                ),
            ),
        ),
    )
    bad_value = bad_quote.model_copy(
        update={
            "answer": "合同总额为 90 万元。",
            "claims": (
                GroundedAnswerClaim(
                    content="合同总额为 90 万元。",
                    supports=(
                        EvidenceSupport(
                            evidence_id=source_id,
                            quote="合同总额为 80 万元",
                        ),
                    ),
                ),
            ),
        }
    )

    assert "not verbatim" in (_grounded_answer_error(bad_quote, (source,)) or "")
    assert "date or number" in (_grounded_answer_error(bad_value, (source,)) or "")


def test_platform_retrieval_time_cannot_ground_a_source_fact() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="web.search",
        locator="https://example.com/live",
        content="检索时间：2026-09-14T08:00:00+00:00\n页面只说明当前状态正常。",
    )
    answer = WebStructuredAnswer(
        answer="该状态在2026年9月14日正常。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="该状态在2026年9月14日正常。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="页面只说明当前状态正常。",
                    ),
                ),
            ),
        ),
    )
    metadata_quote = answer.model_copy(
        update={
            "claims": (
                GroundedAnswerClaim(
                    content="检索发生在2026年9月14日。",
                    supports=(
                        EvidenceSupport(
                            evidence_id=source_id,
                            quote="检索时间：2026-09-14T08:00:00+00:00",
                        ),
                    ),
                ),
            ),
        }
    )
    assert "date or number" in (_grounded_answer_error(answer, (source,)) or "")
    assert "not verbatim" in (_grounded_answer_error(metadata_quote, (source,)) or "")


def test_web_generation_context_is_diverse_deduplicated_and_bounded() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    items = tuple(
        evidence(
            evidence_id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            source_type="WEB",
            source_id=f"https://source-{index}.example/news",
            locator=f"https://source-{index}.example/news",
            content=(f"source {index} " + "evidence " * 300),
        )
        for index in range(7)
    )

    payload = _analysis_evidence_payload(items, "web-research")

    assert len(payload) == 5
    assert len({str(item["locator"]).split("/")[2] for item in payload}) == 5
    assert sum(len(str(item["content"])) for item in payload) <= 4500
    assert all(len(str(item["content"])) <= 900 for item in payload)
    assert all(item["opened_page"] is False for item in payload)
    assert all(item["substantive_characters"] == 0 for item in payload)
    assert all(item["source_quality"] == "GENERAL_WEB" for item in payload)
    assert all(isinstance(item["retrieved_at"], str) for item in payload)


def test_document_summary_payload_preserves_order_and_repeated_sections() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    document_contract = AnswerContract.model_validate(
        {
            "answer_type": "DOCUMENT_SUMMARY",
            "required_aspects": ["main points", "material conclusions"],
            "completeness": "COMPLETE",
            "freshness": "SOURCE_BOUND",
            "minimum_answer_items": None,
            "maximum_answer_items": None,
            "require_explicit_dates": False,
            "minimum_evidence_items": 1,
            "minimum_independent_sources": 1,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "STRUCTURED_SUMMARY",
        }
    )

    def summary_locator(ordinals: list[int]) -> str:
        return json.dumps(
            {
                "mode": "DOCUMENT_SUMMARY_COVERAGE",
                "document_chunk_count": 2,
                "selected_chunk_count": 2,
                "selected_ordinals": ordinals,
                "source_locators": [],
                "coverage_complete": True,
            },
            sort_keys=True,
        )

    later = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="document-version-1",
        locator=summary_locator([1]),
        content="Repeated legal boilerplate.",
    )
    earlier = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="document-version-1",
        locator=summary_locator([0]),
        content="Repeated legal boilerplate.",
    )

    payload = _analysis_evidence_payload(
        (later, earlier),
        "document-analysis",
        "总结附件",
        document_contract,
    )
    coverage = _document_summary_coverage((later, earlier))

    assert len(payload) == 2
    assert [json.loads(str(item["locator"]))["selected_ordinals"] for item in payload] == [
        [0],
        [1],
    ]
    assert coverage == {
        "coverage_complete": True,
        "selected_chunk_count": 2,
        "document_chunk_count": 2,
    }


def test_explicit_requested_item_count_is_preserved_for_generation() -> None:
    assert _requested_answer_minimum("概括最重要的两三项，并给出来源") == 2
    assert _requested_answer_minimum("列出 3-5 条主要变化") == 3
    assert _requested_answer_minimum("今天气温是多少") is None


def test_substantive_opened_report_outranks_thin_authoritative_page() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    thin = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://tv.cctv.com/2026/09/14/thin.shtml",
        content=(
            "Acme latest update\n来源发布日期：2026-09-14\n\n"
            "网页正文摘录：\nAcme latest update\n内容简介\nAcme announced an update.\n"
            "播放列表\n正在播放\nAcme latest update"
        ),
    )
    rich = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.nytimes.com/2026/09/13/acme-update.html",
        content=(
            "Acme latest update explained\n来源发布日期：2026-09-13\n\n"
            "网页正文摘录：\nAcme latest update explained\n"
            + "This detailed report explains the event, its participants, evidence, and "
            "material consequences for readers. " * 8
        ),
    )

    payload = _analysis_evidence_payload(
        (thin, rich),
        "web-research",
        "Acme latest news 2026年9月",
    )

    assert payload[0]["locator"] == rich.locator
    assert _web_evidence_substantive_characters(thin) < 120
    assert _web_evidence_substantive_characters(rich) >= 400


def test_explicit_requested_item_count_preserves_the_upper_bound() -> None:
    assert _requested_answer_bounds("概括最重要的两三项，并给出来源") == (2, 3)
    assert _requested_answer_bounds("列出 3-5 条主要变化") == (3, 5)
    assert _requested_answer_bounds("只列出 2 项") == (2, 2)


def test_open_ended_news_roundup_gets_compact_default_but_focused_update_does_not() -> None:
    assert _default_current_event_bounds("特朗普最近有什么新闻吗？") == (2, 3)
    assert _default_current_event_bounds("他最近有哪些动态？") == (2, 3)
    assert _default_current_event_bounds("这个事件的最新进展是什么？") is None


def test_semantic_evidence_selection_restores_required_publisher_coverage() -> None:
    first_id, duplicate_id, independent_id = uuid4(), uuid4(), uuid4()
    candidates: list[dict[str, object]] = [
        {"evidence_id": str(first_id), "locator": "https://news.sina.com.cn/a"},
        {"evidence_id": str(duplicate_id), "locator": "https://finance.sina.com.cn/b"},
        {"evidence_id": str(independent_id), "locator": "https://www.bbc.com/news/c"},
    ]

    selected = _apply_web_evidence_selection(
        candidates,
        (first_id, duplicate_id),
        minimum_items=2,
        maximum_items=3,
        minimum_publishers=2,
    )

    assert {item["evidence_id"] for item in selected} >= {
        str(first_id),
        str(independent_id),
    }


def test_current_news_accepts_established_editorial_evidence_but_keeps_high_risk_bar() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://www.reuters.com/world/acme-update",
        locator="https://www.reuters.com/world/acme-update",
        content=(
            "Acme announced an update on September 14.\n\n"
            "网页正文摘录：\nAcme announced an update on September 14."
        ),
    )
    answer = WebStructuredAnswer(
        answer="Reuters reports that Acme announced an update on September 14.",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="Acme announced an update on September 14.",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="Acme announced an update on September 14.",
                    ),
                ),
            ),
        ),
    )

    assert _grounded_answer_error(answer, (source,), "Acme latest news") is None
    assert "two independently hosted" in (
        _grounded_answer_error(answer, (source,), "Acme latest war allegation") or ""
    )


def test_two_newsroom_subdomains_do_not_satisfy_independent_corroboration() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    statement = "Acme was accused of misconduct on September 14."
    first = evidence(
        evidence_id=first_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://news.sina.com.cn/world/acme-one",
        content=f"网页正文摘录：\n{statement}",
    )
    second = evidence(
        evidence_id=second_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://finance.sina.com.cn/world/acme-two",
        content=f"网页正文摘录：\n{statement}",
    )
    answer = WebStructuredAnswer(
        answer=statement,
        answer_status="ANSWERED",
        evidence_ids=(first_id, second_id),
        claims=(
            GroundedAnswerClaim(
                content=statement,
                supports=(
                    EvidenceSupport(evidence_id=first_id, quote=statement),
                    EvidenceSupport(evidence_id=second_id, quote=statement),
                ),
            ),
        ),
    )

    assert "two independently hosted" in (
        _grounded_answer_error(answer, (first, second), "Acme latest allegation") or ""
    )


def test_claim_level_fallback_keeps_supported_facts_and_drops_invalid_ones() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="https://www.reuters.com/world/acme-update",
        locator="https://www.reuters.com/world/acme-update",
        content="网页正文摘录：\nAcme announced an update.",
    )
    valid = GroundedAnswerClaim(
        content="Acme announced an update.",
        supports=(EvidenceSupport(evidence_id=source_id, quote="Acme announced an update."),),
    )
    invalid = GroundedAnswerClaim(
        content="The update costs 99 dollars.",
        supports=(EvidenceSupport(evidence_id=source_id, quote="Acme announced an update."),),
    )
    candidate = WebStructuredAnswer(
        answer="Acme announced an update that costs 99 dollars.",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(valid, invalid),
    )

    partial = _grounded_partial_answer(candidate, (source,), "Acme latest news")

    assert partial is not None
    assert partial.answer_status == "ANSWERED"
    assert partial.claims == (valid,)
    assert "99" not in partial.answer
    assert "lack sufficient verifiable evidence" in partial.answer

    sufficient_partial = _grounded_partial_answer(
        candidate,
        (source,),
        "Acme latest news",
        minimum_items=1,
    )
    assert sufficient_partial is not None
    assert "lack sufficient verifiable evidence" not in sufficient_partial.answer

    incomplete_roundup = _grounded_partial_answer(
        candidate,
        (source,),
        "Acme latest news",
        minimum_items=2,
        max_items=3,
    )
    assert incomplete_roundup is None


def test_extractive_web_fallback_preserves_dated_multi_publisher_news() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    first = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://news.sina.com.cn/world/2026-09-14/one",
        content=(
            "Acme announces Atlas\n"
            "来源发布日期：2026-09-14\n"
            "检索时间：2026-09-14T08:00:00+00:00\n\n"
            "网页正文摘录：\nAcme announces Atlas"
        ),
    )
    second = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.bbc.com/news/2026-09-13/two",
        content=(
            "Acme opens a new research center\n"
            "来源发布日期：2026-09-13\n"
            "检索时间：2026-09-14T08:00:00+00:00\n\n"
            "网页正文摘录：\nAcme opens a new research center"
        ),
    )

    fallback = _extractive_web_fallback(
        (first, second),
        "Acme 最近有什么新闻 2026年9月",
        minimum_items=2,
        max_items=3,
    )

    assert fallback is not None
    assert fallback.answer_status == "ANSWERED"
    assert len(fallback.claims) == 2
    assert "2026-09-14" in fallback.answer
    assert "2026-09-13" in fallback.answer
    assert _grounded_answer_error(fallback, (first, second), "Acme 最近新闻 2026年9月") is None


def test_dated_former_officeholder_headline_never_becomes_current_answer() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    stale = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://news.example/2026/06/former-prime-minister",
        content=(
            "Former Prime Minister Yoshihide Suga gives an interview\n"
            "来源发布日期：2026-06-10\n"
            "网页正文摘录：\nFormer Prime Minister Yoshihide Suga gave an interview."
        ),
    )
    assert _extractive_web_fallback((stale,), "current prime minister of Japan") is None
    fallback = _invalid_web_envelope_fallback(
        (stale,),
        "Who are the current prime minister and emperor of Japan?",
        "Japan prime minister",
        None,
        maximum_items=2,
    )
    assert fallback.answer_status == "NO_RELIABLE_RESULTS"


def test_official_dated_forecast_survives_model_timeout() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    source = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.fetch",
        locator="https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
        content=(
            "广州-天气预报\n来源发布日期：未提供\n"
            "检索时间：2026-09-16T12:44:45+00:00\n\n网页正文摘录：\n"
            "无持续风向\n微风\n09/17\n周四\n晴\n无持续风向\n微风\n"
            "33℃\n24℃\n91.2%\n10.1%\n"
        ),
    )
    query = "广州 2026年9月17日 天气预报"
    direct = _extractive_official_forecast_fallback((source,), query)
    assert direct is not None
    assert _grounded_answer_error(direct, (source,), query) is None
    result = _invalid_web_envelope_fallback(
        (source,), "明天的广州天气", query, None, maximum_items=None
    )
    assert result.answer_status == "ANSWERED"
    assert "广州9月17日天气预报晴" in result.answer
    assert "最高33℃，最低24℃" in result.answer
    assert _grounded_answer_error(result, (source,), query) is None

    unrelated = _invalid_web_envelope_fallback(
        (source,),
        "明天东莞的天气",
        "东莞 2026年9月17日 天气预报",
        None,
        maximum_items=None,
    )
    assert unrelated.answer_status == "NO_RELIABLE_RESULTS"


def test_two_city_official_forecasts_survive_model_timeout() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    sources = tuple(
        evidence(
            evidence_id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            source_type="WEB",
            source_id="web.search",
            locator=url,
            content=(
                f"{city}-天气预报\n来源发布日期：未提供\n"
                "网页正文摘录：\n09/18\n周五\n"
                f"{condition}\n无持续风向\n微风\n{high}℃\n{low}℃\n"
            ),
        )
        for city, url, condition, high, low in (
            (
                "东莞",
                "https://www.nmc.cn/publish/forecast/AGD/dongzuo.html",
                "多云",
                33,
                25,
            ),
            (
                "广州",
                "https://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
                "晴",
                34,
                24,
            ),
        )
    )
    query = "2026年9月18日东莞和广州的天气情况"
    duplicate_guangzhou = evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="http://www.nmc.cn/publish/forecast/AGD/guangzhou.html",
        content=(
            "广州-天气预报\n来源发布日期：未提供\n网页正文摘录：\n09/18\n周五\n晴\n34℃\n24℃\n"
        ),
    )
    contract = AnswerContract.model_validate(
        {
            "answer_type": "FORECAST",
            "required_aspects": ["2026年9月18日东莞天气", "2026年9月18日广州天气"],
            "completeness": "BEST_EFFORT",
            "freshness": "CURRENT",
            "minimum_answer_items": 1,
            "maximum_answer_items": 7,
            "require_explicit_dates": True,
            "minimum_evidence_items": 1,
            "minimum_independent_sources": 1,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "STRUCTURED_SUMMARY",
        }
    )
    all_sources = (*sources, duplicate_guangzhou)
    result = _invalid_web_envelope_fallback(all_sources, query, query, contract, maximum_items=7)
    assert result.answer_status == "ANSWERED"
    assert "东莞" in result.answer and "广州" in result.answer
    assert "33℃" in result.answer and "34℃" in result.answer
    assert result.answer.count("广州9月18日") == 1
    assert _grounded_answer_error(result, all_sources, query) is None
    assert _answer_core_coverage_error(result, query, contract) is None


def test_web_claim_requires_primary_source_or_opened_independent_corroboration() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    first = evidence(
        evidence_id=first_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://one.example/acme",
        content="Acme released Atlas.\n\n网页正文摘录：\nAcme released Atlas.",
    )
    second = evidence(
        evidence_id=second_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://two.example/acme",
        content="Acme released Atlas.",
    )
    one_source = WebStructuredAnswer(
        answer="Acme released Atlas.",
        answer_status="ANSWERED",
        evidence_ids=(first_id,),
        claims=(
            GroundedAnswerClaim(
                content="Acme released Atlas.",
                supports=(
                    EvidenceSupport(
                        evidence_id=first_id,
                        quote="Acme released Atlas.",
                    ),
                ),
            ),
        ),
    )
    corroborated = one_source.model_copy(
        update={
            "evidence_ids": (first_id, second_id),
            "claims": (
                GroundedAnswerClaim(
                    content="Acme released Atlas.",
                    supports=(
                        EvidenceSupport(
                            evidence_id=first_id,
                            quote="Acme released Atlas.",
                        ),
                        EvidenceSupport(
                            evidence_id=second_id,
                            quote="Acme released Atlas.",
                        ),
                    ),
                ),
            ),
        }
    )
    primary = first.model_copy(update={"locator": "https://acme.com/news/atlas"})

    grounding_error = (
        _grounded_answer_error(
            one_source,
            (first, second),
            "Acme latest news",
        )
        or ""
    )
    assert "two independently hosted" in grounding_error
    assert str(first_id) in grounding_error
    assert "one.example" in grounding_error
    assert str(second_id) in grounding_error
    assert (
        _grounded_answer_error(
            corroborated,
            (first, second),
            "Acme latest news",
        )
        is None
    )
    assert _grounded_answer_error(one_source, (primary,), "Acme latest product") is None
    assert (
        _grounded_answer_error(
            one_source,
            (first,),
            "Acme product documentation",
        )
        is None
    )


def test_authoritative_specialist_supports_low_risk_official_fact() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.weather.com.cn/weather/101280111.shtml",
        content=(
            "黄埔天气预报\n\n网页正文摘录：\n"
            "全国 > 广东 > 广州 > 黄埔\n14日（今天）雷阵雨，33/25℃，风力小于3级。"
        ),
    )
    answer = WebStructuredAnswer(
        answer="今天广州黄埔有雷阵雨，最高33℃，最低25℃。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="今天广州黄埔有雷阵雨，最高33℃，最低25℃。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="14日（今天）雷阵雨，33/25℃，风力小于3级。",
                    ),
                ),
            ),
        ),
    )

    assert (
        _grounded_answer_error(
            answer,
            (source,),
            "2026年9月14日 广州市 黄埔区 今日天气预报 官方",
        )
        is None
    )


def test_corroboration_is_required_per_claim_not_only_across_the_whole_answer() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    first = evidence(
        evidence_id=first_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://one.example/acme",
        content="Acme released Atlas.\n\n网页正文摘录：\nAcme released Atlas.",
    )
    second = evidence(
        evidence_id=second_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://two.example/acme",
        content="Acme appointed Lee.\n\n网页正文摘录：\nAcme appointed Lee.",
    )
    answer = WebStructuredAnswer(
        answer="Acme released Atlas and appointed Lee.",
        answer_status="ANSWERED",
        evidence_ids=(first_id, second_id),
        claims=(
            GroundedAnswerClaim(
                content="Acme released Atlas.",
                supports=(EvidenceSupport(evidence_id=first_id, quote="Acme released Atlas."),),
            ),
            GroundedAnswerClaim(
                content="Acme appointed Lee.",
                supports=(EvidenceSupport(evidence_id=second_id, quote="Acme appointed Lee."),),
            ),
        ),
    )

    assert "two independently hosted" in (
        _grounded_answer_error(answer, (first, second), "Acme latest news") or ""
    )


def test_corroborating_sources_must_each_support_the_claims_date_and_number() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    general_id, editorial_id = uuid4(), uuid4()
    general = evidence(
        evidence_id=general_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://updates.example/acme",
        content=("网页正文摘录：\nAcme announced a 5000 dollar credit on 2026-09-14."),
    )
    editorial = evidence(
        evidence_id=editorial_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.reuters.com/world/acme-credit",
        content="网页正文摘录：\nAcme announced a 5000 dollar credit three days ago.",
    )
    answer = WebStructuredAnswer(
        answer="Acme announced a 5000 dollar credit on 2026-09-14.",
        answer_status="ANSWERED",
        evidence_ids=(general_id, editorial_id),
        claims=(
            GroundedAnswerClaim(
                content="Acme announced a 5000 dollar credit on 2026-09-14.",
                supports=(
                    EvidenceSupport(
                        evidence_id=general_id,
                        quote="Acme announced a 5000 dollar credit on 2026-09-14.",
                    ),
                    EvidenceSupport(
                        evidence_id=editorial_id,
                        quote="Acme announced a 5000 dollar credit three days ago.",
                    ),
                ),
            ),
        ),
    )

    error = _grounded_answer_error(answer, (general, editorial), "Acme latest news") or ""

    assert "established editorial source" in error


def test_current_period_claim_rejects_evidence_from_a_conflicting_article_month() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://www.reuters.com/world/2026-07-04/acme-update",
        content=(
            "Acme update\n"
            "来源发布日期：2026-07-04\n"
            "检索时间：2026-09-14T00:00:00+00:00\n\n"
            "网页正文摘录：\nAcme announced Atlas on 2026-07-04."
        ),
    )
    answer = WebStructuredAnswer(
        answer="Acme announced Atlas on 2026-07-04.",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="Acme announced Atlas on 2026-07-04.",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="Acme announced Atlas on 2026-07-04.",
                    ),
                ),
            ),
        ),
    )

    error = _grounded_answer_error(answer, (source,), "Acme latest update 2026-09") or ""

    assert "conflicts with the requested period" in error


def test_citation_normalizer_recovers_exact_quote_and_support_id_set() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://acme.com/news/atlas",
        content=("Acme 公告\n\n网页正文摘录：\nAcme 于 2026-09-13 发布 Atlas。"),
    )
    answer = WebStructuredAnswer(
        answer="Acme 于 2026-09-13 发布 Atlas。",
        answer_status="ANSWERED",
        evidence_ids=(uuid4(),),
        claims=(
            GroundedAnswerClaim(
                content="Acme 于 2026-09-13 发布 Atlas。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="Acme 于 2026 年 9 月 13 日发布了 Atlas。",
                    ),
                ),
            ),
        ),
    )

    normalized = _normalize_web_citations(answer, (source,))

    assert normalized.evidence_ids == (source_id,)
    assert normalized.claims[0].supports[0].quote == "Acme 于 2026-09-13 发布 Atlas。"
    assert _grounded_answer_error(normalized, (source,), "Acme latest product") is None


def test_lookalike_content_domain_is_not_accepted_as_a_primary_source() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    lookalike = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="WEB",
        source_id="web.search",
        locator="https://deepseek.chat/",
        content="DeepSeek announced Atlas.",
    )
    answer = WebStructuredAnswer(
        answer="DeepSeek announced Atlas.",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="DeepSeek announced Atlas.",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="DeepSeek announced Atlas.",
                    ),
                ),
            ),
        ),
    )

    assert "two independently hosted" in (
        _grounded_answer_error(answer, (lookalike,), "DeepSeek official release") or ""
    )


def test_grounded_numeric_validation_handles_decimals_and_equivalent_units() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="annual-report",
        locator="document:annual-report",
        content=(
            "2024年全省平均年降水量2190.8mm，比2023年增加15.8%，其中汛期（4-9月）降水量1948.3mm。"
        ),
    )
    answer = WebStructuredAnswer(
        answer="2024年全省平均年降水量为2190.8毫米，比2023年增加15.8%。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content=("2024年全省平均年降水量为2190.8毫米，比2023年增加15.8%。"),
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="2024年全省平均年降水量2190.8mm，比2023年增加15.8%",
                    ),
                ),
            ),
        ),
    )

    assert _grounded_answer_error(answer, (source,)) is None


def test_grounded_validation_accepts_equivalent_iso_and_localized_dates() -> None:
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    source = evidence(
        evidence_id=source_id,
        workspace_id=workspace_id,
        task_id=task_id,
        source_type="DOCUMENT",
        source_id="official-record",
        locator="document:official-record",
        content="官方记录的截止日期为2026 年 9 月 13 日。",
    )
    answer = WebStructuredAnswer(
        answer="官方记录的截止日期为2026年9月13日。",
        answer_status="ANSWERED",
        evidence_ids=(source_id,),
        claims=(
            GroundedAnswerClaim(
                content="官方记录的截止日期为2026年9月13日。",
                supports=(
                    EvidenceSupport(
                        evidence_id=source_id,
                        quote="官方记录的截止日期为2026 年 9 月 13 日",
                    ),
                ),
            ),
        ),
    )

    assert _grounded_answer_error(answer, (source,)) is None


@pytest.mark.parametrize("unsupported_extra", [False, True])
def test_valid_claim_repair_preserves_readable_answer_without_preserving_unsupported_text(unsupported_extra):
    workspace_id, task_id, source_id = uuid4(), uuid4(), uuid4()
    facts = ("Annual leave requires approval.", "Applications need a handover.")
    source = evidence(evidence_id=source_id, workspace_id=workspace_id, task_id=task_id,
                      source_type="DOCUMENT", source_id=str(uuid4()), locator='{"page": 1}',
                      content="\n".join(facts))
    claims = tuple(GroundedAnswerClaim(content=fact, supports=(EvidenceSupport(evidence_id=source_id, quote=fact),)) for fact in facts)
    formatted = "- Annual leave requires approval.\n\n- Applications need a handover."
    candidate = WebStructuredAnswer(answer=formatted + (" A fee of 99 dollars applies." if unsupported_extra else ""), answer_status="ANSWERED", evidence_ids=(source_id,), claims=claims)
    result = _grounded_partial_answer(candidate, (source,), "Summarize the leave policy")
    assert result is not None
    assert result.claims == claims
    if unsupported_extra:
        assert "99" not in result.answer
    else:
        assert result.answer == formatted


@pytest.mark.parametrize("scope", ["Quarter 4 is not covered by this document.", "2027 data are unavailable in these sources."])
def test_answer_scope_numbers_go_to_semantic_review_without_loosening_claim_checks(scope):
    item = evidence(evidence_id=uuid4(), workspace_id=uuid4(), task_id=uuid4(),
                    source_type="DOCUMENT", source_id="inventory", locator="inventory.txt:1",
                    content="Inventory: 31 items.")
    claim = GroundedAnswerClaim(content="Inventory is 31 items.", supports=(
        EvidenceSupport(evidence_id=item.evidence_id, quote="Inventory: 31 items."),))
    candidate = WebStructuredAnswer(answer="Inventory is 31 items. " + scope,
        answer_status="ANSWERED", evidence_ids=(item.evidence_id,), claims=(claim,))
    assert _grounded_answer_error(candidate, (item,)) is not None
    assert _grounded_answer_error(candidate, (item,), check_answer_numbers=False) is None
    wrong_value = candidate.model_copy(update={"claims": (claim.model_copy(update={"content": "Inventory is 99 items."}),)})
    assert _grounded_answer_error(wrong_value, (item,), check_answer_numbers=False) is not None
    forged = claim.model_copy(update={"supports": (EvidenceSupport(evidence_id=uuid4(), quote="Inventory: 31 items."),)})
    assert _grounded_answer_error(candidate.model_copy(update={"claims": (forged,)}), (item,), check_answer_numbers=False) is not None
