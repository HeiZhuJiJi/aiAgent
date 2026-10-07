from uuid import uuid4

import pytest

from enterprise_agent.capabilities.builtins import (
    AnswerQualityAssessment,
    _apply_coverage_findings,
    _require_quality_replan,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem


def accepted(*items):
    return AnswerQualityAssessment(coverage=items, decision='PARTIAL_ACCEPT', acceptable=True,
        answered_core_request=True, language_matches=True, appropriate_depth=True,
        revision_severity='NONE')


@pytest.mark.parametrize('requirement', [
    'Summary across the requested week', 'Complete department roster',
    'Both product variants and their prices', 'All requested document sections',
])
def test_incomplete_scope_cannot_be_accepted_as_useful_partial(requirement):
    assessment = accepted(AnswerCoverageItem(requirement=requirement, status='MISSING_EVIDENCE'))
    result = _apply_coverage_findings(assessment, [])
    assert result.decision == 'REPLAN'
    assert not result.acceptable
    assert result.missing_aspects == (requirement,)
    with pytest.raises(DomainError, match='EVIDENCE_INSUFFICIENT'):
        _require_quality_replan(result)


def test_existing_evidence_gap_repairs_answer_without_new_search():
    result = _apply_coverage_findings(accepted(AnswerCoverageItem(
        requirement='Second requested section', status='OMITTED_FROM_ANSWER')), [])
    assert result.decision == 'REVISE'
    assert not result.acceptable
    assert result.missing_aspects == ()
    assert 'Second requested section' in result.critique
    _require_quality_replan(result)


@pytest.mark.parametrize('quote,source_matches', [('', True), ('No records exist.', False)])
def test_unavailable_exception_requires_actual_source_support(quote, source_matches):
    source_id = uuid4()
    result = _apply_coverage_findings(accepted(AnswerCoverageItem(
        requirement='Remaining period', status='UNAVAILABLE', evidence_ids=(source_id,),
        limitation_quote=quote)), [{'evidence_id': str(source_id),
            'content': 'No records exist.' if source_matches else 'Navigation index'}])
    assert result.decision == 'REPLAN'
    assert 'Remaining period' in result.missing_aspects


def test_source_established_limit_keeps_supported_partial_answer_acceptable():
    source_id = uuid4()
    value = accepted(AnswerCoverageItem(requirement='Published period', status='ANSWERED'),
        AnswerCoverageItem(requirement='Unpublished period', status='UNAVAILABLE',
            evidence_ids=(source_id,), limitation_quote='Only the next 3 days are published.'))
    result = _apply_coverage_findings(value, [{'evidence_id': str(source_id),
        'content': 'Only the next 3 days are published.'}])
    assert result.acceptable
    assert result.decision == 'PARTIAL_ACCEPT'


def test_complete_summary_does_not_require_daily_rows():
    value = accepted(AnswerCoverageItem(requirement='Requested period trend summary', status='ANSWERED'))
    assert _apply_coverage_findings(value, []).acceptable


def test_missing_and_omitted_requirements_replan_together_and_keep_supported_claims():
    value = accepted(AnswerCoverageItem(requirement='Existing detail', status='OMITTED_FROM_ANSWER'),
        AnswerCoverageItem(requirement='Missing detail', status='MISSING_EVIDENCE'))
    value = value.model_copy(update={'supported_claim_indexes': (1, 2)})
    result = _apply_coverage_findings(value, [])
    assert result.decision == 'REPLAN'
    assert result.supported_claim_indexes == (1, 2)
    assert result.missing_aspects == ('Missing detail',)


@pytest.mark.parametrize('limit', [10000, 50000, 100000])
def test_optional_precheck_preserves_generation_and_recovery_budget(limit):
    from enterprise_agent.runtime.answer_coverage import discovery_check_fits
    assert discovery_check_fits(limit, 0, limit // 5)
    assert not discovery_check_fits(limit, limit // 5, limit // 5)
    assert not discovery_check_fits(limit, limit, 1)


@pytest.mark.parametrize('note', ['Optional measurement unavailable', 'Source does not list an unrequested attribute'])
def test_advisory_limitations_do_not_create_extra_required_work(note):
    value = accepted(AnswerCoverageItem(requirement='Requested overview', status='ANSWERED'))
    value = value.model_copy(update={'inherent_limitations': (note,)})
    result = _apply_coverage_findings(value, [])
    assert result.acceptable
    assert result.decision == 'PARTIAL_ACCEPT'
    assert result.missing_aspects == ()


def test_shortened_date_range_broadens_as_a_whole():
    from enterprise_agent.runtime.research_scope import undated_discovery_query
    query = 'Alpha 2026\u5e749\u67087\u65e5\u81f39\u670813\u65e5 records'
    assert undated_discovery_query(query, ('Alpha',)) == 'Alpha records'


@pytest.mark.parametrize("topic", ["Inventory", "Schedule", "Measurements"])
def test_quote_repair_preserves_long_table_cell_sequence(topic):
    from enterprise_agent.capabilities.builtins import (
        _best_verbatim_quote,
        _normalized_support_text,
    )
    from tests.unit.test_reviewer import review_fixture
    _, _, source = review_fixture()
    body = f"{topic} Alpha 101\n" + "\n".join(["OK"] * 12) + f"\n{topic} Beta 202"
    source = source.model_copy(update={"content": body})
    quote = _best_verbatim_quote(f"{topic}: Alpha 101 and Beta 202",
        f"{topic} Alpha 101\n{topic} Beta 202", source)
    assert quote is not None
    assert _normalized_support_text(quote) in _normalized_support_text(body)
    assert quote.count("OK") == 12
    assert len(quote) <= 600


def test_quote_repair_does_not_bridge_beyond_support_length_limit():
    from enterprise_agent.capabilities.builtins import _best_verbatim_quote
    from tests.unit.test_reviewer import review_fixture
    _, _, source = review_fixture()
    body = "Alpha 101\n" + "\n".join(["intervening data"] * 50) + "\nBeta 202"
    source = source.model_copy(update={"content": body})
    assert _best_verbatim_quote("Alpha 101 and Beta 202", "Alpha 101\nBeta 202", source) is None


@pytest.mark.parametrize('topic', ['Inventory', 'Incident log', 'Measurements'])
def test_short_date_table_rows_survive_context_packing(topic):
    from enterprise_agent.capabilities.builtins import _focused_web_excerpt
    marker = "\u7f51\u9875\u6b63\u6587\u6458\u5f55\uff1a"
    body = f"{topic} 2026\nDate\nValue\n" + "".join(f"09-{day:02d}\n{day * 10} units\n" for day in range(1, 18))
    content = 'Alpha source\n' + marker + '\n' + body
    excerpt = _focused_web_excerpt(content, f'{topic} 2026-09-07 through 2026-09-13', 330)
    for day in range(7, 14):
        assert f'09-{day:02d}\n{day * 10} units' in excerpt
    assert '2026' in excerpt
    assert '09-01' not in excerpt
    assert len(excerpt) <= 330


def test_short_date_records_do_not_infer_year_from_query_or_ambiguous_heading():
    from enterprise_agent.runtime.research_scope import dated_text_records
    assert dated_text_records('Date\n09-07\n12')[0][2] is None
    assert dated_text_records('2025 and 2026 comparison\n09-07\n12')[0][2] is None
    assert dated_text_records('2026 records\n02-30\n12')[0][2] is None
    records = dated_text_records('2025 records\n09-07\n12\n2026 records\n09-07\n34')
    assert [item[2] for item in records] == [(2025, 9, 7), (2026, 9, 7)]


def test_one_requirement_can_reference_every_admitted_answer_source():
    from pydantic import ValidationError

    from enterprise_agent.capabilities.builtins import (
        WebEvidenceSelection,
        WebStructuredAnswer,
        _web_analysis_schema_prompt,
    )
    from enterprise_agent.runtime.answer_coverage import MAX_ANSWER_EVIDENCE

    assert f"1-{MAX_ANSWER_EVIDENCE} supplied UUID strings" in _web_analysis_schema_prompt()
    ids = tuple(uuid4() for _ in range(MAX_ANSWER_EVIDENCE))
    selection = WebEvidenceSelection(evidence_ids=ids)
    answer = WebStructuredAnswer(answer="All requested sections", answer_status="ANSWERED",
        evidence_ids=selection.evidence_ids)
    item = AnswerCoverageItem(requirement="All requested sections", status="ANSWERED",
        entity_alignment="MATCH", period_alignment="MATCH", evidence_ids=answer.evidence_ids)
    assert item.evidence_ids == ids
    assert _apply_coverage_findings(accepted(item), []).acceptable
    # Capacity alignment must not allow broad or partial coverage to pass review.
    assert not _apply_coverage_findings(accepted(item.model_copy(
        update={"entity_alignment": "BROADER"})), []).acceptable
    assert not _apply_coverage_findings(accepted(item.model_copy(
        update={"period_alignment": "PARTIAL"})), []).acceptable
    for schema in (WebEvidenceSelection, WebStructuredAnswer, AnswerCoverageItem):
        assert schema.model_json_schema()["properties"]["evidence_ids"]["maxItems"] == len(ids)
    with pytest.raises(ValidationError):
        AnswerCoverageItem(requirement="All requested sections", status="ANSWERED",
            evidence_ids=ids + (uuid4(),))


def test_model_evidence_transport_preserves_facts_without_mutating_audit_payload():
    from copy import deepcopy

    from enterprise_agent.capabilities.builtins import _model_evidence_payload

    payload = [{"evidence_id": "one", "source_id": "https://example.org/item",
        "locator": "https://example.org/item", "content": "Parent A / Child B: value 12",
        "content_hash": "verified-by-runtime", "substantive_characters": 29,
        "source_identity_context": "Parent A / Child B",
        "excerpt_manifest": {"omitted_blocks": 2, "scope_note": "Repeated instruction"},
        "record_coverage": {"omitted": ["2026-09-10"]}}]
    before = deepcopy(payload)
    result = _model_evidence_payload(payload)
    assert payload == before
    for key in ("evidence_id", "locator", "content", "source_identity_context", "record_coverage"):
        assert result[0][key] == payload[0][key]
    assert result[0]["excerpt_manifest"] == {"omitted_blocks": 2}
    assert "content_hash" not in result[0]
