from __future__ import annotations

from enum import StrEnum
from threading import Lock

from pydantic import Field

from enterprise_agent.contracts.domain import Contract


class FailurePoint(StrEnum):
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_429 = "LLM_429"
    LLM_SCHEMA_CORRUPTION = "LLM_SCHEMA_CORRUPTION"
    MCP_TIMEOUT = "MCP_TIMEOUT"
    MCP_DISCONNECT = "MCP_DISCONNECT"
    CAPABILITY_RESPONSE_LOST = "CAPABILITY_RESPONSE_LOST"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"


class FailureDirective(Contract):
    point: FailurePoint
    occurrences: tuple[int, ...] = Field(min_length=1)


class InjectedFailure(RuntimeError):
    def __init__(self, point: FailurePoint) -> None:
        super().__init__(f"deterministic failure injected at {point.value}")
        self.point = point


class FailureInjector:
    """Deterministic, opt-in failure schedule used at real runtime boundaries."""

    def __init__(self, directives: tuple[FailureDirective, ...] = ()) -> None:
        scheduled: dict[FailurePoint, frozenset[int]] = {}
        for directive in directives:
            if any(item < 1 for item in directive.occurrences):
                raise ValueError("failure occurrence must be one-based")
            if directive.point in scheduled:
                raise ValueError(f"duplicate failure directive: {directive.point.value}")
            scheduled[directive.point] = frozenset(directive.occurrences)
        self._scheduled = scheduled
        self._counts: dict[FailurePoint, int] = {}
        self._lock = Lock()

    def should_fail(self, point: FailurePoint) -> bool:
        with self._lock:
            occurrence = self._counts.get(point, 0) + 1
            self._counts[point] = occurrence
            return occurrence in self._scheduled.get(point, frozenset())

    def raise_if_scheduled(self, point: FailurePoint) -> None:
        if self.should_fail(point):
            raise InjectedFailure(point)

    def calls(self, point: FailurePoint) -> int:
        with self._lock:
            return self._counts.get(point, 0)

