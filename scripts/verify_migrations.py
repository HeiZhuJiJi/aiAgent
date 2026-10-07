"""Apply every migration to a disposable database and verify the resulting schema."""

import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from dotenv import dotenv_values
from sqlalchemy import URL, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.storage.turns import load_conversation_context_projection

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _upgrade(environment: dict[str, str], revision: str) -> None:
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", revision],
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic migration to {revision} failed")


async def _seed_legacy_history(
    target_url: URL,
) -> tuple[UUID, UUID, list[UUID], UUID]:
    workspace_id = uuid4()
    conversation_id = uuid4()
    pair_user_ids = [uuid4() for _ in range(6)]
    pair_assistant_ids = [uuid4() for _ in range(6)]
    unpaired_user_id = uuid4()
    answers = [
        "第一个可靠回答，因为过旧不应进入上下文。",
        "第二个可靠回答。",
        "抱歉，我无法回答。当前检索没有返回任何可用结果。",
        "第四个可靠回答。",
        "第五个可靠回答。",
        "第六个可靠回答。",
    ]
    base = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    message_rows: list[dict[str, object]] = []
    for index, (user_id, assistant_id, answer) in enumerate(
        zip(pair_user_ids, pair_assistant_ids, answers, strict=True)
    ):
        question = f"legacy question {index + 1}"
        message_rows.extend(
            (
                {
                    "id": user_id,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "role": "USER",
                    "content": question,
                    "content_hash": sha256(question.encode()).hexdigest(),
                    "created_at": base + timedelta(minutes=index * 2),
                },
                {
                    "id": assistant_id,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "role": "ASSISTANT",
                    "content": answer,
                    "content_hash": sha256(answer.encode()).hexdigest(),
                    "created_at": base + timedelta(minutes=index * 2 + 1),
                },
            )
        )
    orphan_content = "this user message has no assistant response"
    message_rows.append(
        {
            "id": unpaired_user_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "role": "USER",
            "content": orphan_content,
            "content_hash": sha256(orphan_content.encode()).hexdigest(),
            "created_at": base + timedelta(minutes=20),
        }
    )

    target = create_async_engine(target_url)
    try:
        async with target.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO workspaces(id, name, enabled, version)
                    VALUES (:id, 'migration verification', true, 1)
                    """
                ),
                {"id": workspace_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO conversations(
                        id, workspace_id, schema_version, title, version, created_at
                    ) VALUES (
                        :id, :workspace_id, 1, 'legacy backfill fixture', 1, :created_at
                    )
                    """
                ),
                {
                    "id": conversation_id,
                    "workspace_id": workspace_id,
                    "created_at": base - timedelta(minutes=1),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO messages(
                        id, workspace_id, conversation_id, schema_version,
                        role, content, content_hash, created_at
                    ) VALUES (
                        :id, :workspace_id, :conversation_id, 1,
                        :role, :content, :content_hash, :created_at
                    )
                    """
                ),
                message_rows,
            )
    finally:
        await target.dispose()
    return workspace_id, conversation_id, pair_user_ids, unpaired_user_id


async def main() -> None:
    values = dotenv_values(".env")
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise ValueError("POSTGRES_PASSWORD is missing")
    database = "migration_verify_" + uuid4().hex
    if re.fullmatch(r"migration_verify_[0-9a-f]{32}", database) is None:
        raise ValueError("unsafe temporary database name")
    admin_url = URL.create(
        "postgresql+asyncpg",
        username="platform",
        password=password,
        host="127.0.0.1",
        port=55432,
        database="postgres",
    )
    target_url = admin_url.set(database=database)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
        environment = os.environ.copy()
        environment["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
        await _upgrade(environment, "0032_conversation_turn_lifecycle")
        (
            workspace_id,
            conversation_id,
            pair_user_ids,
            unpaired_user_id,
        ) = await _seed_legacy_history(target_url)
        await _upgrade(environment, "head")
        target = create_async_engine(target_url)
        try:
            async with target.connect() as connection:
                tables = await connection.run_sync(
                    lambda sync: set(inspect(sync).get_table_names())
                )
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                step_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("task_steps")
                    )
                }
                attempt_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("step_attempts")
                    )
                }
                task_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("task_runs")
                    )
                }
                model_config_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("model_configs")
                    )
                }
                turn_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("conversation_turns")
                    )
                }
                decision_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns(
                            "conversation_turn_decisions"
                        )
                    )
                }
                conversation_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("conversations")
                    )
                }
                knowledge_document_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("knowledge_documents")
                    )
                }
                knowledge_job_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("knowledge_ingestion_jobs")
                    )
                }
                turn_rls = (
                    await connection.execute(
                        text("""
                            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                                   count(p.policyname) FILTER (
                                       WHERE p.qual LIKE '%app.workspace_id%'
                                         AND p.with_check LIKE '%app.workspace_id%'
                                   ) AS workspace_policies
                            FROM pg_class c
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            LEFT JOIN pg_policies p
                              ON p.schemaname = n.nspname
                             AND p.tablename = c.relname
                            WHERE n.nspname = 'public'
                              AND c.relname IN (
                                   'conversation_turns',
                                   'conversation_turn_decisions',
                                   'conversation_turn_attempts',
                                  'conversation_turn_outcomes',
                                  'task_contract_versions',
                                  'requirements',
                                  'requirement_supports',
                                  'verification_results',
                                  'requirement_assessments',
                                  'gaps',
                                  'delivery_decisions'
                              )
                            GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                        """)
                    )
                ).all()
                backfilled = (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                turn.user_message_id,
                                turn.state AS turn_state,
                                turn.context_eligible AS turn_eligible,
                                attempt.state AS attempt_state,
                                outcome.status AS outcome_status,
                                outcome.failure_payload,
                                outcome.context_eligible AS outcome_eligible
                            FROM conversation_turns AS turn
                            JOIN conversation_turn_attempts AS attempt
                              ON attempt.workspace_id = turn.workspace_id
                             AND attempt.id = turn.active_attempt_id
                            JOIN conversation_turn_outcomes AS outcome
                              ON outcome.workspace_id = turn.workspace_id
                             AND outcome.turn_id = turn.id
                            WHERE turn.workspace_id = :workspace_id
                              AND turn.conversation_id = :conversation_id
                            ORDER BY turn.created_at, turn.id
                            """
                        ),
                        {
                            "workspace_id": workspace_id,
                            "conversation_id": conversation_id,
                        },
                    )
                ).all()
                orphan_turns = await connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM conversation_turns
                        WHERE workspace_id = :workspace_id
                          AND user_message_id = :user_message_id
                        """
                    ),
                    {
                        "workspace_id": workspace_id,
                        "user_message_id": unpaired_user_id,
                    },
                )
            required = {
                "workspaces",
                "task_runs",
                "task_steps",
                "step_attempts",
                "evidence",
                "audit_logs",
                "operations",
                "domain_commands",
                "execution_snapshots",
                "task_plans",
                "approval_requests",
                "outbox_events",
                "capability_calls",
                "task_budgets",
                "runtime_guards",
                "users",
                "workspace_members",
                "auth_sessions",
                "task_checkpoints",
                "claims",
                "claim_evidence_links",
                "artifacts",
                "knowledge_documents",
                "knowledge_document_versions",
                "knowledge_ingestion_jobs",
                "knowledge_chunks",
                "conversations",
                "conversation_summaries",
                "project_memory",
                "messages",
                "conversation_turns",
                "conversation_turn_decisions",
                "conversation_turn_attempts",
                "conversation_turn_outcomes",
                "checkpoint_migrations",
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
                "skills",
                "skill_versions",
                "skill_installations",
                "mcp_servers",
                "mcp_capabilities",
                "replan_decisions",
                "evaluation_cases",
                "evaluation_runs",
                "evaluation_results",
                "usage_records",
                "consistency_incidents",
                "service_accounts",
                "capabilities",
                "knowledge_bases",
                "model_configs",
                "secrets",
                "retention_policies",
                "workspace_deletion_requests",
                "task_contract_versions",
                "requirements",
                "requirement_supports",
                "verification_results",
                "requirement_assessments",
                "gaps",
                "delivery_decisions",
            }
            if (
                not required <= tables
                or version != "0039_completion_immutability"
                or not {
                    "plan_id",
                    "step_key",
                    "spec_payload",
                    "retry_available_at",
                }
                <= step_columns
                or "failure_payload" not in attempt_columns
                or "created_by" not in task_columns
                or "supersedes_config_id" not in model_config_columns
                or "deleted_at" not in conversation_columns
                or "deleted_at" not in knowledge_document_columns
                or "deleted_at" not in knowledge_job_columns
                or not {
                    "user_message_id",
                    "active_attempt_id",
                    "fencing_token",
                    "context_eligible",
                }
                <= turn_columns
                or not {
                    "turn_id",
                    "decision_version",
                    "model_binding_payload",
                    "decision_payload",
                    "content_hash",
                }
                <= decision_columns
                or {row.relname for row in turn_rls}
                != {
                    "conversation_turns",
                    "conversation_turn_decisions",
                    "conversation_turn_attempts",
                    "conversation_turn_outcomes",
                    "task_contract_versions",
                    "requirements",
                    "requirement_supports",
                    "verification_results",
                    "requirement_assessments",
                    "gaps",
                    "delivery_decisions",
                }
                or any(
                    not row.relrowsecurity
                    or not row.relforcerowsecurity
                    or row.workspace_policies != 1
                    for row in turn_rls
                )
            ):
                raise AssertionError("fresh migration schema is incomplete")

            if len(backfilled) != 6 or orphan_turns != 0:
                raise AssertionError("legacy adjacency backfill is incomplete")
            by_user = {row.user_message_id: row for row in backfilled}
            expected_eligible: set[UUID] = set()
            actual_eligible = {
                row.user_message_id
                for row in backfilled
                if row.turn_eligible and row.outcome_eligible
            }
            failure = by_user[pair_user_ids[2]]
            if (
                actual_eligible != expected_eligible
                or failure.turn_state != "COMPLETED"
                or failure.attempt_state != "FAILED"
                or failure.outcome_status != "UNAVAILABLE"
                or failure.turn_eligible
                or failure.outcome_eligible
                or failure.failure_payload["code"] != "CAPABILITY_UNAVAILABLE"
                or by_user[pair_user_ids[0]].turn_eligible
                or by_user[pair_user_ids[0]].outcome_eligible
            ):
                raise AssertionError("legacy eligibility or failure classification is unsafe")

            sessions = async_sessionmaker(target, expire_on_commit=False)
            async with sessions.begin() as session:
                await session.execute(
                    text("SELECT set_config('app.workspace_id', :workspace, true)"),
                    {"workspace": str(workspace_id)},
                )
                projection = await load_conversation_context_projection(
                    session,
                    workspace_id,
                    conversation_id,
                )
            if projection:
                raise AssertionError("legacy Context Projection is invalid")
        finally:
            await target.dispose()
        print("Fresh Alembic migration and legacy Turn backfill verification passed.")
    finally:
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
