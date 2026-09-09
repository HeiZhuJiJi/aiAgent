import pytest

from enterprise_agent.contracts.domain import FailureCode, RetryPolicy
from enterprise_agent.runtime.retry import (
    FailureAction,
    classify_failure,
    permits_inline_retry,
    retry_delay,
)


@pytest.mark.parametrize(
    ("code", "action"),
    [
        (FailureCode.LLM_RATE_LIMIT, FailureAction.BACKOFF_RETRY),
        (FailureCode.LLM_SERVER_ERROR, FailureAction.RETRY_THEN_MODEL_FALLBACK),
        (FailureCode.LLM_TIMEOUT, FailureAction.BACKOFF_RETRY),
        (FailureCode.LLM_SCHEMA_ERROR, FailureAction.REPAIR_MAX_TWO),
        (FailureCode.CAPABILITY_TIMEOUT, FailureAction.BACKOFF_RETRY),
        (FailureCode.CAPABILITY_UNKNOWN_OUTCOME, FailureAction.RECONCILE),
        (FailureCode.MCP_DISCONNECTED, FailureAction.RECONNECT_RETRY),
        (FailureCode.WORKER_CRASH, FailureAction.DURABLE_RESUME),
        (FailureCode.VALIDATION_FAILED, FailureAction.REPAIR_OR_REPLAN),
        (FailureCode.BUDGET_EXCEEDED, FailureAction.ABORT_OR_HITL),
    ],
)
def test_frozen_retry_matrix(code: FailureCode, action: FailureAction) -> None:
    assert classify_failure(code) == action


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.CAPABILITY_TIMEOUT,
        FailureCode.CAPABILITY_UNAVAILABLE,
        FailureCode.CAPABILITY_BAD_OUTPUT,
        FailureCode.CAPABILITY_UNKNOWN_OUTCOME,
        FailureCode.MCP_DISCONNECTED,
    ],
)
def test_side_effect_uncertainty_always_reconciles(code: FailureCode) -> None:
    action = classify_failure(code, has_side_effect=True)
    assert action == FailureAction.RECONCILE
    assert not permits_inline_retry(action)


def test_retry_delay_is_exponential_and_bounded() -> None:
    policy = RetryPolicy(max_attempts=5, initial_delay_sec=0.25, max_delay_sec=0.75)
    assert [retry_delay(policy, attempt) for attempt in range(1, 5)] == [
        0.25,
        0.5,
        0.75,
        0.75,
    ]
    with pytest.raises(ValueError):
        retry_delay(policy, 0)
