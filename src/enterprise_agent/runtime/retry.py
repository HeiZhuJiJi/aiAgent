from enum import StrEnum

from enterprise_agent.contracts.domain import FailureCode, RetryPolicy


class FailureAction(StrEnum):
    BACKOFF_RETRY = "BACKOFF_RETRY"
    RETRY_THEN_MODEL_FALLBACK = "RETRY_THEN_MODEL_FALLBACK"
    REPAIR_MAX_TWO = "REPAIR_MAX_TWO"
    RECONNECT_RETRY = "RECONNECT_RETRY"
    RECONCILE = "RECONCILE"
    DURABLE_RESUME = "DURABLE_RESUME"
    REPAIR_OR_REPLAN = "REPAIR_OR_REPLAN"
    ABORT_OR_HITL = "ABORT_OR_HITL"
    FAIL = "FAIL"


_FAILURE_ACTIONS: dict[FailureCode, FailureAction] = {
    FailureCode.LLM_RATE_LIMIT: FailureAction.BACKOFF_RETRY,
    FailureCode.LLM_TIMEOUT: FailureAction.BACKOFF_RETRY,
    FailureCode.LLM_SERVER_ERROR: FailureAction.RETRY_THEN_MODEL_FALLBACK,
    FailureCode.LLM_SCHEMA_ERROR: FailureAction.REPAIR_MAX_TWO,
    FailureCode.CAPABILITY_TIMEOUT: FailureAction.BACKOFF_RETRY,
    FailureCode.CAPABILITY_UNAVAILABLE: FailureAction.BACKOFF_RETRY,
    FailureCode.CAPABILITY_BAD_OUTPUT: FailureAction.REPAIR_OR_REPLAN,
    FailureCode.CAPABILITY_UNKNOWN_OUTCOME: FailureAction.RECONCILE,
    FailureCode.MCP_DISCONNECTED: FailureAction.RECONNECT_RETRY,
    FailureCode.MCP_SCHEMA_CHANGED: FailureAction.FAIL,
    FailureCode.DB_UNAVAILABLE: FailureAction.BACKOFF_RETRY,
    FailureCode.WORKER_CRASH: FailureAction.DURABLE_RESUME,
    FailureCode.CONTEXT_OVERFLOW: FailureAction.REPAIR_OR_REPLAN,
    FailureCode.BUDGET_EXCEEDED: FailureAction.ABORT_OR_HITL,
    FailureCode.POLICY_DENIED: FailureAction.FAIL,
    FailureCode.APPROVAL_REJECTED: FailureAction.FAIL,
    FailureCode.VALIDATION_FAILED: FailureAction.REPAIR_OR_REPLAN,
    FailureCode.STALE_ATTEMPT: FailureAction.FAIL,
    FailureCode.CONSISTENCY_VIOLATION: FailureAction.FAIL,
}


def classify_failure(code: FailureCode, *, has_side_effect: bool = False) -> FailureAction:
    """Return the frozen domain recovery action, independent of Temporal retry policy."""
    if has_side_effect and code in {
        FailureCode.CAPABILITY_TIMEOUT,
        FailureCode.CAPABILITY_UNAVAILABLE,
        FailureCode.CAPABILITY_BAD_OUTPUT,
        FailureCode.CAPABILITY_UNKNOWN_OUTCOME,
        FailureCode.MCP_DISCONNECTED,
    }:
        return FailureAction.RECONCILE
    return _FAILURE_ACTIONS[code]


def permits_inline_retry(action: FailureAction) -> bool:
    return action in {
        FailureAction.BACKOFF_RETRY,
        FailureAction.RECONNECT_RETRY,
        FailureAction.RETRY_THEN_MODEL_FALLBACK,
    }


def retry_delay(policy: RetryPolicy, failed_attempt: int) -> float:
    """Compute bounded exponential delay after a one-based failed attempt."""
    if failed_attempt < 1:
        raise ValueError("failed_attempt must be one-based")
    return float(
        min(policy.initial_delay_sec * (2 ** (failed_attempt - 1)), policy.max_delay_sec)
    )
