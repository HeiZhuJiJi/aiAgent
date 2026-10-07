import json
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    Claim,
    ConversationMessage,
    ConversationTurnAttemptState,
    ConversationTurnOutcomeStatus,
    ConversationTurnState,
    DeliveryDecisionType,
    Evidence,
    Failure,
    FailureCode,
    StructuredAnalysisResult,
    TaskState,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.attempts import list_task_attempts, list_task_evidence
from enterprise_agent.storage.completion import get_current_delivery_decision
from enterprise_agent.storage.memory import append_conversation_message
from enterprise_agent.storage.provenance import load_claims
from enterprise_agent.storage.tables import (
    AuditRow,
    ConversationMessageRow,
    ConversationTurnAttemptRow,
    ConversationTurnOutcomeRow,
    ConversationTurnRow,
    TaskRow,
)
from enterprise_agent.storage.turns import (
    acquire_conversation_turn_attempt,
    commit_conversation_turn_outcome,
    get_conversation_turn,
)

NO_RELIABLE_RESULTS_MISSING_INFORMATION = (
    "Current permitted Evidence does not support a reliable factual answer."
)


def is_no_reliable_results_payload(payload: object) -> bool:
    """Recognize the canonical, non-factual terminal result from grounded QA."""

    evidence_sufficient: object
    if isinstance(payload, StructuredAnalysisResult):
        evidence_sufficient = payload.evidence_sufficient
        missing_information: object = payload.missing_information
        claims: object = payload.claims
    elif isinstance(payload, dict):
        evidence_sufficient = payload.get("evidence_sufficient")
        missing_information = payload.get("missing_information")
        claims = payload.get("claims")
    else:
        return False
    return (
        evidence_sufficient is False
        and isinstance(missing_information, (list, tuple))
        and tuple(missing_information) == (NO_RELIABLE_RESULTS_MISSING_INFORMATION,)
        and isinstance(claims, (list, tuple))
        and not claims
    )


def _no_reliable_results_message(user_message: str) -> str:
    if any("\u3400" <= character <= "\u9fff" for character in user_message):
        return (
            "现有检索证据不足以可靠回答这个问题。本轮没有生成事实性结论；"
            "你可以缩小问题范围，或指定更明确的对象、地点和时间后重试。"
        )
    return (
        "The available evidence is insufficient to answer this question reliably. "
        "No factual conclusion was produced; narrow the request or specify a clearer "
        "subject, place, and time range before retrying."
    )


def conversation_turn_ids(
    workspace_id: UUID,
    conversation_id: UUID,
    user_message_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    turn_id = uuid5(
        NAMESPACE_URL,
        f"conversation-turn:{workspace_id}:{conversation_id}:{user_message_id}",
    )
    return (
        turn_id,
        uuid5(NAMESPACE_URL, f"conversation-turn-attempt:{turn_id}:1"),
        uuid5(NAMESPACE_URL, f"conversation-turn-outcome:{turn_id}"),
    )


async def reconcile_abandoned_direct_turns(
    session: AsyncSession,
    *,
    stale_after_seconds: int,
    workspace_id: UUID | None = None,
    limit: int = 100,
) -> int:
    """Fence and materialize stale direct-chat Turns after an API process loss."""

    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")
    database_now = await session.scalar(select(func.clock_timestamp()))
    assert database_now is not None
    cutoff = database_now - timedelta(seconds=stale_after_seconds)
    statement = select(ConversationTurnRow).where(
        ConversationTurnRow.task_id.is_(None),
        ConversationTurnRow.state == ConversationTurnState.RUNNING.value,
        ConversationTurnRow.active_attempt_id.is_not(None),
        ConversationTurnRow.updated_at <= cutoff,
    )
    if workspace_id is not None:
        statement = statement.where(ConversationTurnRow.workspace_id == workspace_id)
    stale_turns = (
        await session.scalars(
            statement.order_by(ConversationTurnRow.updated_at, ConversationTurnRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()

    recovered = 0
    for turn in stale_turns:
        attempt_id = turn.active_attempt_id
        assert attempt_id is not None
        attempt = await session.scalar(
            select(ConversationTurnAttemptRow)
            .where(
                ConversationTurnAttemptRow.workspace_id == turn.workspace_id,
                ConversationTurnAttemptRow.id == attempt_id,
            )
            .with_for_update()
        )
        if (
            attempt is None
            or attempt.turn_id != turn.id
            or attempt.fencing_token != turn.fencing_token
            or ConversationTurnAttemptState(attempt.state)
            is not ConversationTurnAttemptState.RUNNING
        ):
            continue

        trace_id = f"direct-turn-reconcile:{turn.id}"
        failure = Failure(
            code=FailureCode.LLM_TIMEOUT,
            safe_detail="direct chat attempt exceeded its recovery deadline",
            trace_id=trace_id,
        )
        assistant_message_id = uuid5(
            NAMESPACE_URL,
            f"direct-chat-message:{turn.workspace_id}:{turn.conversation_id}:{turn.id}",
        )
        await append_conversation_message(
            session,
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            message_id=assistant_message_id,
            role="ASSISTANT",
            content=turn_failure_message(failure.code),
        )
        _, _, outcome_id = conversation_turn_ids(
            turn.workspace_id,
            turn.conversation_id,
            turn.user_message_id,
        )
        await commit_conversation_turn_outcome(
            session,
            workspace_id=turn.workspace_id,
            turn_id=turn.id,
            attempt_id=attempt.id,
            fencing_token=attempt.fencing_token,
            outcome_id=outcome_id,
            status=ConversationTurnOutcomeStatus.UNAVAILABLE,
            assistant_message_id=assistant_message_id,
            failure=failure,
            context_eligible=False,
        )
        audit_id = uuid5(
            NAMESPACE_URL,
            f"direct-turn-reconciled:{turn.workspace_id}:{turn.id}",
        )
        await session.execute(
            insert(AuditRow)
            .values(
                id=audit_id,
                workspace_id=turn.workspace_id,
                actor_type="SYSTEM",
                actor_id=attempt.id,
                task_id=None,
                step_id=None,
                action="conversation.turn.reconciled",
                resource_type="CONVERSATION_TURN",
                resource_id=turn.id,
                capability_id=None,
                policy_decision=None,
                approval_id=None,
                result={
                    "reason": "DIRECT_TURN_RECOVERY_DEADLINE_EXCEEDED",
                    "outcome_status": ConversationTurnOutcomeStatus.UNAVAILABLE.value,
                    "assistant_message_id": str(assistant_message_id),
                },
                trace_id=trace_id,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        recovered += 1
    return recovered


async def reconcile_abandoned_planned_turns(
    session: AsyncSession,
    *,
    stale_after_seconds: int,
    workspace_id: UUID | None = None,
    limit: int = 100,
) -> int:
    """Materialize planner hand-offs that never reached an execution endpoint."""

    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")
    database_now = await session.scalar(select(func.clock_timestamp()))
    assert database_now is not None
    cutoff = database_now - timedelta(seconds=stale_after_seconds)
    statement = select(ConversationTurnRow).where(
        ConversationTurnRow.task_id.is_(None),
        ConversationTurnRow.state == ConversationTurnState.PENDING.value,
        ConversationTurnRow.active_attempt_id.is_(None),
        ConversationTurnRow.updated_at <= cutoff,
    )
    if workspace_id is not None:
        statement = statement.where(ConversationTurnRow.workspace_id == workspace_id)
    stale_turns = (
        await session.scalars(
            statement.order_by(ConversationTurnRow.updated_at, ConversationTurnRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()

    recovered = 0
    for turn in stale_turns:
        trace_id = f"planned-turn-reconcile:{turn.id}"
        attempt_id = uuid5(NAMESPACE_URL, f"planned-turn-reconcile-attempt:{turn.id}")
        attempt = await acquire_conversation_turn_attempt(
            session,
            workspace_id=turn.workspace_id,
            turn_id=turn.id,
            attempt_id=attempt_id,
            trace_id=trace_id,
        )
        failure = Failure(
            code=FailureCode.CAPABILITY_TIMEOUT,
            safe_detail="planned Turn was not handed off before its recovery deadline",
            trace_id=trace_id,
        )
        assistant_message_id = uuid5(
            NAMESPACE_URL,
            f"planned-turn-reconcile-message:{turn.workspace_id}:{turn.id}",
        )
        await append_conversation_message(
            session,
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            message_id=assistant_message_id,
            role="ASSISTANT",
            content=turn_failure_message(failure.code),
        )
        _, _, outcome_id = conversation_turn_ids(
            turn.workspace_id,
            turn.conversation_id,
            turn.user_message_id,
        )
        await commit_conversation_turn_outcome(
            session,
            workspace_id=turn.workspace_id,
            turn_id=turn.id,
            attempt_id=attempt.attempt_id,
            fencing_token=attempt.fencing_token,
            outcome_id=outcome_id,
            status=ConversationTurnOutcomeStatus.UNAVAILABLE,
            assistant_message_id=assistant_message_id,
            failure=failure,
            context_eligible=False,
        )
        await session.execute(
            insert(AuditRow)
            .values(
                id=uuid5(NAMESPACE_URL, f"planned-turn-reconciled:{turn.workspace_id}:{turn.id}"),
                workspace_id=turn.workspace_id,
                actor_type="SYSTEM",
                actor_id=attempt.attempt_id,
                task_id=None,
                step_id=None,
                action="conversation.turn.reconciled",
                resource_type="CONVERSATION_TURN",
                resource_id=turn.id,
                capability_id=None,
                policy_decision=None,
                approval_id=None,
                result={
                    "reason": "PLANNED_TURN_HANDOFF_DEADLINE_EXCEEDED",
                    "outcome_status": ConversationTurnOutcomeStatus.UNAVAILABLE.value,
                    "assistant_message_id": str(assistant_message_id),
                },
                trace_id=trace_id,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        recovered += 1
    return recovered


def turn_failure_status(code: FailureCode) -> ConversationTurnOutcomeStatus:
    if code in {
        FailureCode.LLM_RATE_LIMIT,
        FailureCode.LLM_TIMEOUT,
        FailureCode.LLM_SERVER_ERROR,
        FailureCode.CAPABILITY_TIMEOUT,
        FailureCode.CAPABILITY_UNAVAILABLE,
        FailureCode.DB_UNAVAILABLE,
        FailureCode.BUDGET_EXCEEDED,
    }:
        return ConversationTurnOutcomeStatus.UNAVAILABLE
    return ConversationTurnOutcomeStatus.FAILED


def turn_failure_message(code: FailureCode) -> str:
    if code in {
        FailureCode.LLM_RATE_LIMIT,
        FailureCode.LLM_TIMEOUT,
        FailureCode.LLM_SERVER_ERROR,
    }:
        return "回答模型暂时不可用，本轮已安全结束，请稍后重试。"
    if code in {
        FailureCode.CAPABILITY_TIMEOUT,
        FailureCode.CAPABILITY_UNAVAILABLE,
    }:
        return "所需信息服务暂时不可用，本轮已安全结束，请稍后重试。"
    if code is FailureCode.BUDGET_EXCEEDED:
        return "本轮已达到时间或用量上限并安全结束，你可以缩小问题范围后重试。"
    return "本轮回答未能安全完成，未完成内容不会影响后续对话。"


def _partial_review_message(assessment: object, evidence: tuple[Evidence, ...]) -> str | None:
    """Render only individually reviewed facts, never promote a failed task."""
    # Deferred import: the capability module already depends on result contracts.
    from enterprise_agent.capabilities.builtins import _restore_answer_draft

    if not isinstance(assessment, dict) or assessment.get("outcome") != "COMPLETED":
        return None
    if assessment.get("decision") not in {"REVISE", "REPLAN"} or assessment.get("language_matches") is not True:
        return None
    coverage = assessment.get("coverage", [])
    aligned_ids = {str(eid) for item in coverage if isinstance(item, dict)
                   and item.get("entity_alignment") == "MATCH"
                   and item.get("period_alignment") in {"MATCH", "PARTIAL"}
                   for eid in item.get("evidence_ids", [])} if isinstance(coverage, list) else set()
    if not aligned_ids:
        return None
    claims = _restore_answer_draft(assessment.get("supported_draft"), evidence)
    available = {item.evidence_id: item for item in evidence if item.source_type == "WEB"}
    lines = []
    for claim in claims:
        ids = tuple(dict.fromkeys(support.evidence_id for support in claim.supports))
        if not all(str(eid) in aligned_ids and eid in available and urlsplit(available[eid].locator).scheme in {"http", "https"} for eid in ids):
            continue
        links = " · ".join(f"[{urlsplit(available[eid].locator).netloc}]({available[eid].locator})" for eid in ids)
        lines.append("- " + claim.content + "（来源：" + links + "）")
    if not lines:
        return None
    raw_missing = assessment.get("missing_aspects", [])
    missing = tuple(
        dict.fromkeys(
            item.strip()[:240]
            for item in raw_missing
            if isinstance(item, str) and item.strip()
        )
    ) if isinstance(raw_missing, list) else ()
    missing_section = (
        "尚未核验的信息：\n" + "\n".join("- " + item for item in missing[:5])
        if missing
        else "尚有部分信息未完成核验。"
    )
    return (
        "已找到并核验部分信息，先提供目前可以确认的结果：\n\n"
        + "\n\n".join(lines)
        + "\n\n"
        + missing_section
        + "\n\n缺失内容不会被推断或补写；这些部分结果不会自动作为后续问题的已确认前提。"
    )


async def _supported_partial_reply(
    session: AsyncSession, workspace_id: UUID, task_id: UUID,
) -> str | None:
    # A missing final review does not erase prior explicitly supported facts;
    # a newer completed review does supersede the older one. Never union drafts.
    rows = (await session.scalars(select(AuditRow).where(
        AuditRow.workspace_id == workspace_id, AuditRow.task_id == task_id,
        AuditRow.action == "model.validator.assessed", AuditRow.actor_type == "AGENT",
    ).order_by(AuditRow.created_at.desc()).limit(8))).all()
    latest = next((row.result for row in rows if isinstance(row.result, dict)
                   and row.result.get("outcome") == "COMPLETED"), None)
    if latest is None:
        return None
    evidence = await list_task_evidence(session, workspace_id, task_id)
    return _partial_review_message(latest, evidence)


def _model_result_payload(
    evidence: tuple[Evidence, ...], task_id: UUID
) -> StructuredAnalysisResult:
    model_results = sorted(
        (item for item in evidence if item.source_id == "model.structured_output"),
        key=lambda item: (item.retrieved_at, item.evidence_id),
        reverse=True,
    )
    if not model_results:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "completed research task has no model result Evidence",
        )
    try:
        payload = StructuredAnalysisResult.model_validate_json(model_results[0].content)
    except ValueError:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "completed research task model Evidence is invalid",
        ) from None
    source_evidence_ids = {
        item.evidence_id
        for item in evidence
        if item.task_id == task_id and item.source_type in {"WEB", "DOCUMENT"}
    }
    cited_ids = {evidence_id for claim in payload.claims for evidence_id in claim.evidence_ids}
    if not cited_ids <= source_evidence_ids:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "completed research task cites unavailable source Evidence",
        )
    if not payload.missing_information and not payload.claims:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "completed research task factual answer has no grounded Claims",
        )
    return payload


def _document_citation(locator: str) -> str:
    try:
        value = json.loads(locator)
    except (ValueError, TypeError):
        return locator[:120]
    if not isinstance(value, dict):
        return locator[:120]
    name = value.get("document_name")
    label = str(name) if isinstance(name, str) and name.strip() else "知识库文档"
    source_locators = value.get("source_locators")
    locations = source_locators if isinstance(source_locators, list) else [value]
    pages = sorted(
        {
            item["page"]
            for item in locations
            if isinstance(item, dict) and isinstance(item.get("page"), int)
        }
    )
    lines = sorted(
        {
            item["line"]
            for item in locations
            if isinstance(item, dict) and isinstance(item.get("line"), int)
        }
    )
    if pages:
        return (
            f"{label} 第{pages[0]}页" if len(pages) == 1 else f"{label} 第{pages[0]}—{pages[-1]}页"
        )
    if lines:
        return (
            f"{label} 第{lines[0]}行" if len(lines) == 1 else f"{label} 第{lines[0]}—{lines[-1]}行"
        )
    return label


def render_research_task_result(evidence: tuple[Evidence, ...], task_id: UUID) -> str:
    payload = _model_result_payload(evidence, task_id)
    source_evidence = sorted(
        (
            item
            for item in evidence
            if item.task_id == task_id and item.source_type in {"WEB", "DOCUMENT"}
        ),
        key=lambda item: (item.retrieved_at, item.evidence_id),
    )
    sources_by_id = {str(item.evidence_id): item for item in source_evidence}
    cited_source_ids: list[str] = []
    for claim in payload.claims:
        for evidence_id in claim.evidence_ids:
            encoded_id = str(evidence_id)
            if encoded_id in sources_by_id and encoded_id not in cited_source_ids:
                cited_source_ids.append(encoded_id)

    missing_information = payload.missing_information
    summary = payload.summary.strip()
    if "The question requires additional context." in missing_information and not any(
        word in summary for word in ("补充", "具体", "重试", "换一个")
    ):
        summary += " 你可以补充更具体的对象、地点或时间范围后再试。"
    sections = [summary]
    has_factual_answer = not missing_information
    cited_sources = [sources_by_id[item] for item in cited_source_ids] if has_factual_answer else []
    cited_web_sources = [
        item
        for item in cited_sources
        if item.source_type == "WEB" and urlsplit(item.locator).scheme in {"http", "https"}
    ]
    cited_document_sources = [item for item in cited_sources if item.source_type == "DOCUMENT"]
    if cited_web_sources:
        source_lines: list[str] = []
        # A multi-part answer can legitimately need four or five distinct
        # first-party pages; do not hide citations for the later claims.
        for item in cited_web_sources[:5]:
            host = urlsplit(item.locator).netloc or "网页来源"
            source_lines.append(f"[{host}]({item.locator})")
        sections.append("参考来源：" + " · ".join(source_lines))
    if cited_document_sources:
        locators = list(
            dict.fromkeys(_document_citation(item.locator) for item in cited_document_sources)
        )
        sections.append("知识依据：" + "；".join(locators[:5]))
    return "\n\n".join(section for section in sections if section)


async def _existing_materialized_message(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn: ConversationTurnRow,
) -> ConversationMessage | None:
    outcome = await session.scalar(
        select(ConversationTurnOutcomeRow).where(
            ConversationTurnOutcomeRow.workspace_id == workspace_id,
            ConversationTurnOutcomeRow.turn_id == turn.id,
        )
    )
    if outcome is None:
        return None
    message = await session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.workspace_id == workspace_id,
            ConversationMessageRow.id == outcome.assistant_message_id,
        )
    )
    if (
        message is None
        or message.conversation_id != turn.conversation_id
        or message.role != "ASSISTANT"
    ):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "materialized Turn outcome message is unavailable",
        )
    return ConversationMessage(
        message_id=message.id,
        workspace_id=message.workspace_id,
        conversation_id=message.conversation_id,
        role="ASSISTANT",
        content=message.content,
        created_at=message.created_at,
    )



def _render_delivery_claims(
    claims: tuple[Claim, ...],
    evidence: tuple[Evidence, ...],
    included_claim_ids: tuple[UUID, ...],
) -> str:
    """Render only Claim content selected by the authoritative DeliveryDecision."""
    by_id = {item.claim_id: item for item in claims}
    included = tuple(included_claim_ids)
    if set(by_id) != set(included):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "delivery decision references unavailable persisted Claim",
        )
    sections = [by_id[claim_id].content.strip() for claim_id in included]
    if any(not item for item in sections):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "delivery decision references an empty persisted Claim",
        )
    source_by_id = {item.evidence_id: item for item in evidence}
    locators: list[str] = []
    for claim_id in included:
        claim = by_id[claim_id]
        for evidence_id in claim.evidence_ids:
            source = source_by_id.get(evidence_id)
            if source is None or source.source_type not in {"WEB", "DOCUMENT"}:
                continue
            if source.source_type == "WEB" and urlsplit(source.locator).scheme in {"http", "https"}:
                host = urlsplit(source.locator).netloc or "网页来源"
                line = f"[{host}]({source.locator})"
            else:
                line = source.locator[:120]
            if line not in locators:
                locators.append(line)
    if locators:
        sections.append("参考来源：" + " · ".join(locators[:3]))
    return "\n\n".join(sections)


async def materialize_chat_task_turn(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    task_id: UUID,
    trace_id: str,
) -> ConversationMessage | None:
    """Commit a research Task's chat message and Turn outcome in one idempotent transaction."""
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    turn_row = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.task_id == task_id,
        )
        .with_for_update()
    )
    if turn_row is None:
        return None
    existing = await _existing_materialized_message(
        session,
        workspace_id=workspace_id,
        turn=turn_row,
    )
    if existing is not None:
        return existing

    task_state = TaskState(task.state)
    if task_state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "Agent Task has not reached a terminal state",
        )
    if turn_row.active_attempt_id is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "chat research Turn has no active attempt",
        )

    failure: Failure | None = None
    if task_state is TaskState.COMPLETED:
        delivery = await get_current_delivery_decision(session, workspace_id, task_id)
        evidence = await list_task_evidence(session, workspace_id, task_id)
        if delivery is None:
            content = "当前任务没有可审计的交付决定，暂不返回事实性答案。"
            outcome_status = ConversationTurnOutcomeStatus.UNAVAILABLE
            context_eligible = False
            failure = Failure(
                code=FailureCode.CAPABILITY_UNAVAILABLE,
                safe_detail="completed task has no current DeliveryDecision",
                trace_id=trace_id,
            )
        elif delivery.decision in {
            DeliveryDecisionType.FULL_ANSWER,
            DeliveryDecisionType.PARTIAL_ANSWER,
        }:
            try:
                persisted_claims = await load_claims(session, workspace_id, task_id)
                selected = tuple(
                    item for item in persisted_claims if item.claim_id in set(delivery.included_claim_ids)
                )
                if {item.claim_id for item in selected} != set(delivery.included_claim_ids):
                    raise DomainError(
                        FailureCode.CONSISTENCY_VIOLATION,
                        "delivery decision references unavailable persisted Claim",
                    )
                content = _render_delivery_claims(
                    selected,
                    evidence,
                    delivery.included_claim_ids,
                )
                if delivery.decision is DeliveryDecisionType.PARTIAL_ANSWER and delivery.disclosure:
                    content = f"{content}\n\n{delivery.disclosure}"
                outcome_status = (
                    ConversationTurnOutcomeStatus.PARTIALLY_ANSWERED
                    if delivery.decision is DeliveryDecisionType.PARTIAL_ANSWER
                    else ConversationTurnOutcomeStatus.ANSWERED
                )
                context_eligible = delivery.decision is DeliveryDecisionType.FULL_ANSWER
            except DomainError as exc:
                failure = Failure(
                    code=exc.code,
                    safe_detail="persisted Delivery Claims could not be safely materialized",
                    trace_id=trace_id,
                )
                outcome_status = ConversationTurnOutcomeStatus.FAILED
                context_eligible = False
                content = turn_failure_message(exc.code)
        else:
            content = delivery.disclosure or {
                DeliveryDecisionType.CLARIFICATION_REQUIRED: "当前请求需要进一步澄清后才能回答。",
                DeliveryDecisionType.BLOCKED: "当前请求受到授权或策略限制，暂时无法交付。",
                DeliveryDecisionType.UNAVAILABLE: "当前没有满足交付策略的可验证事实。",
            }.get(delivery.decision, "当前结果暂时不可用。")
            outcome_status = (
                ConversationTurnOutcomeStatus.CLARIFICATION_REQUIRED
                if delivery.decision is DeliveryDecisionType.CLARIFICATION_REQUIRED
                else ConversationTurnOutcomeStatus.UNAVAILABLE
            )
            context_eligible = False
            failure = Failure(
                code=FailureCode.CAPABILITY_UNAVAILABLE,
                safe_detail=f"DeliveryDecision is {delivery.decision.value}",
                trace_id=trace_id,
            )
    else:
        attempts = await list_task_attempts(session, workspace_id, task_id)
        failure = next(
            (item.failure for item in reversed(attempts) if item.failure is not None),
            None,
        ) or Failure(
            code=FailureCode.CAPABILITY_UNAVAILABLE,
            safe_detail=(
                "research task was cancelled"
                if task_state is TaskState.CANCELLED
                else "research task failed before producing an answer"
            ),
            trace_id=trace_id,
        )
        outcome_status = turn_failure_status(failure.code)
        context_eligible = False
        content = turn_failure_message(failure.code)
        if (task_state is TaskState.FAILED
            and (task.goal_payload or {}).get("requested_skill") == "web-research"
            and failure.code in {FailureCode.VALIDATION_FAILED, FailureCode.BUDGET_EXCEEDED,
                                 FailureCode.LLM_TIMEOUT, FailureCode.LLM_SERVER_ERROR}):
            partial = await _supported_partial_reply(session, workspace_id, task_id)
            if partial is not None:
                content = partial
                # The durable Agent Task remains FAILED with its original Failure for
                # operations and audit. The user-facing Turn did successfully deliver
                # individually reviewed facts, so expose that narrower outcome while
                # keeping the partial result out of future context.
                outcome_status = ConversationTurnOutcomeStatus.PARTIALLY_ANSWERED
                failure = None
        # context_eligible remains False for failed tasks and limited delivery.

    assistant_message_id = uuid5(
        NAMESPACE_URL,
        f"research-task-message:{workspace_id}:{turn_row.conversation_id}:{task_id}",
    )
    message = await append_conversation_message(
        session,
        workspace_id=workspace_id,
        conversation_id=turn_row.conversation_id,
        message_id=assistant_message_id,
        role="ASSISTANT",
        content=content,
    )
    turn = await get_conversation_turn(session, workspace_id, turn_row.id)
    attempt_id = turn.active_attempt_id
    if attempt_id is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "chat research Turn lost its active attempt",
        )
    _, _, outcome_id = conversation_turn_ids(
        workspace_id,
        turn.conversation_id,
        turn.user_message_id,
    )
    await commit_conversation_turn_outcome(
        session,
        workspace_id=workspace_id,
        turn_id=turn.turn_id,
        attempt_id=attempt_id,
        fencing_token=turn.fencing_token,
        outcome_id=outcome_id,
        status=outcome_status,
        assistant_message_id=assistant_message_id,
        failure=failure,
        context_eligible=context_eligible,
    )
    return message
