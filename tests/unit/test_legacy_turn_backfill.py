import importlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from enterprise_agent.contracts.domain import (
    ConversationTurnOutcome,
    ConversationTurnOutcomeStatus,
    Failure,
)
from enterprise_agent.runtime.conversation_results import conversation_turn_ids
from enterprise_agent.storage.turns import _outcome_payload

migration = importlib.import_module(
    "migrations.versions.0033_conservative_legacy_turn_backfill"
)


def _pair(
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    seed: int,
    content: str,
) -> Any:
    started_at = datetime(2026, 9, 14, 1, 2, 3, 1000, tzinfo=UTC) + timedelta(
        minutes=seed * 2
    )
    return migration._LegacyPair(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        user_message_id=UUID(int=1000 + seed),
        assistant_message_id=UUID(int=2000 + seed),
        user_created_at=started_at,
        assistant_created_at=started_at + timedelta(minutes=1),
        assistant_content=content,
    )


def test_failure_classifier_is_deliberately_conservative() -> None:
    failures = (
        "抱歉，我无法提供美国华盛顿明天的天气情况。当前检索没有返回任何可用的天气预报结果，因此没有可靠信息可供参考。",
        "关于希拉里·克林顿目前的活动，我这边没有检索到可靠的最新信息，所以无法告诉你她最近在做什么。",
        "Sorry, I am unable to answer because the search service is unavailable.",
        "本轮已安全结束，请稍后重试。",
    )
    for content in failures:
        assert migration._is_obvious_failure_or_refusal(content)

    factual_answers = (
        "特朗普出生于1946年6月14日。",
        "搜索结果显示，多个独立来源报道了同一事件。",
        "这个模型在旧测试中 failed，但新版本已经通过全部测试。",
        "虽然未获得多数票，他仍赢得了选举。",
    )
    for content in factual_answers:
        assert not migration._is_obvious_failure_or_refusal(content)


def test_backfill_preserves_history_but_never_trusts_legacy_context() -> None:
    workspace_id = UUID(int=1)
    first_conversation = UUID(int=2)
    second_conversation = UUID(int=3)
    first_pairs = [
        _pair(
            workspace_id=workspace_id,
            conversation_id=first_conversation,
            seed=index,
            content=(
                "抱歉，我无法回答。"
                if index == 2
                else f"这是第 {index + 1} 个可靠回答。"
            ),
        )
        for index in range(6)
    ]
    second_pairs = [
        _pair(
            workspace_id=workspace_id,
            conversation_id=second_conversation,
            seed=20 + index,
            content=f"另一个会话的第 {index + 1} 个可靠回答。",
        )
        for index in range(5)
    ]

    records = migration._prepare_backfill([*first_pairs, *second_pairs])
    by_user = {record.pair.user_message_id: record for record in records}

    assert not by_user[first_pairs[0].user_message_id].context_eligible
    assert not by_user[first_pairs[1].user_message_id].context_eligible
    assert not by_user[first_pairs[2].user_message_id].context_eligible
    assert by_user[first_pairs[2].user_message_id].attempt_state == "FAILED"
    assert by_user[first_pairs[2].user_message_id].outcome_status == "UNAVAILABLE"
    assert by_user[first_pairs[2].user_message_id].failure_payload is not None
    assert all(
        not by_user[pair.user_message_id].context_eligible for pair in first_pairs[3:]
    )
    assert all(
        not by_user[pair.user_message_id].context_eligible for pair in second_pairs
    )


def test_backfill_ids_and_hash_match_runtime_contract() -> None:
    pair = _pair(
        workspace_id=UUID(int=10),
        conversation_id=UUID(int=11),
        seed=30,
        content="抱歉，我无法完成本次查询。",
    )
    record = migration._prepare_backfill([pair])[0]
    expected_ids = conversation_turn_ids(
        pair.workspace_id,
        pair.conversation_id,
        pair.user_message_id,
    )
    assert (record.turn_id, record.attempt_id, record.outcome_id) == expected_ids
    assert migration._json_datetime(pair.assistant_created_at).endswith(".001000Z")

    failure = Failure.model_validate(record.failure_payload)
    outcome = ConversationTurnOutcome(
        outcome_id=record.outcome_id,
        workspace_id=pair.workspace_id,
        turn_id=record.turn_id,
        attempt_id=record.attempt_id,
        status=ConversationTurnOutcomeStatus(record.outcome_status),
        assistant_message_id=pair.assistant_message_id,
        failure=failure,
        context_eligible=record.context_eligible,
        created_at=pair.assistant_created_at,
    )
    _, runtime_hash = _outcome_payload(outcome)
    assert record.content_hash == runtime_hash
