import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import Field, JsonValue, ValidationError

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    Contract,
    FailureCode,
    PlanStepSpec,
)
from enterprise_agent.domain.states import DomainError


class SkillDependency(Contract):
    capability_id: str
    version_range: str


class SkillManifest(Contract):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str
    runtime_contract: Literal[1]
    dependencies: tuple[SkillDependency, ...]


class SkillWorkflow(Contract):
    steps: tuple[PlanStepSpec, ...] = Field(min_length=1)


class SkillPermissions(Contract):
    permissions: frozenset[str]


class SkillPackage(Contract):
    manifest: SkillManifest
    workflow: SkillWorkflow
    permissions: SkillPermissions
    instructions: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    evaluation_cases: tuple[dict[str, JsonValue], ...]
    sha256: str


REQUIRED = frozenset(
    {
        "skill.yaml",
        "instructions.md",
        "workflow.yaml",
        "schemas/input.schema.json",
        "schemas/output.schema.json",
        "policies/permissions.yaml",
        "eval/cases.json",
    }
)


def validate_package(blob: bytes, capabilities: tuple[CapabilityDescriptor, ...]) -> SkillPackage:
    if len(blob) > 10 * 1024 * 1024:
        raise DomainError(FailureCode.VALIDATION_FAILED, "skill archive exceeds size limit")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            total = 0
            for member in archive.infolist():
                name = member.filename
                path = PurePosixPath(name)
                if (
                    "\\" in name
                    or ":" in name
                    or path.is_absolute()
                    or ".." in path.parts
                    or str(path) != name.rstrip("/")
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise ValueError("unsafe archive path")
                if member.is_dir():
                    continue
                if name not in REQUIRED or name in files:
                    raise ValueError("unexpected or duplicate archive member")
                total += member.file_size
                if (
                    total > 20 * 1024 * 1024
                    or member.file_size > max(member.compress_size, 1) * 100
                ):
                    raise ValueError("archive expansion limit")
                files[name] = archive.read(member)
            if files.keys() != REQUIRED:
                raise ValueError("required files missing")
        manifest = SkillManifest.model_validate(yaml.safe_load(files["skill.yaml"]))
        workflow = SkillWorkflow.model_validate(yaml.safe_load(files["workflow.yaml"]))
        permissions = SkillPermissions.model_validate(
            yaml.safe_load(files["policies/permissions.yaml"])
        )
        input_schema = json.loads(files["schemas/input.schema.json"])
        output_schema = json.loads(files["schemas/output.schema.json"])
        for schema in (input_schema, output_schema):
            Draft202012Validator.check_schema(schema)
            if re.search(r'"\$(?:ref|dynamicRef)"\s*:\s*"(?!#)', json.dumps(schema)):
                raise ValueError("remote schema references prohibited")
        registry = {c.id: c for c in capabilities if c.health_status == "ACTIVE"}
        deps = {d.capability_id: d for d in manifest.dependencies}
        if len(deps) != len(manifest.dependencies):
            raise ValueError("duplicate capability dependency")
        for dependency in manifest.dependencies:
            cap = registry.get(dependency.capability_id)
            if cap is None or Version(cap.version) not in SpecifierSet(dependency.version_range):
                raise ValueError("capability dependency unsatisfied")
            if not cap.required_permissions <= permissions.permissions:
                raise ValueError("capability permissions omitted from manifest")
        keys = {s.step_key for s in workflow.steps}
        if len(keys) != len(workflow.steps):
            raise ValueError("duplicate workflow step")
        done: set[str] = set()
        for step in workflow.steps:
            if not set(step.dependencies) <= keys:
                raise ValueError("unknown workflow dependency")
            for requirement in step.capability_requirements:
                if requirement.capability_id not in deps:
                    raise ValueError("workflow capability absent from dependencies")
                if Version(registry[requirement.capability_id].version) not in SpecifierSet(
                    requirement.version_range
                ):
                    raise ValueError("workflow capability version incompatible")
        while len(done) < len(keys):
            ready = {
                s.step_key
                for s in workflow.steps
                if s.step_key not in done and set(s.dependencies) <= done
            }
            if not ready:
                raise ValueError("cyclic workflow")
            done |= ready
        return SkillPackage(
            manifest=manifest,
            workflow=workflow,
            permissions=permissions,
            instructions=files["instructions.md"].decode("utf-8"),
            input_schema=input_schema,
            output_schema=output_schema,
            evaluation_cases=json.loads(files["eval/cases.json"]),
            sha256=hashlib.sha256(blob).hexdigest(),
        )
    except (
        ValueError,
        KeyError,
        TypeError,
        ValidationError,
        zipfile.BadZipFile,
        yaml.YAMLError,
        InvalidSpecifier,
        SchemaError,
    ) as exc:
        raise DomainError(
            FailureCode.VALIDATION_FAILED, "skill package validation failed: " + type(exc).__name__
        ) from None
