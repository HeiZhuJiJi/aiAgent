import json

import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    FailureCode,
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
    settled_tokens: list[int] = []

    async def reserve():
        nonlocal calls
        calls += 1

    async def settle(usage):
        settled_tokens.append(usage.input_tokens + usage.output_tokens)

    try:
        result, usage = await ReplanPlanner(gateway).generate(
            ReplanContext(
                plan=_plan(),
                trigger="EVIDENCE_INSUFFICIENT",
                reason="missing source",
                failed_step_keys=("research",),
            ),
            before_call=reserve,
            after_call=settle,
        )
    finally:
        await gateway._client.aclose()
    assert result == decision
    assert usage.input_tokens == 12 and usage.output_tokens == 4
    assert calls == 1 and transport.calls == 1
    assert transport.max_tokens == [2048]
    assert settled_tokens == [16]


async def test_replanner_settles_invalid_reply_before_next_repair_budget_check():
    transport = ScriptedModel(("not-json", "also-not-used"))
    gateway = _gateway(transport)
    before_calls = 0
    settled_tokens = 0

    async def reserve():
        nonlocal before_calls
        before_calls += 1
        if settled_tokens:
            raise DomainError(FailureCode.BUDGET_EXCEEDED, "repair budget exhausted")

    async def settle(usage):
        nonlocal settled_tokens
        settled_tokens += usage.input_tokens + usage.output_tokens

    try:
        with pytest.raises(DomainError) as raised:
            await ReplanPlanner(gateway).generate(
                ReplanContext(
                    plan=_plan(),
                    trigger="EVIDENCE_INSUFFICIENT",
                    reason="missing source",
                    failed_step_keys=("research",),
                ),
                before_call=reserve,
                after_call=settle,
            )
    finally:
        await gateway._client.aclose()

    assert raised.value.code is FailureCode.BUDGET_EXCEEDED
    assert before_calls == 2
    assert transport.calls == 1
    assert settled_tokens == 16


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


async def test_replanner_repairs_dangling_dependency_and_receives_absolute_goal_context():
    plan = _plan()
    invalid_step = plan.steps[0].model_copy(update={"dependencies": ("missing-step",)})
    invalid = ReplanDecision(
        reason="collect missing dated observations", trigger="EVIDENCE_INSUFFICIENT",
        action="REPLACE_STEP", affected_steps=("research",), new_steps=(invalid_step,),
        preserve_completed_steps=True, budget_impact=0,
    )
    valid_step = plan.steps[0].model_copy(update={"objective": "daily observations 2026-09-07 2026-09-13"})
    corrected = invalid.model_copy(update={"new_steps": (valid_step,)})
    transport = ScriptedModel([invalid.model_dump_json(), corrected.model_dump_json()])
    gateway = _gateway(transport)
    try:
        result, _ = await ReplanPlanner(gateway).generate(ReplanContext(
            plan=plan, trigger="EVIDENCE_INSUFFICIENT", reason="missing observations",
            failed_step_keys=("research",), goal_context="historical weather 2026-09-07 through 2026-09-13",
            observed_sources=("https://example.org/climate | Archive form without daily observations",),
        ))
    finally:
        await gateway._client.aclose()
    assert result == corrected
    assert transport.calls == 2
    first_prompt = json.dumps(transport.payloads[0]["messages"])
    assert "2026-09-07" in first_prompt and "2026-09-13" in first_prompt
    assert "Archive form without daily observations" in first_prompt
    assert "unknown dependency" in json.dumps(transport.payloads[1]["messages"])


@pytest.mark.parametrize("goal", ["compare software plans", "summarize an attachment", "explain inventory changes"])
async def test_answer_repair_reuses_evidence_and_rejects_redundant_search(goal):
    plan = _plan()
    collect = plan.steps[0]
    synthesis = collect.model_copy(update={
        "step_key": "synthesis", "objective": goal, "dependencies": ("research",),
        "capability_requirements": (CapabilityRequirement(
            capability_id="model.structured_output", version_range=">=1,<2"),),
    })
    plan = plan.model_copy(update={"goal": goal, "steps": (collect, synthesis)})
    additional_search = collect.model_copy(update={"step_key": "search_again"})
    invalid = ReplanDecision(
        reason="try again", trigger="VALIDATION_FAILURE", action="EXPAND_STEP",
        affected_steps=("synthesis",), new_steps=(additional_search, synthesis),
        preserve_completed_steps=True, budget_impact=0,
    )
    repaired = synthesis.model_copy(update={"objective": goal + "; correct the cited row using existing evidence"})
    valid = invalid.model_copy(update={"action": "REPLACE_STEP", "new_steps": (repaired,)})
    transport = ScriptedModel([invalid.model_dump_json(), valid.model_dump_json()])
    gateway = _gateway(transport)
    try:
        result, _ = await ReplanPlanner(gateway).generate(ReplanContext(
            plan=plan, trigger="VALIDATION_FAILURE", reason="ANSWER_REPAIR_REQUIRED: incorrect row",
            failed_step_keys=("synthesis",), goal_context=goal,
        ))
    finally:
        await gateway._client.aclose()
    assert result == valid
    assert transport.calls == 2
    assert result.new_steps[0].dependencies == synthesis.dependencies
    assert result.new_steps[0].capability_requirements == synthesis.capability_requirements
    assert "replace only the failed synthesis" in json.dumps(transport.payloads[1]["messages"])
