from enterprise_agent.runtime.conversation_results import _document_citation


def test_procedure_citation_shows_document_name_and_page_range() -> None:
    locator = (
        '{"document_name":"HR-02_leave_policy_v2.pdf",'
        '"mode":"DOCUMENT_PROCEDURE_SECTION",'
        '"source_locators":[{"page":2,"segment":1},{"page":3,"segment":1}]}'
    )

    assert _document_citation(locator) == "HR-02_leave_policy_v2.pdf 第2—3页"


def test_markdown_citation_shows_line_range_without_raw_json() -> None:
    locator = '{"source_locators":[{"line":17},{"line":25}],"mode":"DOCUMENT_NEIGHBORHOOD"}'

    assert _document_citation(locator) == "知识库文档 第17—25行"
