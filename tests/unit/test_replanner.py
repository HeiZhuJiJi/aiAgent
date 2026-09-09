import json

import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    Risk,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import ModelConfig, ModelGateway
from enterprise_agent.runtime.replanner import ReplanContext, ReplanPlanner
from tests.fakes.model import ScriptedModel


def _plan():
    from uuid import uuid4

    return Plan(
        plan_id=uuid4(),
        task_id=uuid4(),
        workspace_id=uuid4(),
        execution_snapshot_id=uuid4(),
        version=1,
        goal="supplier risk",
        constraints=(),
        deliverables=("report",),
        steps=(
            PlanStepSpec(
                step_key="research",
                objective="collect evidence",
                capability_requirements=(
                    CapabilityRequirement(
                        capability_id="knowledge.hybrid_search", version_range=">=1,<2"
                    ),
                ),
                expected_output="evidence",
                success_criteria=("grounded",),
                risk_hint=Risk.READ,
                execution_mode="CAPABILITY",
                parallelizable=True,
                max_attempts=2,
            ),
        ),
    )


def _gateway(transport):
    return ModelGateway(
        ModelConfig(
            provider="OpenAICompatible",
            base_url="https://model.invalid/v1",
            model="test-model",
            external=False,
            version="test",
        ),
        SecretStr(""),
        httpx.AsyncClient(transport=transport),
    )


async def test_replanner_uses_structured_gateway_and_preserves_deterministic_trigger():
    decision = ReplanDecision(
        reason="try the failed step with refined inputs",
        trigger="EVIDENCE_INSUFFICIENT",
        action="RETRY_STEP",
        affected_steps=("research",),
        new_steps=(),
        preserve_completed_steps=True,
        budget_impact=0,
    )
    transport = ScriptedModel([decision.model_dump_json()])
    gateway = _gateway(transport)
    calls = 0

    async def reserve():
        nonlocal calls
        calls += 1

    try:
        result, usage = await ReplanPlanner(gateway).generate(
            ReplanContext(
                plan=_plan(),
                trigger="EVIDENCE_INSUFFICIENT",
                reason="missing source",
                failed_step_keys=("research",),
            ),
            before_call=reserve,
        )
    finally:
        await gateway._client.aclose()
    assert result == decision
    assert usage.input_tokens == 12 and usage.output_tokens == 4
    assert calls == 1 and transport.calls == 1


async def test_replanner_rejects_model_attempt_to_change_runtime_trigger():
    payload = json.loads(
        ReplanDecision(
            reason="abort",
            trigger="TERMINAL_FAILURE",
            action="ABORT_TASK",
            affected_steps=(),
            new_steps=(),
            preserve_completed_steps=True,
            budget_impact=0,
        ).model_dump_json()
    )
    gateway = _gateway(ScriptedModel([json.dumps(payload)]))
    try:
        with pytest.raises(DomainError, match="deterministic Replan trigger"):
            await ReplanPlanner(gateway).generate(
                ReplanContext(
                    plan=_plan(),
                    trigger="EVIDENCE_INSUFFICIENT",
                    reason="missing source",
                    failed_step_keys=("research",),
                )
            )
    finally:
        await gateway._client.aclose()
