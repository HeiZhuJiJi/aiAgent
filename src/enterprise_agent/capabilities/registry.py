from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    FailureCode,
)
from enterprise_agent.domain.states import DomainError

CapabilityHandler = Callable[[CapabilityRequest], Awaitable[CapabilityResult]]


@dataclass(frozen=True)
class RegisteredCapability:
    descriptor: CapabilityDescriptor
    handler: CapabilityHandler


class CapabilityRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredCapability] = {}

    def register(self, descriptor: CapabilityDescriptor, handler: CapabilityHandler) -> None:
        key = (descriptor.id, descriptor.version)
        if key in self._entries:
            raise DomainError(FailureCode.VALIDATION_FAILED, "capability version already registered")
        self._entries[key] = RegisteredCapability(descriptor=descriptor, handler=handler)

    def resolve(self, capability_id: str, capability_version: str) -> RegisteredCapability:
        entry = self._entries.get((capability_id, capability_version))
        if entry is None:
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "capability version unavailable")
        return entry
