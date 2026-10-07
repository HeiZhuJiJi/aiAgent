from uuid import uuid4

from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.contracts.domain import CapabilityRequest
from enterprise_agent.runtime.activities import _result_evidence


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="web.search",
        capability_version="1.0.0",
        arguments={"query": "weather tomorrow"},
    )


def test_empty_web_results_do_not_create_evidence() -> None:
    descriptor = next(item for item in builtin_descriptors() if item.id == "web.search")

    result = _result_evidence(
        request=_request(),
        descriptor=descriptor,
        result_output={"query": "weather tomorrow", "results": []},
        derived_from=(),
    )

    assert result == ()


def test_empty_rag_hits_do_not_create_fake_document_evidence() -> None:
    descriptor = next(
        item for item in builtin_descriptors() if item.id == "knowledge.hybrid_search"
    )
    request = _request().model_copy(
        update={
            "capability_id": "knowledge.hybrid_search",
            "arguments": {"query": "summarize selected document"},
        }
    )

    result = _result_evidence(
        request=request,
        descriptor=descriptor,
        result_output={"query": "summarize selected document", "hits": []},
        derived_from=(),
    )

    assert result == ()


def test_non_empty_web_results_keep_real_source_evidence() -> None:
    descriptor = next(item for item in builtin_descriptors() if item.id == "web.search")
    url = "https://weather.example/forecast"

    result = _result_evidence(
        request=_request(),
        descriptor=descriptor,
        result_output={
            "query": "weather tomorrow",
            "results": [
                {
                    "title": "Forecast",
                    "url": url,
                    "snippet": "Tomorrow will be sunny.",
                    "published_at": None,
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "content_hash": "a" * 64,
                }
            ],
        },
        derived_from=(),
    )

    assert len(result) == 1
    assert result[0].source_type == "WEB"
    assert result[0].locator == url
    assert result[0].content == "Tomorrow will be sunny."
