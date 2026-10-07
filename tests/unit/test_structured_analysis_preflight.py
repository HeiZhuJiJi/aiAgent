import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.capabilities import builtins
from enterprise_agent.contracts.domain import (
    CapabilityRequest,
    ClaimDraft,
    Classification,
    Evidence,
    Risk,
    StructuredAnalysis,
)
from enterprise_agent.models.gateway import (
    FallbackModelGateway,
    Message,
    ModelConfig,
    ModelGateway,
    ModelUsage,
)


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="model.structured_output",
        capability_version="1.0.0",
        arguments={"instruction": "Summarize the selected document."},
    )


def _evidence(request: CapabilityRequest) -> Evidence:
    content = "The policy dated 2025-09-12 requires 5 reviews."
    digest = hashlib.sha256(content.encode()).hexdigest()
    return Evidence(
        evidence_id=uuid4(),
        workspace_id=request.workspace_id,
        task_id=request.task_id,
        step_id=request.step_id,
        source_type="DOCUMENT",
        source_id="selected-document",
        locator="page:4",
        content=content,
        content_hash=digest,
        source_hash=digest,
        source_version="1",
        confidence=1,
        relevance=1,
        retrieved_at=datetime.now(UTC),
        generated_by=request.attempt_id,
        derived_from=(),
    )


def _analysis(evidence_id, content: str) -> StructuredAnalysis:
    return StructuredAnalysis(
        decision="ADMIT",
        risk_level="LOW",
        summary=content,
        key_findings=(content,),
        missing_information=(),
        recommendation="Use the supported finding.",
        evidence_sufficient=True,
        claims=(
            ClaimDraft(
                content=content,
                importance="HIGH",
                risk_level=Risk.READ,
                evidence_ids=(evidence_id,),
            ),
        ),
    )


def _config(model: str) -> ModelConfig:
    return ModelConfig(
        provider="OpenAICompatible",
        base_url=f"https://{model}.example/v1",
        model=model,
        external=False,
        version="1",
    )


def _response(model: str, analysis: StructuredAnalysis) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [
                {"message": {"content": json.dumps(analysis.model_dump(mode="json"))}}
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("ownership", "cross-boundary evidence"),
        ("hash", "content hash mismatch"),
        ("locator", "no citation locator"),
        ("confidence", "no eligible supporting Evidence"),
        ("relevance", "no eligible supporting Evidence"),
        ("date", "unsupported dates or numbers"),
        ("number", "unsupported dates or numbers"),
    ),
)
def test_structured_analysis_preflight_reuses_reviewer_support_rules(
    case: str,
    expected: str,
) -> None:
    request = _request()
    evidence = _evidence(request)
    claim_content = evidence.content
    if case == "ownership":
        evidence = evidence.model_copy(update={"task_id": uuid4()})
    elif case == "hash":
        evidence = evidence.model_copy(update={"content": evidence.content + " changed"})
    elif case == "locator":
        evidence = evidence.model_copy(update={"locator": ""})
    elif case == "confidence":
        evidence = evidence.model_copy(update={"confidence": 0.49})
    elif case == "relevance":
        evidence = evidence.model_copy(update={"relevance": 0.49})
    elif case == "date":
        claim_content = "The policy dated 2026-09-12 requires 5 reviews."
    elif case == "number":
        claim_content = "The policy dated 2025-09-12 requires 6 reviews."

    error = builtins._structured_analysis_validation_error(
        _analysis(evidence.evidence_id, claim_content),
        request,
        (evidence,),
    )

    assert error is not None
    assert expected in error


def test_evidence_sufficient_preflight_requires_a_grounded_claim() -> None:
    request = _request()
    evidence = _evidence(request)
    analysis = _analysis(evidence.evidence_id, evidence.content).model_copy(
        update={"claims": ()}
    )

    error = builtins._structured_analysis_validation_error(
        analysis,
        request,
        (evidence,),
    )

    assert error == "an evidence-sufficient answer must contain at least one grounded Claim"


async def test_reviewer_preflight_repairs_primary_then_uses_structured_fallback() -> None:
    request = _request()
    evidence = _evidence(request)
    unsupported = _analysis(
        evidence.evidence_id,
        "The policy dated 2025-09-12 requires 6 reviews.",
    )
    supported = _analysis(evidence.evidence_id, evidence.content)
    calls = {"primary": 0, "fallback": 0}

    def primary_handler(_request: httpx.Request) -> httpx.Response:
        calls["primary"] += 1
        return _response("primary", unsupported)

    def fallback_handler(_request: httpx.Request) -> httpx.Response:
        calls["fallback"] += 1
        return _response("fallback", supported)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(primary_handler)) as primary_client,
        httpx.AsyncClient(transport=httpx.MockTransport(fallback_handler)) as fallback_client,
    ):
        gateway = FallbackModelGateway(
            ModelGateway(_config("primary"), SecretStr("primary-key"), primary_client),
            ModelGateway(_config("fallback"), SecretStr("fallback-key"), fallback_client),
        )
        result, usage = await gateway.structured(
            (Message(role="user", content="Summarize the selected document."),),
            StructuredAnalysis,
            Classification.INTERNAL,
            validate_result=lambda value: builtins._structured_analysis_validation_error(
                value,
                request,
                (evidence,),
            ),
        )

    assert calls == {"primary": 3, "fallback": 1}
    assert gateway.used_fallback
    assert result == supported
    assert usage == ModelUsage(input_tokens=8, output_tokens=4)
    assert builtins._structured_analysis_validation_error(result, request, (evidence,)) is None
