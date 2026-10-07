from apps.web_provider.main import extract_visible_text
from enterprise_agent.runtime.evidence_records import (
    matching_records,
    pack_records,
    record_manifest,
)
from enterprise_agent.runtime.research_scope import dated_text_records


def test_html_table_keeps_cells_units_empty_positions_and_spans():
    html = """<h2>Alpha inventory 2026</h2><table><tr><th>Date</th><th>Units</th><th>Cost (EUR)</th></tr>
    <tr><td>09-07</td><td><b>12</b></td><td>4.5</td></tr>
    <tr><td rowspan="2">09-08</td><td></td><td>0</td></tr>
    <tr><td colspan="2">Unavailable</td></tr></table>"""
    text = extract_visible_text(html)
    assert 'Date | Units | Cost (EUR)' in text
    assert '09-07 | 12 | 4.5' in text
    assert '09-08 [rowspan=2] | | 0' in text
    assert 'Unavailable [colspan=2]' in text
    assert 'Alpha inventory 2026' in text


def test_records_keep_each_entity_section_and_do_not_merge_same_date_values():
    source = 'Alpha branch 2026\nDate | Units\n09-07 | 12\nBeta branch 2026\nDate | Units\n09-07 | 99\n'
    records = matching_records(source, 'Alpha 2026-09-07')
    assert len(records) == 2
    assert records[0].context == 'Alpha branch 2026\nDate | Units'
    assert records[1].context == 'Beta branch 2026\nDate | Units'
    assert 'Beta' not in records[0].text
    assert '12' not in records[1].text
    excerpt = pack_records(source, 'Beta 2026-09-07', 60)
    assert excerpt is not None and excerpt.startswith('Beta branch')
    assert '99' in excerpt and '12' not in excerpt
    manifest = record_manifest(source, excerpt, 'Beta 2026-09-07')
    assert manifest['matching_records_in_source'] == 2
    assert manifest['complete_records_in_excerpt'] == 1
    assert manifest['omitted_dates'] == ['2026-09-07']


def test_context_is_required_to_count_a_record_as_included():
    source = 'Alpha 2026\nDate | Price (USD)\n09-07 | 25\n'
    assert record_manifest(source, '09-07 | 25', '2026-09-07')['complete_records_in_excerpt'] == 0


def test_four_digit_measurement_does_not_change_inherited_year():
    records = dated_text_records('Alpha 2026 records\n09-07\n2025\n09-08\n2024')
    assert [stamp for _, _, stamp in records] == [(2026, 9, 7), (2026, 9, 8)]


def test_insufficient_budget_never_returns_detached_or_partial_row():
    source = 'Alpha 2026\nDate | Units\n09-07 | ' + '1' * 100
    assert pack_records(source, '2026-09-07', 50) is None


def test_broader_entity_or_partial_period_cannot_pass_as_answered():
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem, coverage_gaps
    for entity, period in [('BROADER', 'MATCH'), ('DIFFERENT', 'MATCH'), ('MATCH', 'PARTIAL'), ('MATCH', 'DIFFERENT')]:
        item = AnswerCoverageItem(requirement='Requested branch quarterly results', status='ANSWERED',
                                  entity_alignment=entity, period_alignment=period)
        assert coverage_gaps((item,), []) == (('Requested branch quarterly results',), ())


def test_live_review_requires_explicit_scope_alignment():
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem, alignment_review_error
    item = AnswerCoverageItem(requirement='Named product version', status='ANSWERED')
    assert alignment_review_error((item,)) is not None
    assert alignment_review_error((item.model_copy(update={'entity_alignment': 'MATCH', 'period_alignment': 'MATCH'}),)) is None


def test_parallel_lists_keep_boundaries_empty_items_and_numeric_siblings():
    html = '<h2>Alpha inventory</h2><ul><li>A</li><li>B</li></ul><ul><li><span>33</span><span>26</span></li><li></li></ul>'
    text = extract_visible_text(html)
    assert text.count('<ul>') == text.count('</ul>') == 2
    assert '<li>A</li>' in text and '<li>B</li>' in text
    assert '<li>33 | 26</li>' in text
    assert '<li></li>' in text
    assert '3326' not in text
    assert 'A | 33' not in text  # No inferred pairing between parallel lists.


def test_inline_formatting_does_not_split_a_number_without_sibling_elements():
    assert extract_visible_text('<p>1<b>000</b> units</p>') == '1000 units'
    assert extract_visible_text('<p>Version <b>1</b>.2</p>') == 'Version 1.2'


def test_nested_and_unclosed_lists_keep_parent_text_and_links():
    text = extract_visible_text('<h1>Catalog</h1><ul><li>Alpha<ul><li>Nested</li></ul></li><li>Beta')
    assert 'Catalog' in text and 'Alpha' in text and 'Nested' in text and 'Beta' in text
    assert text.count('<ul>') == text.count('</ul>') == 2


def test_structured_block_excerpt_keeps_parallel_lists_as_a_whole():
    from enterprise_agent.runtime.evidence_records import excerpt_manifest, pack_structured_blocks
    body = extract_visible_text('<h2>Alpha inventory</h2><ul><li>2026-09-07</li><li>2026-09-08</li></ul><ul><li>12 units</li><li>0 units</li></ul>')
    excerpt = pack_structured_blocks(body, 'Alpha inventory 2026-09-07', len(body))
    assert excerpt == body
    assert pack_structured_blocks(body, 'Alpha inventory', 30) is None
    manifest = excerpt_manifest(body, excerpt)
    assert manifest['source_fully_included'] is True
    assert manifest['complete_blocks_in_excerpt'] == 1
    assert excerpt_manifest(body, body[:30])['omitted_blocks'] == 1


def test_numeric_siblings_in_table_do_not_coalesce():
    text = extract_visible_text('<table><tr><td>Price</td><td><span>12</span><span>15</span></td></tr></table>')
    assert '12 | 15' in text
    assert '1215' not in text


def test_provider_preserves_complete_lists_before_runtime_selection():
    from apps.web_provider.main import relevant_excerpt
    data = extract_visible_text('<h2>Alpha product inventory</h2><ul><li>A</li><li>B</li></ul><ul><li>12 units</li><li>15 units</li></ul>')
    source = ('Unrelated navigation line\n' * 100) + data + '\nOther news\n' + ('Unrelated article text\n' * 100)
    result = relevant_excerpt('Alpha product inventory', source, 300)
    assert data in result
    assert result.count('<ul>') == result.count('</ul>') == 2
    assert len(result) <= 300


def test_unused_context_capacity_rereads_original_without_increasing_total_budget():
    import hashlib
    from uuid import uuid4

    from enterprise_agent.capabilities.builtins import _analysis_evidence_payload
    from tests.unit.test_bounded_qa_fast_paths import _answer_contract
    from tests.unit.test_reviewer import review_fixture

    _, _, base = review_fixture()
    long_body = 'Alpha inventory\n' + 'Record data. ' * 105 + '\nAdditional required item: Beta 17 units.'
    sources = []
    for index, body in enumerate((long_body, 'Alpha short supporting source.')):
        sources.append(base.model_copy(update={'evidence_id': uuid4(), 'source_type': 'WEB',
            'locator': f'https://records.example/{index}', 'content': body,
            'content_hash': hashlib.sha256(body.encode()).hexdigest()}))
    payload = _analysis_evidence_payload(tuple(sources), 'web-research', 'Alpha inventory', _answer_contract('FACT'),
        web_character_budget=2000, web_source_limit=2)
    assert sum(len(item['content']) for item in payload) <= 2000
    target = next(item for item in payload if item['evidence_id'] == str(sources[0].evidence_id))
    assert target['content'] == long_body
    assert target['excerpt_manifest']['source_fully_included'] is True
    assert target['content_hash'] == sources[0].content_hash


def test_short_source_keeps_record_date_outside_list():
    from enterprise_agent.capabilities.builtins import (
        _focused_web_excerpt,
        _forecast_evidence_excerpt,
    )
    source = "Record Alpha. Valid 2026-09-19. Units EUR.\n网页正文摘录：\n<ul>\n<li>Alpha: 17</li>\n</ul>\nUpdated at 2026-09-18 UTC"
    for pack in (_focused_web_excerpt, _forecast_evidence_excerpt):
        assert pack(source, "Alpha 2026-09-19", len(source)) == source


def test_table_packing_preserves_headers_blank_cells_units_and_issue_context():
    from enterprise_agent.runtime.evidence_records import pack_table_blocks
    table = "Metric | Fri 18 [colspan=2] | Sat 19 [colspan=2]\nPeriod | AM | PM | AM | PM\nmax EUR | 11 | 12 | 13 | 14\nmin EUR | 8 | | 9 | 10\n"
    body = "Alpha report issued 2026-09-18 UTC\n" + table + "Unrelated navigation " * 300
    packed = pack_table_blocks(body, "Alpha 2026-09-19", 500)
    assert packed is not None and len(packed) <= 500
    assert table.strip() in packed
    assert "issued 2026-09-18 UTC" in packed
    assert pack_table_blocks(body, "Alpha", 40) is None


def test_reread_does_not_repeat_identical_context_or_treat_it_as_accepted(monkeypatch):
    from enterprise_agent.capabilities import builtins
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    from tests.unit.test_answer_coverage import accepted
    quality = accepted(AnswerCoverageItem(requirement="Alpha detail", status="MISSING_EVIDENCE"))
    quality = builtins._apply_coverage_findings(quality, [])
    payload = [{"evidence_id": "a", "content": "first section",
                "excerpt_manifest": {"source_fully_included": False}}]
    monkeypatch.setattr(builtins, "_analysis_evidence_payload", lambda *args, **kwargs: payload)
    assert builtins._reread_existing_evidence((), payload, quality, "Alpha", None) is None
    changed = [{"evidence_id": "a", "content": "first section and requested detail"}]
    monkeypatch.setattr(builtins, "_analysis_evidence_payload", lambda *args, **kwargs: changed)
    assert builtins._reread_existing_evidence((), payload, quality, "Alpha", None) == changed
    assert quality.decision == "REPLAN" and not quality.acceptable


def test_complete_table_takes_priority_over_many_truncated_previews():
    import hashlib
    from uuid import uuid4

    from enterprise_agent.capabilities.builtins import _analysis_evidence_payload
    from tests.unit.test_bounded_qa_fast_paths import _answer_contract
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    table = "Metric | Alpha | Beta\n" + "\n".join(f"Record {i} units | {i} | {i + 1}" for i in range(35))
    body = "Alpha inventory issued 2026-09-18 UTC\n" + table + "\n" + "Navigation links " * 100
    sources = []
    for index, content in enumerate([body, *("Alpha additional source " + "details " * 70 for _ in range(10))]):
        sources.append(base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB",
            "locator": f"https://source{index}.example/report", "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest()}))
    payload = _analysis_evidence_payload(tuple(sources), "web-research", "Alpha inventory",
        _answer_contract("FACT"), web_character_budget=2000, web_source_limit=16)
    assert sum(len(item["content"]) for item in payload) <= 2000
    target = next(item for item in payload if item["evidence_id"] == str(sources[0].evidence_id))
    assert table in target["content"]
    assert len(payload) >= 2


def test_short_menu_does_not_displace_relevant_explanatory_prose():
    from enterprise_agent.capabilities.builtins import _focused_web_excerpt
    body = "Research guide\n网页正文摘录：\n<ul>\n<li>Alpha</li>\n<li>Beta</li>\n</ul>\n"
    explanation = "Alpha and Beta differ because one transmits energy while the other stores it. This distinction determines their respective use in the system."
    body += explanation + "\n" + "Unrelated text " * 100
    excerpt = _focused_web_excerpt(body, "Alpha Beta distinction", 400)
    assert explanation in excerpt



def test_many_unrelated_menu_labels_cannot_outvote_explanatory_record():
    from enterprise_agent.capabilities.builtins import _focused_web_excerpt
    from enterprise_agent.runtime.evidence_records import pack_structured_blocks
    menu = "<ul>\n" + "\n".join(f"<li>{word}</li>" for word in
        ("spectral", "absorption", "reflection", "measurements", "instruments", "laboratory")) + "\n</ul>\n"
    paragraph = ("Spectral absorption measures how a material takes up incident light at particular wavelengths. "
                 "Reflection describes the portion of the incident light returned from its surface.")
    body = menu + "Unrelated navigation.\n" * 30 + paragraph
    query = "spectral absorption reflection measurements instruments laboratory"
    assert pack_structured_blocks(body, query, 500) is None
    excerpt = _focused_web_excerpt("Optics report\n网页正文摘录：\n" + body, query, 500)
    assert paragraph in excerpt



def test_selection_previews_cannot_drop_sources_to_fit_first_large_table():
    import hashlib
    from uuid import uuid4

    from enterprise_agent.capabilities.builtins import _analysis_evidence_payload
    from tests.unit.test_bounded_qa_fast_paths import _answer_contract
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    table = "Metric | Alpha | Beta\n" + "\n".join(f"Record {i} units | {i} | {i+1}" for i in range(35))
    texts = ["Alpha inventory\n" + table, *(f"Alpha record {i}\n" + "details " * 90 for i in range(6))]
    sources = tuple(base.model_copy(update={"evidence_id": uuid4(), "source_type": "WEB",
        "locator": f"https://source{i}.example/report", "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest()}) for i,text in enumerate(texts))
    payload = _analysis_evidence_payload(sources, "web-research", "Alpha inventory", _answer_contract("FACT"),
        web_character_budget=2500, web_source_limit=16, preserve_sources=True)
    assert {item["evidence_id"] for item in payload} == {str(item.evidence_id) for item in sources}
    assert sum(len(item["content"]) for item in payload) <= 2500



def test_explicit_local_read_gets_complete_target_without_losing_other_source():
    import hashlib
    from uuid import uuid4

    from enterprise_agent.capabilities import builtins
    from tests.unit.test_reviewer import review_fixture
    _, _, base = review_fixture()
    text = "Warehouse report\n" + "Source background details. " * 260 + "\nWarehouse Alpha has 31 items."
    source = base.model_copy(update={"source_type": "WEB", "evidence_id": uuid4(), "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest()})
    other_text = "Beta has 12 items."
    other = base.model_copy(update={"source_type": "WEB", "evidence_id": uuid4(), "content": other_text,
        "content_hash": hashlib.sha256(other_text.encode()).hexdigest()})
    quality = builtins.AnswerQualityAssessment(decision="REPLAN", acceptable=False,
        answered_core_request=False, language_matches=True, appropriate_depth=True,
        read_requests=(builtins.EvidenceReadRequest(evidence_id=source.evidence_id, focus="Warehouse Alpha inventory"),))
    payload = [{"evidence_id": str(source.evidence_id), "content": "Warehouse report",
        "excerpt_manifest": {"source_fully_included": False}}]
    result = builtins._reread_existing_evidence((source,other),payload,quality,"Compare inventory",None)
    assert result is not None
    assert next(item["content"] for item in result if item["evidence_id"] == str(source.evidence_id)) == text
    assert any(other_text in item["content"] for item in result)
    assert sum(len(item["content"]) for item in result) <= 14000
    assert not quality.acceptable
