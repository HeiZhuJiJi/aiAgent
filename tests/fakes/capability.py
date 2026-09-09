from dataclasses import dataclass, field

from pydantic import JsonValue

from enterprise_agent.contracts.domain import CapabilityRequest, CapabilityResult, Failure


@dataclass
class RecordingCapability:
    output: JsonValue
    outcome: str = "SUCCEEDED"
    failure: Failure | None = None
    calls: list[CapabilityRequest] = field(default_factory=list)

    async def __call__(self, request: CapabilityRequest) -> CapabilityResult:
        self.calls.append(request)
        return CapabilityResult(
            operation_id=request.operation_id,
            outcome=self.outcome,
            output=self.output,
            failure=self.failure,
        )
