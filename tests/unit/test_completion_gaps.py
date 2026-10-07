import pytest

from enterprise_agent.completion.gaps import recovery_action_for_gap
from enterprise_agent.contracts.domain import GapType, RecoveryAction


@pytest.mark.parametrize(
    ("gap_type", "action"),
    (
        (GapType.MISSING, RecoveryAction.RETRY),
        (GapType.UNSUPPORTED, RecoveryAction.REPLAN),
        (GapType.CONFLICT, RecoveryAction.ADDITIONAL_VERIFICATION),
        (GapType.STALE, RecoveryAction.REFRESH),
        (GapType.AMBIGUOUS, RecoveryAction.CLARIFICATION),
        (GapType.INVALID, RecoveryAction.REPAIR),
        (GapType.UNKNOWN, RecoveryAction.RECONCILE),
        (GapType.AUTHORIZATION, RecoveryAction.AWAIT_AUTHORIZATION),
    ),
)
def test_gap_cause_maps_to_governance_action_without_changing_the_cause(
    gap_type: GapType, action: RecoveryAction
) -> None:
    assert recovery_action_for_gap(gap_type) is action
