import json
from collections.abc import Awaitable, Callable

from enterprise_agent.contracts.domain import (
    Classification,
    Contract,
    FailureCode,
    Plan,
    ReplanDecision,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import Message, ModelGateway, ModelUsage
from enterprise_agent.storage.replans import ReplanTrigger, validate_replan_decision


class ReplanContext(Contract):
    plan: Plan
    trigger: ReplanTrigger
    reason: str
    failed_step_keys: tuple[str, ...]


class ReplanPlanner:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def generate(
        self,
        context: ReplanContext,
        *,
        before_call: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[ReplanDecision, ModelUsage]:
        decision, usage = await self._gateway.structured(
            (
                Message(
                    role="system",
                    content=(
                        "You are the governed Replan component. Return only a ReplanDecision. "
                        "Keep completed work when safe. Use only capability identifiers and "
                        "version ranges already present in the current Plan. Do not change the "
                        "Task goal, frozen snapshot, permissions, or runtime policy."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "deterministic_trigger": context.trigger,
                            "reason": context.reason,
                            "failed_step_keys": context.failed_step_keys,
                            "current_plan": context.plan.model_dump(mode="json"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            ),
            ReplanDecision,
            Classification.INTERNAL,
            before_call=before_call,
        )
        if decision.trigger != context.trigger:
            raise DomainError(
                FailureCode.LLM_SCHEMA_ERROR,
                "model changed the deterministic Replan trigger",
            )
        validate_replan_decision(decision, context.plan)
        return decision, usage
