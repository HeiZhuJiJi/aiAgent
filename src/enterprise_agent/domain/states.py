from enterprise_agent.contracts.domain import AttemptState, FailureCode, StepState, TaskState


class DomainError(RuntimeError):
    def __init__(self, code: FailureCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.PLANNING, TaskState.CANCELLING, TaskState.FAILED}),
    TaskState.PLANNING: frozenset({TaskState.READY, TaskState.CANCELLING, TaskState.FAILED}),
    TaskState.READY: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLING, TaskState.FAILED}
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_HITL,
            TaskState.PAUSED,
            TaskState.PLANNING,
            TaskState.CANCELLING,
            TaskState.COMPLETED,
            TaskState.FAILED,
        }
    ),
    TaskState.WAITING_HITL: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLING, TaskState.FAILED}
    ),
    TaskState.PAUSED: frozenset(
        {TaskState.READY, TaskState.WAITING_HITL, TaskState.CANCELLING, TaskState.FAILED}
    ),
    TaskState.CANCELLING: frozenset({TaskState.CANCELLED}),
    TaskState.CANCELLED: frozenset(),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
}
STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PENDING: frozenset(
        {
            StepState.READY,
            StepState.BLOCKED,
            StepState.CANCELLED,
            StepState.SKIPPED,
            StepState.SUPERSEDED,
        }
    ),
    StepState.READY: frozenset(
        {
            StepState.RUNNING,
            StepState.WAITING_HITL,
            StepState.BLOCKED,
            StepState.CANCELLED,
            StepState.SUPERSEDED,
        }
    ),
    StepState.RUNNING: frozenset(
        {
            StepState.COMPLETED,
            StepState.RETRY_WAIT,
            StepState.WAITING_HITL,
            StepState.FAILED,
            StepState.CANCELLED,
        }
    ),
    StepState.RETRY_WAIT: frozenset(
        {StepState.READY, StepState.FAILED, StepState.CANCELLED, StepState.SUPERSEDED}
    ),
    StepState.WAITING_HITL: frozenset(
        {StepState.READY, StepState.FAILED, StepState.CANCELLED, StepState.SUPERSEDED}
    ),
    StepState.BLOCKED: frozenset(
        {StepState.READY, StepState.CANCELLED, StepState.SKIPPED, StepState.SUPERSEDED}
    ),
    StepState.COMPLETED: frozenset(),
    StepState.FAILED: frozenset({StepState.SUPERSEDED}),
    StepState.CANCELLED: frozenset(),
    StepState.SKIPPED: frozenset(),
    StepState.SUPERSEDED: frozenset(),
}
ATTEMPT_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.CREATED: frozenset({AttemptState.RUNNING, AttemptState.CANCELLED}),
    AttemptState.RUNNING: frozenset(
        {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.UNKNOWN, AttemptState.CANCELLED}
    ),
    AttemptState.UNKNOWN: frozenset({AttemptState.SUCCEEDED, AttemptState.FAILED}),
    AttemptState.SUCCEEDED: frozenset(),
    AttemptState.FAILED: frozenset(),
    AttemptState.CANCELLED: frozenset(),
}


def transition[S: (TaskState, StepState, AttemptState)](current: S, target: S) -> S:
    if type(current) is not type(target):
        raise DomainError(FailureCode.VALIDATION_FAILED, "state types differ")
    allowed: frozenset[TaskState] | frozenset[StepState] | frozenset[AttemptState]
    if isinstance(current, TaskState):
        allowed = TASK_TRANSITIONS[current]
    elif isinstance(current, StepState):
        allowed = STEP_TRANSITIONS[current]
    else:
        allowed = ATTEMPT_TRANSITIONS[current]
    if target not in allowed:
        raise DomainError(
            FailureCode.VALIDATION_FAILED, f"illegal transition {current} -> {target}"
        )
    return target
