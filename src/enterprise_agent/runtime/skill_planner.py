import hashlib
import json
from collections.abc import Sequence
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from jsonschema import Draft202012Validator
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    FailureCode,
    Plan,
    TaskGoal,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.skills.package import SkillPackage


def resolve_skill_capabilities(
    package: SkillPackage,
    capabilities: Sequence[CapabilityDescriptor],
) -> tuple[CapabilityDescriptor, ...]:
    active = [item for item in capabilities if item.health_status == "ACTIVE"]
    resolved: list[CapabilityDescriptor] = []
    for dependency in package.manifest.dependencies:
        matches = [
            item
            for item in active
            if item.id == dependency.capability_id
            and Version(item.version) in SpecifierSet(dependency.version_range)
        ]
        if len(matches) != 1:
            raise DomainError(
                FailureCode.CAPABILITY_UNAVAILABLE,
                f"Skill capability resolution failed: {dependency.capability_id}",
            )
        resolved.append(matches[0])
    return tuple(resolved)


def skill_version_ref(package: SkillPackage) -> VersionRef:
    return VersionRef(
        identifier=package.manifest.name,
        version=package.manifest.version,
        sha256=package.sha256,
    )


def capability_version_ref(capability: CapabilityDescriptor) -> VersionRef:
    payload = json.dumps(
        capability.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return VersionRef(
        identifier=capability.id,
        version=capability.version,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def build_skill_plan(
    *,
    task_id: UUID,
    execution_snapshot_id: UUID,
    goal: TaskGoal,
    package: SkillPackage,
) -> Plan:
    validator = Draft202012Validator(cast(dict[str, Any], package.input_schema))
    errors = sorted(
        validator.iter_errors(goal.model_dump(mode="json")),
        key=lambda item: list(item.path),
    )
    if errors:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "Skill input does not satisfy its schema",
        )
    return Plan(
        plan_id=uuid5(
            NAMESPACE_URL,
            f"skill-plan:{goal.workspace_id}:{task_id}:{package.sha256}:1",
        ),
        task_id=task_id,
        workspace_id=goal.workspace_id,
        execution_snapshot_id=execution_snapshot_id,
        version=1,
        goal=goal.goal,
        constraints=goal.constraints,
        deliverables=goal.deliverables,
        steps=package.workflow.steps,
    )
