"""Workspace-scoped append-only persistence for Completion facts."""

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.completion.assessment import assess_requirement, effective_assessments
from enterprise_agent.completion.delivery import build_delivery_decision
from enterprise_agent.contracts.domain import (
    AnswerContract,
    Claim,
    ContractVersion,
    DeliveryDecision,
    Evidence,
    ExecutionSnapshot,
    FailureCode,
    Gap,
    Plan,
    Requirement,
    RequirementAssessment,
    RequirementRelationType,
    RequirementSupport,
    RequirementType,
    ReviewResult,
    VerificationGates,
    VerificationMetrics,
    VerificationResult,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.steps import plan_step_id
from enterprise_agent.storage.tables import (
    ApprovalRow,
    AttemptRow,
    CapabilityCallRow,
    ClaimRow,
    DeliveryDecisionRow,
    EvidenceRow,
    ExecutionSnapshotRow,
    GapRow,
    OperationRow,
    PlanRow,
    RequirementAssessmentRow,
    RequirementRow,
    RequirementSupportRow,
    StepRow,
    TaskContractVersionRow,
    TaskRow,
    VerificationResultRow,
)


def _payload(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


async def _task(session: AsyncSession, workspace_id: UUID, task_id: UUID) -> TaskRow:
    row = await session.scalar(
        select(TaskRow).where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "completion task unavailable")
    return row


async def _assert_fencing(
    session: AsyncSession,
    workspace_id: UUID,
    attempt_id: UUID | None,
    fencing_token: int | None,
) -> None:
    if attempt_id is None and fencing_token is None:
        return
    if attempt_id is None or fencing_token is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "completion fencing identity is incomplete")
    attempt = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id,
            AttemptRow.id == attempt_id,
        )
    )
    if attempt is None or attempt.fencing_token != fencing_token or attempt.state != "RUNNING":
        raise DomainError(FailureCode.STALE_ATTEMPT, "completion fencing rejected")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None or attempt.lease_expires_at <= now:
        raise DomainError(FailureCode.STALE_ATTEMPT, "completion attempt lease expired")


async def create_contract_version(
    session: AsyncSession,
    contract: ContractVersion,
) -> ContractVersion:
    await _task(session, contract.workspace_id, contract.task_id)
    existing = await session.scalar(
        select(TaskContractVersionRow).where(
            TaskContractVersionRow.workspace_id == contract.workspace_id,
            TaskContractVersionRow.id == contract.id,
        )
    )
    payload = _payload(contract.model_dump(mode="json"))
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "contract version is immutable")
        return ContractVersion.model_validate(existing.payload)
    superseded: TaskContractVersionRow | None = None
    if contract.supersedes_id is not None:
        superseded = await session.scalar(
            select(TaskContractVersionRow)
            .where(
                TaskContractVersionRow.workspace_id == contract.workspace_id,
                TaskContractVersionRow.id == contract.supersedes_id,
            )
            .with_for_update()
        )
        if (
            superseded is None
            or superseded.task_id != contract.task_id
            or not superseded.is_current
            or superseded.version >= contract.version
        ):
            raise DomainError(FailureCode.VALIDATION_FAILED, "invalid contract supersession")
    elif contract.version != 1:
        raise DomainError(FailureCode.VALIDATION_FAILED, "new contract versions must supersede")
    current = await session.scalar(
        select(TaskContractVersionRow)
        .where(
            TaskContractVersionRow.workspace_id == contract.workspace_id,
            TaskContractVersionRow.task_id == contract.task_id,
            TaskContractVersionRow.is_current.is_(True),
        )
        .with_for_update()
    )
    if current is not None and superseded is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "task already has a current contract")
    if superseded is not None:
        superseded.is_current = False
    session.add(
        TaskContractVersionRow(
            id=contract.id,
            workspace_id=contract.workspace_id,
            task_id=contract.task_id,
            version=contract.version,
            supersedes_id=contract.supersedes_id,
            content_hash=contract.content_hash,
            payload=payload,
            created_by=contract.created_by,
            created_at=contract.created_at,
            is_current=True,
        )
    )
    await session.flush()
    return contract


async def get_current_contract_version(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> ContractVersion | None:
    row = await session.scalar(
        select(TaskContractVersionRow)
        .where(
            TaskContractVersionRow.workspace_id == workspace_id,
            TaskContractVersionRow.task_id == task_id,
            TaskContractVersionRow.is_current.is_(True),
        )
        .order_by(TaskContractVersionRow.version.desc())
    )
    return None if row is None else ContractVersion.model_validate(row.payload)


def requirements_for_contract(
    contract: ContractVersion,
    *,
    legacy_descriptions: tuple[str, ...] = (),
    entity_scope: tuple[str, ...] = (),
    temporal_scope: tuple[str, ...] = (),
) -> tuple[Requirement, ...]:
    """Expand an AnswerContract into immutable, auditable requirements."""
    raw_contract = contract.completion_policy.get("answer_contract")
    answer_contract = (
        AnswerContract.model_validate(raw_contract)
        if isinstance(raw_contract, dict)
        else None
    )
    if answer_contract is None:
        return tuple(
            Requirement(
                id=uuid5(contract.id, f"legacy:{index}:{description}"),
                workspace_id=contract.workspace_id,
                task_id=contract.task_id,
                contract_version_id=contract.id,
                key=f"legacy:{index}",
                type=RequirementType.ANALYSIS,
                mandatory=True,
                description=description,
                completion_rule="legacy_verified_claims_with_provenance",
                entity_scope=entity_scope,
                temporal_scope=temporal_scope,
                evidence_policy={"minimum": 1, "require_provenance": True, "legacy": True},
                delivery_policy={"allow_partial": True, "legacy": True},
                legacy=True,
            )
            for index, description in enumerate(legacy_descriptions)
        )
    mandatory = answer_contract.completeness == "COMPLETE"
    freshness_policy = {"policy": answer_contract.freshness}
    evidence_policy = {
        "minimum": answer_contract.minimum_evidence_items,
        "minimum_independent_sources": answer_contract.minimum_independent_sources,
        "require_provenance": True,
    }
    delivery_policy = {
        "allow_partial": answer_contract.partial_answer_policy == "ALLOW_SUPPORTED_ONLY",
        "minimum_answer_items": answer_contract.minimum_answer_items,
        "maximum_answer_items": answer_contract.maximum_answer_items,
    }
    requirement_type = (
        RequirementType.VERIFICATION
        if answer_contract.answer_type == "DOCUMENT_QA"
        else RequirementType.READ
        if answer_contract.answer_type in {"FACT", "LIST", "TIMELINE", "FORECAST"}
        else RequirementType.ANALYSIS
    )
    return tuple(
        Requirement(
            id=uuid5(contract.id, f"aspect:{index}:{aspect}"),
            workspace_id=contract.workspace_id,
            task_id=contract.task_id,
            contract_version_id=contract.id,
            key=f"aspect:{index}",
            type=requirement_type,
            mandatory=mandatory,
            description=aspect,
            completion_rule="verified_claims_for_required_aspect",
            entity_scope=entity_scope,
            temporal_scope=temporal_scope
            + (("EXPLICIT_DATE",) if answer_contract.require_explicit_dates else ()),
            freshness_policy=cast(dict[str, JsonValue], freshness_policy),
            evidence_policy=cast(dict[str, JsonValue], evidence_policy),
            delivery_policy=cast(dict[str, JsonValue], delivery_policy),
        )
        for index, aspect in enumerate(answer_contract.required_aspects)
    )


async def create_intended_requirement_supports(
    session: AsyncSession,
    plan: Plan,
) -> tuple[RequirementSupport, ...]:
    """Persist the deterministic Requirement -> Plan/Step intent graph."""
    contract = await get_current_contract_version(session, plan.workspace_id, plan.task_id)
    if contract is None:
        return ()
    requirements = await list_requirements(
        session, plan.workspace_id, plan.task_id, contract_version_id=contract.id
    )
    created_at = await session.scalar(select(func.clock_timestamp()))
    if created_at is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    supports: list[RequirementSupport] = []
    for requirement in requirements:
        for step in plan.steps:
            step_id = plan_step_id(plan.plan_id, step.step_key)
            support = RequirementSupport(
                id=uuid5(
                    requirement.id,
                    f"INTENDED_TO_SATISFY:{plan.plan_id}:{step_id}",
                ),
                workspace_id=plan.workspace_id,
                task_id=plan.task_id,
                requirement_id=requirement.id,
                plan_revision_id=plan.plan_id,
                step_id=step_id,
                relation_type=RequirementRelationType.INTENDED_TO_SATISFY,
                created_at=created_at,
            )
            supports.append(await create_requirement_support(session, support))
    return tuple(supports)


async def create_requirement(
    session: AsyncSession,
    requirement: Requirement,
) -> Requirement:
    await _task(session, requirement.workspace_id, requirement.task_id)
    contract = await session.scalar(
        select(TaskContractVersionRow).where(
            TaskContractVersionRow.workspace_id == requirement.workspace_id,
            TaskContractVersionRow.id == requirement.contract_version_id,
            TaskContractVersionRow.task_id == requirement.task_id,
        )
    )
    if contract is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "requirement contract unavailable")
    existing = await session.scalar(
        select(RequirementRow).where(
            RequirementRow.workspace_id == requirement.workspace_id,
            RequirementRow.id == requirement.id,
        )
    )
    payload = _payload(requirement.model_dump(mode="json"))
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "requirement is immutable")
        return Requirement.model_validate(existing.payload)
    session.add(
        RequirementRow(
            id=requirement.id,
            workspace_id=requirement.workspace_id,
            task_id=requirement.task_id,
            contract_version_id=requirement.contract_version_id,
            requirement_key=requirement.key,
            payload=payload,
        )
    )
    await session.flush()
    return requirement


async def list_requirements(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    contract_version_id: UUID | None = None,
) -> tuple[Requirement, ...]:
    statement = select(RequirementRow).where(
        RequirementRow.workspace_id == workspace_id,
        RequirementRow.task_id == task_id,
    )
    if contract_version_id is not None:
        statement = statement.where(RequirementRow.contract_version_id == contract_version_id)
    rows = (
        await session.scalars(statement.order_by(RequirementRow.created_at, RequirementRow.id))
    ).all()
    return tuple(Requirement.model_validate(row.payload) for row in rows)


async def create_requirement_support(
    session: AsyncSession,
    support: RequirementSupport,
) -> RequirementSupport:
    requirement = await session.scalar(
        select(RequirementRow).where(
            RequirementRow.workspace_id == support.workspace_id,
            RequirementRow.id == support.requirement_id,
            RequirementRow.task_id == support.task_id,
        )
    )
    if requirement is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "support requirement unavailable")
    await _task(session, support.workspace_id, support.task_id)
    step = await session.scalar(
        select(StepRow).where(
            StepRow.workspace_id == support.workspace_id,
            StepRow.id == support.step_id,
            StepRow.task_id == support.task_id,
        )
    )
    plan = await session.scalar(
        select(PlanRow).where(
            PlanRow.workspace_id == support.workspace_id,
            PlanRow.id == support.plan_revision_id,
            PlanRow.task_id == support.task_id,
        )
    )
    if step is None or plan is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "support execution scope is invalid")
    if step.plan_id != support.plan_revision_id:
        raise DomainError(FailureCode.VALIDATION_FAILED, "support step belongs to another plan")
    payload = _payload(support.model_dump(mode="json"))
    existing = await session.scalar(
        select(RequirementSupportRow).where(
            RequirementSupportRow.workspace_id == support.workspace_id,
            RequirementSupportRow.id == support.id,
        )
    )
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "requirement support is immutable")
        return RequirementSupport.model_validate(existing.payload)
    session.add(
        RequirementSupportRow(
            id=support.id,
            workspace_id=support.workspace_id,
            task_id=support.task_id,
            requirement_id=support.requirement_id,
            plan_revision_id=support.plan_revision_id,
            step_id=support.step_id,
            relation_type=support.relation_type.value,
            payload=payload,
            created_at=support.created_at,
        )
    )
    await session.flush()
    return support


async def create_verification_result(
    session: AsyncSession,
    verification: VerificationResult,
) -> VerificationResult:
    await _task(session, verification.workspace_id, verification.task_id)
    await _assert_fencing(
        session, verification.workspace_id, verification.attempt_id, verification.fencing_token
    )
    existing = await session.scalar(
        select(VerificationResultRow).where(
            VerificationResultRow.workspace_id == verification.workspace_id,
            VerificationResultRow.id == verification.id,
        )
    )
    payload = _payload(verification.model_dump(mode="json"))
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "verification result is immutable")
        return VerificationResult.model_validate(existing.payload)
    claim_rows = (
        await session.scalars(
            select(ClaimRow).where(
                ClaimRow.workspace_id == verification.workspace_id,
                ClaimRow.id.in_(verification.claim_ids),
            )
        )
    ).all()
    if {row.id for row in claim_rows} != set(verification.claim_ids) or any(
        row.task_id != verification.task_id for row in claim_rows
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "verification Claim scope is invalid")
    verified_claims = tuple(Claim.model_validate(row.payload) for row in claim_rows)
    if any(
        not set(claim.evidence_ids) <= set(verification.evidence_ids)
        for claim in verified_claims
    ):
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "verification does not include all Claim Evidence",
        )
    evidence_rows = (
        await session.scalars(
            select(EvidenceRow)
            .join(
                StepRow,
                (StepRow.workspace_id == EvidenceRow.workspace_id)
                & (StepRow.id == EvidenceRow.step_id),
            )
            .where(
                EvidenceRow.workspace_id == verification.workspace_id,
                EvidenceRow.id.in_(verification.evidence_ids),
                StepRow.task_id == verification.task_id,
            )
        )
    ).all()
    if {row.id for row in evidence_rows} != set(verification.evidence_ids):
        raise DomainError(FailureCode.VALIDATION_FAILED, "verification Evidence scope is invalid")
    session.add(
        VerificationResultRow(
            id=verification.id,
            workspace_id=verification.workspace_id,
            task_id=verification.task_id,
            attempt_id=verification.attempt_id,
            fencing_token=verification.fencing_token,
            payload=payload,
            created_at=verification.created_at,
        )
    )
    await session.flush()
    return verification


async def list_verification_results(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[VerificationResult, ...]:
    rows = (
        await session.scalars(
            select(VerificationResultRow)
            .where(
                VerificationResultRow.workspace_id == workspace_id,
                VerificationResultRow.task_id == task_id,
            )
            .order_by(VerificationResultRow.created_at, VerificationResultRow.id)
        )
    ).all()
    return tuple(VerificationResult.model_validate(row.payload) for row in rows)


async def create_requirement_assessment(
    session: AsyncSession,
    assessment: RequirementAssessment,
) -> RequirementAssessment:
    await _task(session, assessment.workspace_id, assessment.task_id)
    await _assert_fencing(
        session, assessment.workspace_id, assessment.attempt_id, assessment.fencing_token
    )
    requirement = await session.scalar(
        select(RequirementRow).where(
            RequirementRow.workspace_id == assessment.workspace_id,
            RequirementRow.id == assessment.requirement_id,
            RequirementRow.task_id == assessment.task_id,
        )
    )
    if requirement is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "assessment requirement unavailable")
    verification = None
    if assessment.verification_ref is not None:
        verification = await session.scalar(
            select(VerificationResultRow).where(
                VerificationResultRow.workspace_id == assessment.workspace_id,
                VerificationResultRow.id == assessment.verification_ref,
                VerificationResultRow.task_id == assessment.task_id,
            )
        )
        if verification is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "assessment verification unavailable")
        facts = VerificationResult.model_validate(verification.payload)
        if not set(assessment.supporting_claim_ids) <= set(facts.claim_ids) or not set(
            assessment.supporting_evidence_ids
        ) <= set(facts.evidence_ids):
            raise DomainError(FailureCode.VALIDATION_FAILED, "assessment support exceeds verification")
    if assessment.supersedes_id is not None:
        previous = await session.scalar(
            select(RequirementAssessmentRow).where(
                RequirementAssessmentRow.workspace_id == assessment.workspace_id,
                RequirementAssessmentRow.id == assessment.supersedes_id,
                RequirementAssessmentRow.requirement_id == assessment.requirement_id,
            )
        )
        if previous is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "assessment supersession unavailable")
    existing = await session.scalar(
        select(RequirementAssessmentRow).where(
            RequirementAssessmentRow.workspace_id == assessment.workspace_id,
            RequirementAssessmentRow.id == assessment.id,
        )
    )
    payload = _payload(assessment.model_dump(mode="json"))
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "assessment is immutable")
        return RequirementAssessment.model_validate(existing.payload)
    session.add(
        RequirementAssessmentRow(
            id=assessment.id,
            workspace_id=assessment.workspace_id,
            task_id=assessment.task_id,
            requirement_id=assessment.requirement_id,
            verification_ref=assessment.verification_ref,
            supersedes_id=assessment.supersedes_id,
            attempt_id=assessment.attempt_id,
            fencing_token=assessment.fencing_token,
            payload=payload,
            assessed_at=assessment.assessed_at,
        )
    )
    await session.flush()
    if assessment.gap_type is not None:
        from enterprise_agent.completion.gaps import recovery_action_for_gap

        gap = Gap(
            id=uuid5(assessment.id, "gap"),
            workspace_id=assessment.workspace_id,
            task_id=assessment.task_id,
            requirement_id=assessment.requirement_id,
            gap_type=assessment.gap_type,
            recovery_action=recovery_action_for_gap(assessment.gap_type),
            description=(
                f"Requirement {assessment.requirement_id} has "
                f"{assessment.gap_type.value} gap"
            ),
            assessment_ref=assessment.id,
            created_at=assessment.assessed_at,
        )
        await create_gap(session, gap)
    return assessment


async def get_effective_assessments(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[RequirementAssessment, ...]:
    rows = (
        await session.scalars(
            select(RequirementAssessmentRow)
            .where(
                RequirementAssessmentRow.workspace_id == workspace_id,
                RequirementAssessmentRow.task_id == task_id,
            )
            .order_by(RequirementAssessmentRow.assessed_at, RequirementAssessmentRow.id)
        )
    ).all()
    return effective_assessments(RequirementAssessment.model_validate(row.payload) for row in rows)


async def create_gap(session: AsyncSession, gap: Gap) -> Gap:
    await _task(session, gap.workspace_id, gap.task_id)
    payload = _payload(gap.model_dump(mode="json"))
    existing = await session.scalar(
        select(GapRow).where(GapRow.workspace_id == gap.workspace_id, GapRow.id == gap.id)
    )
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "gap is immutable")
        return Gap.model_validate(existing.payload)
    session.add(
        GapRow(
            id=gap.id,
            workspace_id=gap.workspace_id,
            task_id=gap.task_id,
            requirement_id=gap.requirement_id,
            assessment_ref=gap.assessment_ref,
            payload=payload,
            created_at=gap.created_at,
        )
    )
    await session.flush()
    return gap


async def create_delivery_decision(
    session: AsyncSession,
    decision: DeliveryDecision,
) -> DeliveryDecision:
    await _task(session, decision.workspace_id, decision.task_id)
    snapshot = await session.scalar(
        select(ExecutionSnapshotRow).where(
            ExecutionSnapshotRow.workspace_id == decision.workspace_id,
            ExecutionSnapshotRow.id == decision.completion_snapshot_id,
        )
    )
    if snapshot is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "delivery snapshot unavailable")
    if decision.supersedes_id is not None:
        previous = await session.scalar(
            select(DeliveryDecisionRow).where(
                DeliveryDecisionRow.workspace_id == decision.workspace_id,
                DeliveryDecisionRow.id == decision.supersedes_id,
                DeliveryDecisionRow.task_id == decision.task_id,
            )
        )
        if previous is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "delivery supersession unavailable")
    verification = None
    if decision.verification_ref is not None:
        verification = await session.scalar(
            select(VerificationResultRow).where(
                VerificationResultRow.workspace_id == decision.workspace_id,
                VerificationResultRow.id == decision.verification_ref,
                VerificationResultRow.task_id == decision.task_id,
            )
        )
        if verification is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "delivery verification unavailable")
        verified_claim_ids = set(
            VerificationResult.model_validate(verification.payload).claim_ids
        )
        if not set(decision.included_claim_ids) <= verified_claim_ids:
            raise DomainError(FailureCode.VALIDATION_FAILED, "delivery Claim scope is invalid")
        if not set(decision.excluded_claim_ids) <= verified_claim_ids:
            raise DomainError(FailureCode.VALIDATION_FAILED, "delivery exclusion scope is invalid")
    elif decision.included_claim_ids:
        raise DomainError(FailureCode.VALIDATION_FAILED, "delivery requires verification facts")
    existing = await session.scalar(
        select(DeliveryDecisionRow).where(
            DeliveryDecisionRow.workspace_id == decision.workspace_id,
            DeliveryDecisionRow.id == decision.id,
        )
    )
    payload = _payload(decision.model_dump(mode="json"))
    if existing is not None:
        if existing.payload != payload:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "delivery decision is immutable")
        return DeliveryDecision.model_validate(existing.payload)
    session.add(
        DeliveryDecisionRow(
            id=decision.id,
            workspace_id=decision.workspace_id,
            task_id=decision.task_id,
            completion_snapshot_id=decision.completion_snapshot_id,
            verification_ref=decision.verification_ref,
            supersedes_id=decision.supersedes_id,
            policy_version=decision.policy_version,
            payload=payload,
            created_at=decision.created_at,
        )
    )
    await session.flush()
    return decision


async def get_current_delivery_decision(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> DeliveryDecision | None:
    rows = (
        await session.scalars(
            select(DeliveryDecisionRow)
            .where(
                DeliveryDecisionRow.workspace_id == workspace_id,
                DeliveryDecisionRow.task_id == task_id,
            )
            .order_by(DeliveryDecisionRow.created_at, DeliveryDecisionRow.id)
        )
    ).all()
    decisions = [DeliveryDecision.model_validate(row.payload) for row in rows]
    superseded = {item.supersedes_id for item in decisions if item.supersedes_id is not None}
    current = [item for item in decisions if item.id not in superseded]
    return current[-1] if current else None


def _normalized_text(values: tuple[str, ...]) -> str:
    return " ".join(values).casefold()


def _scope_matches(scope: tuple[str, ...], texts: tuple[str, ...]) -> bool:
    if not scope:
        return True
    haystack = _normalized_text(texts)
    for item in scope:
        normalized = " ".join(item.split()).casefold()
        if normalized in {"current", "stable", "historical", "source_bound"}:
            continue
        if normalized == "explicit_date":
            if not re.search(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\b", haystack):
                return False
            continue
        if normalized in haystack:
            continue
        compact = normalized.replace(" ", "")
        if len(compact) > 2 and any(
            compact[index : index + 2] in haystack for index in range(len(compact) - 1)
        ):
            continue
        return False
    return True


def _temporal_matches(scope: tuple[str, ...], texts: tuple[str, ...]) -> bool:
    if not scope:
        return True
    haystack = _normalized_text(texts)
    for item in scope:
        normalized = item.casefold()
        if normalized == "stable":
            continue
        if normalized == "explicit_date":
            if not re.search(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\b", haystack):
                return False
            continue
        if normalized == "current":
            if not re.search(
                r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\b|"
                r"today|tomorrow|current|今天|明天|当前|本周|未来",
                haystack,
            ):
                return False
            continue
        if normalized == "historical":
            if not re.search(
                r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\b|"
                r"historical|history|last week|过去|上周|历史",
                haystack,
            ):
                return False
            continue
        if normalized not in haystack:
            return False
    return True


async def _execution_facts(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    task_id: UUID,
    review: ReviewResult,
    claim_count: int,
    evidence_count: int,
) -> tuple[bool, Literal["CERTAIN", "UNKNOWN", "CONFLICTING"]]:
    calls = (
        await session.scalars(
            select(CapabilityCallRow).where(
                CapabilityCallRow.workspace_id == workspace_id,
                CapabilityCallRow.task_id == task_id,
            )
        )
    ).all()
    authorization = True
    for call in calls:
        if call.policy_decision == "DENY" or call.state == "DENIED":
            authorization = False
        if call.policy_decision == "REQUIRE_APPROVAL":
            approved = await session.scalar(
                select(ApprovalRow.id).where(
                    ApprovalRow.workspace_id == workspace_id,
                    ApprovalRow.operation_id == call.operation_id,
                    ApprovalRow.state == "APPROVED",
                )
            )
            authorization = authorization and approved is not None
    operation_states = (
        await session.scalars(
            select(OperationRow.state)
            .join(
                StepRow,
                (StepRow.workspace_id == OperationRow.workspace_id)
                & (StepRow.id == OperationRow.step_id),
            )
            .where(
                OperationRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
            )
        )
    ).all()
    observed_states = {str(item.state) for item in calls} | set(operation_states)
    if "UNKNOWN" in observed_states:
        certainty: Literal["CERTAIN", "UNKNOWN", "CONFLICTING"] = "UNKNOWN"
    elif {"SUCCEEDED", "FAILED"} <= observed_states:
        certainty = "CONFLICTING"
    elif review.accepted and claim_count > 0 and evidence_count > 0:
        certainty = "CERTAIN"
    else:
        certainty = "CONFLICTING" if observed_states else "UNKNOWN"
    return authorization, certainty


async def _persist_evidence_supports(
    session: AsyncSession,
    *,
    requirements: tuple[Requirement, ...],
    evidence: tuple[Evidence, ...],
    relation_type: RequirementRelationType,
    created_at: datetime,
) -> None:
    for requirement in requirements:
        for item in evidence:
            step = await session.scalar(
                select(StepRow).where(
                    StepRow.workspace_id == item.workspace_id,
                    StepRow.id == item.step_id,
                    StepRow.task_id == item.task_id,
                )
            )
            if step is None or step.plan_id is None:
                continue
            support = RequirementSupport(
                id=uuid5(
                    requirement.id,
                    f"{relation_type}:{item.evidence_id}:{item.step_id}",
                ),
                workspace_id=requirement.workspace_id,
                task_id=requirement.task_id,
                requirement_id=requirement.id,
                plan_revision_id=step.plan_id,
                step_id=step.id,
                relation_type=relation_type,
                created_at=created_at,
            )
            await create_requirement_support(session, support)


async def materialize_completion_after_review(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    task_id: UUID,
    snapshot_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    fencing_token: int,
    review: ReviewResult,
    claims: tuple[Claim, ...],
    evidence: tuple[Evidence, ...],
    created_at: datetime,
) -> DeliveryDecision | None:
    """Bridge the existing Reviewer boundary into fenced Completion facts."""
    contract = await get_current_contract_version(session, workspace_id, task_id)
    if contract is None:
        return None
    requirements = await list_requirements(
        session, workspace_id, task_id, contract_version_id=contract.id
    )
    if not requirements:
        return None
    await _assert_fencing(session, workspace_id, attempt_id, fencing_token)
    snapshot: ExecutionSnapshot = await load_snapshot(session, workspace_id, snapshot_id)
    claim_ids = tuple(item.claim_id for item in claims)
    evidence_ids = tuple(item.evidence_id for item in evidence)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    claims_grounded = all(
        set(item.evidence_ids) <= set(evidence_by_id)
        for item in claims
    )
    input_payload = {
        "snapshot_id": str(snapshot.snapshot_id),
        "review": review.model_dump(mode="json"),
        "claim_ids": [str(item) for item in claim_ids],
        "evidence_hashes": sorted(item.content_hash for item in evidence),
    }
    input_snapshot_hash = hashlib.sha256(
        json.dumps(input_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    texts = tuple(item.content for item in evidence) + tuple(item.content for item in claims)
    entity_match = all(
        requirement.legacy or _scope_matches(requirement.entity_scope, texts)
        for requirement in requirements
    )
    temporal_match = all(
        requirement.legacy or _temporal_matches(requirement.temporal_scope, texts)
        for requirement in requirements
    )
    matched_requirements = sum(
        1
        for requirement in requirements
        if requirement.legacy
        or (
            _scope_matches((requirement.description,), texts)
            and _scope_matches(requirement.entity_scope, texts)
            and _temporal_matches(requirement.temporal_scope, texts)
        )
    )
    completeness = matched_requirements / len(requirements) if requirements else 0.0
    source_weights = {"MCP": 1.0, "DOCUMENT": 0.9, "WEB": 0.8, "DERIVED": 0.5}
    source_quality = (
        sum(source_weights.get(item.source_type, 0.0) for item in evidence) / len(evidence)
        if evidence
        else 0.0
    )
    independent_sources = {item.source_id for item in evidence}
    required_sources = max(
        (
            (int(value) if isinstance((value := requirement.evidence_policy.get("minimum_independent_sources", 1)), (int, float, str)) else 1)
            for requirement in requirements
        ),
        default=1,
    )
    redundancy = min(1.0, len(independent_sources) / required_sources)
    authorization, operation_certainty = await _execution_facts(
        session,
        workspace_id=workspace_id,
        task_id=task_id,
        review=review,
        claim_count=len(claims),
        evidence_count=len(evidence),
    )
    relevance = sum(item.relevance for item in evidence) / len(evidence) if evidence else 0.0
    verification = VerificationResult(
        id=uuid5(NAMESPACE_URL, f"completion-verification:{operation_id}"),
        workspace_id=workspace_id,
        task_id=task_id,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        gates=VerificationGates(
            entity_match=entity_match,
            temporal_match=temporal_match,
            evidence_support=review.accepted and claims_grounded and bool(claims),
            authorization=authorization,
            operation_certainty=operation_certainty,
            schema_validity=review.accepted
            and not review.constraint_violations
            and not review.citation_errors,
        ),
        metrics=VerificationMetrics(
            completeness=completeness if review.accepted else 0.0,
            freshness=1.0 if evidence and temporal_match else 0.0,
            source_quality=source_quality,
            relevance=relevance,
            redundancy=redundancy,
        ),
        verifier_version="reviewer-bridge.v1",
        policy_version=str(contract.completion_policy.get("version", "1")),
        input_snapshot_hash=input_snapshot_hash,
        created_at=created_at,
        attempt_id=attempt_id,
        fencing_token=fencing_token,
    )
    await create_verification_result(session, verification)
    await _persist_evidence_supports(
        session,
        requirements=requirements,
        evidence=evidence,
        relation_type=RequirementRelationType.PRODUCED_EVIDENCE_FOR,
        created_at=created_at,
    )
    previous = {
        item.requirement_id: item
        for item in await get_effective_assessments(session, workspace_id, task_id)
    }
    assessments: list[RequirementAssessment] = []
    for requirement in requirements:
        prior = previous.get(requirement.id)
        assessment = assess_requirement(
            requirement,
            verification,
            assessment_id=uuid5(verification.id, str(requirement.id)),
            evaluator_version="completion-policy.v1",
            supersedes_id=None if prior is None else prior.id,
        )
        await create_requirement_assessment(session, assessment)
        await _persist_evidence_supports(
            session,
            requirements=(requirement,),
            evidence=evidence,
            relation_type=RequirementRelationType.VERIFIED_AGAINST,
            created_at=created_at,
        )
        assessments.append(assessment)
    previous_delivery = await get_current_delivery_decision(session, workspace_id, task_id)
    decision = build_delivery_decision(
        contract,
        requirements,
        (*previous.values(), *assessments),
        (verification,),
        snapshot,
        decision_id=uuid5(NAMESPACE_URL, f"completion-delivery:{operation_id}"),
        created_at=created_at,
        supersedes_id=None if previous_delivery is None else previous_delivery.id,
    )
    return await create_delivery_decision(session, decision)
