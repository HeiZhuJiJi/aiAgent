import hashlib
from uuid import uuid4

import pytest

from enterprise_agent.capabilities import builtins as b
from tests.unit.test_review_materiality import assessment
from tests.unit.test_reviewer import review_fixture


def fixture():
    _, _, source = review_fixture()
    text = "Alpha: 10 units. Beta: 20 units."
    source = source.model_copy(update={"content": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
    claim = b.GroundedAnswerClaim(content="Alpha: 10 units.", supports=(
        b.EvidenceSupport(evidence_id=source.evidence_id, quote="Alpha: 10 units."),))
    candidate = b.WebStructuredAnswer(answer="Alpha: 10 units. Beta not found.", answer_status="ANSWERED",
        evidence_ids=(source.evidence_id,), claims=(claim,))
    patch = b.AnswerRepairPatch(edits=(b.AnswerTextEdit(before="Beta not found.", after="Beta: 20 units."),),
        replacement_claims=(b.GroundedAnswerClaim(content="Beta: 20 units.", supports=(
            b.EvidenceSupport(evidence_id=source.evidence_id, quote="Beta: 20 units."),)),))
    quality = assessment(decision="REVISE", acceptable=False, revision_severity="MATERIAL",
        blocking_issue="Beta already exists in source", repair_patch=patch)
    return candidate, quality, source


def test_patch_preserves_unaffected_answer_and_claim_without_accepting_review():
    candidate, quality, source = fixture()
    result = b._apply_answer_repair_patch(candidate, quality, (source,))
    assert result.answer == "Alpha: 10 units. Beta: 20 units."
    assert result.claims[0] == candidate.claims[0]
    assert len(result.claims) == 2
    assert not quality.acceptable
    assert candidate.answer.endswith("not found.")


@pytest.mark.parametrize("before", ["not in answer", "a"])
def test_ambiguous_or_missing_edit_is_not_applied(before):
    candidate, quality, source = fixture()
    patch = quality.repair_patch.model_copy(update={"edits": (b.AnswerTextEdit(before=before, after="changed"),)})
    assert b._apply_answer_repair_patch(candidate, quality.model_copy(update={"repair_patch": patch}), (source,)) is None


def test_overlapping_edits_are_rejected():
    candidate, quality, source = fixture()
    patch = quality.repair_patch.model_copy(update={"edits": (
        b.AnswerTextEdit(before="Beta not found.", after="Beta: 20 units."),
        b.AnswerTextEdit(before="not found", after="known"))})
    assert b._apply_answer_repair_patch(candidate, quality.model_copy(update={"repair_patch": patch}), (source,)) is None


@pytest.mark.parametrize("change", ["quote", "source", "index", "search", "read"])
def test_patch_cannot_bypass_evidence_or_recovery_boundaries(change):
    candidate, quality, source = fixture()
    patch = quality.repair_patch
    if change in {"quote", "source"}:
        support = b.EvidenceSupport(evidence_id=uuid4() if change == "source" else source.evidence_id,
            quote="invented measurement" if change == "quote" else "Beta: 20 units.")
        patch = patch.model_copy(update={"replacement_claims": (b.GroundedAnswerClaim(content="Beta", supports=(support,)),)})
    elif change == "index":
        patch = patch.model_copy(update={"remove_claim_indexes": (0,)})
    elif change == "search":
        quality = quality.model_copy(update={"missing_aspects": ("Gamma",)})
    else:
        quality = quality.model_copy(update={"read_requests": (b.EvidenceReadRequest(evidence_id=source.evidence_id, focus="Gamma"),)})
    quality = quality.model_copy(update={"repair_patch": patch})
    assert b._apply_answer_repair_patch(candidate, quality, (source,)) is None


def test_review_preserves_exact_claim_source_binding():
    candidate, _, source = fixture()
    payload = b._review_citation_quotes(candidate)
    assert payload[0]['claim_indexes'] == [1]
    support = payload[0]
    assert support['evidence_id'] == str(source.evidence_id)
    assert support['quote'] == 'Alpha: 10 units.'


def test_answer_only_revision_does_not_reselect_source_windows():
    _, quality, source = fixture()
    payload = [{'evidence_id': str(source.evidence_id), 'content': 'Alpha: 10 units.',
                'excerpt_manifest': {'source_fully_included': False}}]
    assert b._reread_existing_evidence((source,), payload, quality, 'Alpha and Beta', None) is None


def test_identical_citation_witness_is_transmitted_once():
    candidate, _, _ = fixture()
    candidate = candidate.model_copy(update={"claims": candidate.claims * 3})
    witnesses = b._review_citation_quotes(candidate)
    assert len(witnesses) == 1
    assert witnesses[0]['claim_indexes'] == [1, 2, 3]


def test_multi_source_claim_does_not_require_all_values_in_each_quote():
    _, _, source = fixture()
    source = source.model_copy(update={"content": "Alpha inventory\n10\nmeasured amount\nitems"})
    restored = b._best_verbatim_quote("Alpha has 10 items; Beta has 20 items.",
                                    "Alpha inventory\n10\nitems", source)
    assert restored is not None
    assert "10" in restored and "measured amount" in restored
    assert "20" not in restored
    assert b._normalized_support_text(restored) in b._normalized_support_text(source.content)


def test_quote_repair_cannot_silently_change_a_hinted_measurement():
    _, _, source = fixture()
    assert b._best_verbatim_quote("Alpha has 999 items.", "Alpha inventory 999 items", source) is None


def test_limited_delivery_contains_only_supported_hash_bound_facts():
    from enterprise_agent.runtime.conversation_results import _partial_review_message
    candidate, quality, source = fixture()
    source = source.model_copy(update={"source_type": "WEB", "locator": "https://inventory.example/report"})
    quality = quality.model_copy(
        update={"supported_claim_indexes": (1,), "missing_aspects": ("Beta",)}
    )
    record = {**quality.model_dump(mode="json"), "outcome": "COMPLETED",
              "supported_draft": b._supported_answer_draft(candidate, quality, (source,)),
              "coverage": [{"entity_alignment": "MATCH", "period_alignment": "PARTIAL",
                            "evidence_ids": [str(source.evidence_id)]}]}
    text = _partial_review_message(record, (source,))
    assert "Alpha: 10 units." in text
    assert "Beta not found" not in text
    assert "已找到并核验部分信息" in text
    assert "尚未核验的信息" in text and "Beta" in text
    assert "不会自动作为后续问题的已确认前提" in text
    assert 'https://inventory.example/report' in text
    changed = source.model_copy(update={"content": source.content + ' changed'})
    assert _partial_review_message(record, (changed,)) is None
    assert _partial_review_message({**record, "outcome": "UNAVAILABLE"}, (source,)) is None
    assert _partial_review_message({**record, "supported_draft": {}}, (source,)) is None
    assert _partial_review_message({**record, "coverage": []}, (source,)) is None
    assert _partial_review_message({**record, "coverage": [{"entity_alignment": "BROADER",
        "period_alignment": "MATCH", "evidence_ids": [str(source.evidence_id)]}]}, (source,)) is None
