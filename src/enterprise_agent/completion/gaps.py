"""Stable mapping from evidence gaps to governance actions."""

from enterprise_agent.contracts.domain import GapType, RecoveryAction

_RECOVERY_BY_GAP: dict[GapType, RecoveryAction] = {
    GapType.MISSING: RecoveryAction.RETRY,
    GapType.UNSUPPORTED: RecoveryAction.REPLAN,
    GapType.CONFLICT: RecoveryAction.ADDITIONAL_VERIFICATION,
    GapType.STALE: RecoveryAction.REFRESH,
    GapType.AMBIGUOUS: RecoveryAction.CLARIFICATION,
    GapType.INVALID: RecoveryAction.REPAIR,
    GapType.UNKNOWN: RecoveryAction.RECONCILE,
    GapType.AUTHORIZATION: RecoveryAction.AWAIT_AUTHORIZATION,
}


def recovery_action_for_gap(gap_type: GapType) -> RecoveryAction:
    """Return the governance action without changing the gap's factual cause."""
    return _RECOVERY_BY_GAP[gap_type]
