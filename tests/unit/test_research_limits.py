from enterprise_agent.capabilities.builtins import (
    _evidence_context_limit,
    _web_discovery_limit,
)
from enterprise_agent.contracts.domain import BoundedResearchPlan


def _single_fact_plan() -> BoundedResearchPlan:
    return BoundedResearchPlan.model_validate(
        {
            "web_queries": ["日本现任首相 2026"],
            "retrieval_queries": [],
            "source_plan": {
                "required_sources": ["PUBLIC_WEB"],
                "preferred_sources": ["OFFICIAL_OR_FIRST_PARTY"],
                "fallback_sources": ["GENERAL_WEB"],
                "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
            },
            "result_limit": 1,
            "answer_contract": {
                "answer_type": "FACT",
                "required_aspects": ["current office holder"],
                "completeness": "COMPLETE",
                "freshness": "CURRENT",
                "minimum_answer_items": 1,
                "maximum_answer_items": 1,
                "require_explicit_dates": True,
                "minimum_evidence_items": 1,
                "minimum_independent_sources": 1,
                "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
                "organization": "CONCISE",
            },
        }
    )


def test_one_fact_answer_does_not_collapse_web_discovery_to_one_result() -> None:
    plan = _single_fact_plan()

    assert plan.result_limit == 1
    assert _web_discovery_limit(1, plan) == 5
    assert _evidence_context_limit(plan, "web-research") == 3


def test_unplanned_capability_calls_preserve_the_explicit_limit() -> None:
    assert _web_discovery_limit(2, None) == 2
    assert _evidence_context_limit(None, None) == 4
