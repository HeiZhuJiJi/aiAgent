from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import CapabilityRequest
from enterprise_agent.runtime.retrieval import RetrievalCandidate
from enterprise_agent.storage.knowledge import _summary_sample_ordinals


def test_section_context_hits_match_public_knowledge_output_schema() -> None:
    descriptor = next(
        item
        for item in builtins.builtin_descriptors()
        if item.id == "knowledge.hybrid_search"
    )
    hit = builtins._KnowledgeRankedHit(
        candidate=RetrievalCandidate(
            chunk_id=str(uuid4()),
            content="leave procedure from the selected policy section",
            locator="line:20",
            source_id=str(uuid4()),
            source_name="HR-02_leave_policy_v2.pdf",
        ),
        rank_score=1.0,
        relevance=1.0,
        ranking_mode="section_context",
    )
    Draft202012Validator(descriptor.output_schema).validate(
        {"query": "leave procedure", "hits": [builtins._knowledge_hit_payload(hit)]}
    )


def _hit(identifier: str, *, relevance: float) -> builtins._KnowledgeRankedHit:
    return builtins._KnowledgeRankedHit(
        candidate=RetrievalCandidate(
            chunk_id=identifier,
            content=f"selected document chunk {identifier}",
            locator=f"page:{identifier}",
            source_id="selected-document",
        ),
        rank_score=relevance,
        relevance=relevance,
        ranking_mode="reranker",
    )


def _rrf_hit(
    identifier: str,
    *,
    rank_score: float,
    source_id: str = "selected-document",
) -> builtins._KnowledgeRankedHit:
    return builtins._KnowledgeRankedHit(
        candidate=RetrievalCandidate(
            chunk_id=identifier,
            content=f"selected document chunk {identifier}",
            locator=f"page:{identifier}",
            source_id=source_id,
        ),
        rank_score=rank_score,
        relevance=1.0,
        ranking_mode="rrf_fallback",
    )


def test_procedural_retrieval_prefers_current_policy_to_template_and_old_version() -> None:
    def named_hit(name: str, relevance: float) -> builtins._KnowledgeRankedHit:
        return builtins._KnowledgeRankedHit(
            candidate=RetrievalCandidate(
                chunk_id=str(uuid4()),
                content="policy excerpt",
                locator="page:1",
                source_id=str(uuid4()),
                source_name=name,
            ),
            rank_score=relevance,
            relevance=relevance,
            ranking_mode="reranker",
        )

    template = named_hit("TPL-08_employee_leave_application.md", 0.96)
    old = named_hit("HR-02_leave_policy_v1.md", 0.90)
    current = named_hit("HR-02_leave_policy_v2.pdf", 0.70)
    unrelated = named_hit("HR-03_attendance_policy_v1.md", 0.95)

    assert builtins._knowledge_procedural_anchor(
        "我想请假三天", (template, old, current, unrelated)
    ) == current
    assert builtins._knowledge_procedural_anchor("请假流程", (template,)) is None
    assert builtins._knowledge_procedural_anchor("普通聊天", (current,)) is None


def test_onboarding_flow_expands_to_governing_policy_not_only_heading() -> None:
    expansion = builtins._knowledge_policy_query_expansion("入职流程")
    assert expansion is not None
    assert "入职" in expansion
    assert "办理流程" in expansion


def _planner(
    calls: list[str],
    queries: tuple[str, ...],
) -> Callable[[tuple[builtins._KnowledgeRankedHit, ...]], Awaitable[tuple[str, ...]]]:
    async def plan(observed: tuple[builtins._KnowledgeRankedHit, ...]) -> tuple[str, ...]:
        calls.append("plan")
        return queries

    return plan


async def test_sufficient_initial_retrieval_never_calls_query_planner() -> None:
    retrieval_calls: list[tuple[str, ...]] = []
    planner_calls: list[str] = []
    sufficient = (_hit("initial", relevance=0.82),)

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return sufficient

    result = await builtins._bounded_knowledge_semantic_recovery(
        ("incident reporting deadline",),
        retrieve=retrieve,
        plan=_planner(planner_calls, ("page seven reporting window",)),
        limit=5,
    )

    assert result == sufficient
    assert retrieval_calls == [("incident reporting deadline",)]
    assert planner_calls == []


async def test_empty_initial_retrieval_executes_one_novel_recovery_pass() -> None:
    retrieval_calls: list[tuple[str, ...]] = []
    planner_calls: list[str] = []
    recovered = (_hit("recovered", relevance=0.91),)

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return () if len(retrieval_calls) == 1 else recovered

    result = await builtins._bounded_knowledge_semantic_recovery(
        ("incident reporting deadline",),
        retrieve=retrieve,
        plan=_planner(
            planner_calls,
            (
                " Incident reporting deadline! ",
                "deadline incident reporting",
                "page seven two-hour reporting window",
            ),
        ),
        limit=5,
    )

    assert result == recovered
    assert planner_calls == ["plan"]
    assert retrieval_calls == [
        ("incident reporting deadline",),
        ("page seven two-hour reporting window",),
    ]


async def test_low_relevance_recovery_is_bounded_when_second_pass_is_empty() -> None:
    retrieval_calls: list[tuple[str, ...]] = []
    planner_calls: list[str] = []
    low_evidence = (_hit("weak", relevance=0.31),)

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return low_evidence if len(retrieval_calls) == 1 else ()

    result = await builtins._bounded_knowledge_semantic_recovery(
        ("incident reporting deadline",),
        retrieve=retrieve,
        plan=_planner(planner_calls, ("two-hour notification requirement",)),
        limit=5,
    )

    assert result == low_evidence
    assert planner_calls == ["plan"]
    assert retrieval_calls == [
        ("incident reporting deadline",),
        ("two-hour notification requirement",),
    ]


def test_rrf_single_signal_does_not_suppress_semantic_recovery() -> None:
    single_signal = _rrf_hit("relative-first", rank_score=1 / 61)
    dual_signal = _rrf_hit("corroborated", rank_score=(1 / 61) + (1 / 63))

    assert not builtins._knowledge_retrieval_is_sufficient((single_signal,))
    assert builtins._knowledge_retrieval_is_sufficient((dual_signal,))


def test_multi_query_results_are_interleaved_instead_of_dropping_expansion() -> None:
    original = tuple(
        _rrf_hit(f"template-{index}", rank_score=1 / (61 + index)) for index in range(4)
    )
    expansion = tuple(
        _rrf_hit(
            f"policy-{index}",
            rank_score=1 / (61 + index),
            source_id="leave-policy",
        )
        for index in range(4)
    )

    selected = builtins._interleave_knowledge_query_hits(
        (original, expansion),
        limit=4,
    )

    assert [hit.candidate.chunk_id for hit in selected] == [
        "template-0",
        "policy-0",
        "template-1",
        "policy-1",
    ]


async def test_repeated_planner_queries_do_not_trigger_an_identical_retrieval() -> None:
    retrieval_calls: list[tuple[str, ...]] = []
    planner_calls: list[str] = []

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return ()

    result = await builtins._bounded_knowledge_semantic_recovery(
        ("Incident reporting deadline",),
        retrieve=retrieve,
        plan=_planner(
            planner_calls,
            (" incident reporting deadline ", "INCIDENT REPORTING DEADLINE!"),
        ),
        limit=5,
    )

    assert result == ()
    assert planner_calls == ["plan"]
    assert retrieval_calls == [("Incident reporting deadline",)]


async def test_preplanned_queries_still_replan_after_observed_empty_results() -> None:
    initial_queries = builtins._knowledge_initial_queries(
        "incident reporting deadline",
        {
            "research_plan": {
                "retrieval_queries": [
                    "page seven two-hour notification requirement",
                    "incident reporting deadline",
                ]
            }
        },
    )
    retrieval_calls: list[tuple[str, ...]] = []
    planner_calls: list[str] = []

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return ()

    result = await builtins._bounded_knowledge_semantic_recovery(
        initial_queries,
        retrieve=retrieve,
        plan=_planner(planner_calls, ("another query",)),
        limit=5,
    )

    assert result == ()
    assert initial_queries == (
        "incident reporting deadline",
        "page seven two-hour notification requirement",
    )
    assert retrieval_calls == [initial_queries, ("another query",)]
    assert planner_calls == ["plan"]


async def test_leave_question_observes_template_and_recovers_policy_source() -> None:
    template = builtins._KnowledgeRankedHit(
        candidate=RetrievalCandidate(
            chunk_id="leave-template",
            content="员工请假申请表：请假天数 {{leave_days}}",
            locator="line:47",
            source_id="template",
            source_name="员工请假申请模板",
        ),
        rank_score=0.91,
        relevance=0.91,
        ranking_mode="reranker",
    )
    policy = builtins._KnowledgeRankedHit(
        candidate=RetrievalCandidate(
            chunk_id="leave-policy",
            content="年假须提前一个工作日申请，主管审批。",
            locator="page:2",
            source_id="policy",
            source_name="HR-02 员工请假管理制度",
        ),
        rank_score=0.86,
        relevance=0.86,
        ranking_mode="reranker",
    )
    retrieval_calls: list[tuple[str, ...]] = []
    observations: list[tuple[builtins._KnowledgeRankedHit, ...]] = []

    async def retrieve(queries: tuple[str, ...]) -> tuple[builtins._KnowledgeRankedHit, ...]:
        retrieval_calls.append(queries)
        return (template,) if len(retrieval_calls) == 1 else (policy,)

    async def plan(observed: tuple[builtins._KnowledgeRankedHit, ...]) -> tuple[str, ...]:
        observations.append(observed)
        return ("HR-02 员工请假管理制度 年假 主管审批",)

    result = await builtins._bounded_knowledge_semantic_recovery(
        ("我想请年假三天", "员工请假申请条件"),
        retrieve=retrieve,
        plan=plan,
        limit=5,
    )

    assert observations == [(template,)]
    assert retrieval_calls == [
        ("我想请年假三天", "员工请假申请条件"),
        ("HR-02 员工请假管理制度 年假 主管审批",),
    ]
    assert {hit.candidate.source_name for hit in result} == {
        "员工请假申请模板",
        "HR-02 员工请假管理制度",
    }


def test_repeated_deterministic_query_does_not_consume_semantic_recovery() -> None:
    initial_queries = builtins._knowledge_initial_queries(
        "incident reporting deadline",
        {
            "research_plan": {
                "retrieval_queries": [
                    " Incident reporting deadline! ",
                    "deadline incident reporting",
                ]
            }
        },
    )

    assert initial_queries == ("incident reporting deadline",)


def test_policy_intent_adds_a_denosed_retrieval_query_without_consuming_recovery() -> None:
    initial_queries = builtins._knowledge_initial_queries(
        "我想要请假看假面骑士",
        {"research_plan": {"retrieval_queries": ["我想要请假看假面骑士"]}},
    )

    assert initial_queries[0] == "我想要请假看假面骑士"
    assert any("员工请假管理制度" in query for query in initial_queries[1:])


def test_document_summary_sampling_is_bounded_and_spans_the_document() -> None:
    assert _summary_sample_ordinals(3, 20) == (0, 1, 2)

    sampled = _summary_sample_ordinals(100, 5)

    assert len(sampled) == 5
    assert sampled[0] == 0
    assert sampled[-1] == 99
    assert tuple(sorted(set(sampled))) == sampled


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSessions:
    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext()


async def test_document_summary_uses_ordered_coverage_without_embedding_or_reranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_version_id = uuid4()
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="knowledge.hybrid_search",
        capability_version="1.0.0",
        arguments={
            "query": "总结附件",
            "limit": 5,
            "document_version_id": str(document_version_id),
        },
    )
    research_plan = {
        "web_queries": [],
        "retrieval_queries": ["附件主要内容"],
        "source_plan": {
            "required_sources": ["SELECTED_DOCUMENT"],
            "preferred_sources": ["SELECTED_DOCUMENT"],
            "fallback_sources": [],
            "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
        },
        "result_limit": 5,
        "answer_contract": {
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
        },
    }
    coverage_candidate = RetrievalCandidate(
        chunk_id="summary-packet",
        content="document beginning\n\ndocument ending",
        locator=(
            '{"coverage_complete":true,"document_chunk_count":2,'
            '"mode":"DOCUMENT_SUMMARY_COVERAGE","selected_chunk_count":2,'
            '"selected_ordinals":[0,1],"source_locators":[]}'
        ),
        source_id=str(document_version_id),
    )
    calls: list[tuple[object, ...]] = []

    async def context(*_args: object) -> dict[str, object]:
        return {"research_plan": research_plan}

    async def summary_candidates(
        *args: object, **_kwargs: object
    ) -> tuple[RetrievalCandidate, ...]:
        calls.append(args)
        return (coverage_candidate,)

    monkeypatch.setattr(builtins, "_task_user_context", context)
    monkeypatch.setattr(builtins, "document_summary_candidates", summary_candidates)
    monkeypatch.setattr(
        builtins,
        "load_embedding_settings",
        lambda: (_ for _ in ()).throw(AssertionError("embedding must not run")),
    )

    result = await builtins._knowledge_handler(_FakeSessions(), request)  # type: ignore[arg-type]

    assert result.outcome == "SUCCEEDED"
    assert calls
    assert isinstance(result.output, dict)
    assert result.output["hits"][0]["ranking_mode"] == "summary_coverage"
