import pytest

from enterprise_agent.capabilities import builtins


def assessment(**changes):
    values = {"decision": "PASS", "acceptable": True, "answered_core_request": True,
              "language_matches": True, "appropriate_depth": True, "revision_severity": "STYLE_ONLY",
              "critique": "Optional: reorder the two supported paragraphs."}
    values.update(changes)
    return builtins.AnswerQualityAssessment(**values)


def test_optional_polish_passes_without_answer_revision():
    value = assessment()
    assert builtins._review_consistency_error(value) is None
    assert builtins._answer_quality_decision(value) == 'PASS'
    builtins._require_quality_replan(value)


@pytest.mark.parametrize('changes', [
    {'acceptable': False, 'decision': 'REVISE'},
    {'answered_core_request': False},
    {'unsupported_claim_indexes': (1,)},
    {'missing_aspects': ('requested record',)},
    {'blocking_issue': 'Wrong division value'},
    {'language_matches': False},
    {'appropriate_depth': False},
])
def test_style_label_cannot_override_material_failures(changes):
    assert builtins._review_consistency_error(assessment(**changes)) is not None


def test_material_scope_confusion_still_requires_revision():
    value = assessment(revision_severity='MATERIAL', decision='REVISE', acceptable=False,
                       blocking_issue='Answer conflates parent total with division amount; distinguish them.')
    assert builtins._review_consistency_error(value) is None
    assert builtins._answer_quality_decision(value) == 'REVISE'


def test_legacy_review_without_severity_is_not_automatically_accepted():
    value = assessment(revision_severity=None, decision='REVISE', acceptable=False)
    assert builtins._review_consistency_error(value) is None
    assert builtins._answer_quality_decision(value) == 'REVISE'


@pytest.mark.parametrize("alignment,period", [("BROADER", "MATCH"), ("MATCH", "PARTIAL"), ("DIFFERENT", "MATCH")])
def test_raw_acceptance_conflict_is_corrected_before_recovery_normalization(alignment, period):
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    value = assessment(coverage=(AnswerCoverageItem(requirement="Requested records", status="ANSWERED",
        entity_alignment=alignment, period_alignment=period),))
    assert "conflicts" in builtins._coverage_review_error(value, [])
    # An uncorrected verdict still cannot bypass the existing grounding gate.
    normalized = builtins._apply_coverage_findings(value, [])
    assert normalized.decision == "REPLAN"
    assert not normalized.acceptable


def test_omission_does_not_authorize_external_search():
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    value = assessment(decision="REPLAN", acceptable=False, revision_severity="MATERIAL",
        blocking_issue="Second section omitted", missing_aspects=("Second section",),
        coverage=(AnswerCoverageItem(requirement="Second section", status="OMITTED_FROM_ANSWER"),))
    assert "Use REVISE" in builtins._coverage_review_error(value, [])
    corrected = value.model_copy(update={"decision": "REVISE", "missing_aspects": ()})
    assert builtins._coverage_review_error(corrected, []) is None
    builtins._require_quality_replan(builtins._apply_coverage_findings(corrected, []))


def test_consistent_missing_evidence_still_replans():
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    value = assessment(decision="REPLAN", acceptable=False, revision_severity="MATERIAL",
        blocking_issue="Missing remaining records", missing_aspects=("Remaining records",),
        coverage=(AnswerCoverageItem(requirement="Remaining records", status="MISSING_EVIDENCE"),))
    assert builtins._coverage_review_error(value, []) is None
    assert builtins._apply_coverage_findings(value, []).decision == "REPLAN"


@pytest.mark.parametrize("supported,unsupported", [((0, 1), (2,)), ((1, 4), ())])
def test_invalid_claim_indexes_never_become_supported_recovery_facts(supported, unsupported):
    from uuid import uuid4
    source_id = uuid4()
    claims = tuple(builtins.GroundedAnswerClaim(content=text, supports=(
        builtins.EvidenceSupport(evidence_id=source_id, quote=text),)) for text in ("Alpha", "Beta", "Gamma"))
    candidate = builtins.WebStructuredAnswer(answer="Alpha Beta Gamma", claims=claims,
        evidence_ids=(source_id,), answer_status="ANSWERED")
    value = assessment(supported_claim_indexes=supported, unsupported_claim_indexes=unsupported)
    assert builtins._claim_review_error(value, len(claims)) is not None
    assert builtins._supported_answer_draft(candidate, value, ()) == {"claims": [], "evidence_hashes": {}}
    payload = builtins._semantic_review_candidate(candidate)
    assert payload["claim_index_order"] == [1, 2, 3]
    assert [item["content"] for item in payload["claims"]] == ["Alpha", "Beta", "Gamma"]


def test_valid_claim_indexes_preserve_the_one_based_contract():
    value = assessment(supported_claim_indexes=(1, 3), unsupported_claim_indexes=(2,))
    assert builtins._claim_review_error(value, 3) is None


def test_conflicting_claim_indexes_require_review_correction():
    value = assessment(supported_claim_indexes=(1, 2), unsupported_claim_indexes=(2,))
    assert "both" in builtins._claim_review_error(value, 2)


def test_repeated_conflicting_verdict_can_only_recover_not_accept():
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    value = assessment(coverage=(AnswerCoverageItem(requirement="Requested records", status="ANSWERED",
        entity_alignment="BROADER", period_alignment="MATCH"),))
    assert b"conflicts" in builtins._coverage_review_error(value, []).encode()
    recovered = builtins._recover_rejected_review(value, [], 0)
    assert recovered.decision == "REPLAN" and not recovered.acceptable
    assert not recovered.answered_core_request
    assert "Requested records" in recovered.missing_aspects


def test_conflicting_review_cannot_salvage_unknown_sources_or_invalid_claim_indexes():
    from uuid import uuid4

    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    value = assessment(coverage=(AnswerCoverageItem(requirement="Requested records", status="MISSING_EVIDENCE",
        evidence_ids=(uuid4(),)),))
    assert builtins._recover_rejected_review(value, [], 0) is None
    assert builtins._recover_rejected_review(value.model_copy(update={"supported_claim_indexes": (0,)}), [], 1) is None
