"""Real RLS checks under the explicitly authorized read-only NOLOGIN role."""

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.storage.tables import TaskRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def rls_database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            text("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runtime_rls_test') THEN
                CREATE ROLE runtime_rls_test NOLOGIN NOSUPERUSER NOCREATEDB
                  NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
              END IF;
            END $$
        """)
        )
        flags = (
            await session.execute(
                text("""
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = 'runtime_rls_test'
        """)
            )
        ).one()
        assert not any(flags), "test role must be non-login and non-privileged"
        await session.execute(text("GRANT SELECT ON TABLE public.task_runs TO runtime_rls_test"))
    yield sessions
    await engine.dispose()


async def test_rls_returns_only_active_workspace_without_application_filter(rls_database):
    workspace_a, task_a, _ = await seed(rls_database)
    workspace_b, task_b, _ = await seed(rls_database)
    for workspace, expected_task, forbidden_task in [
        (workspace_a, task_a, task_b),
        (workspace_b, task_b, task_a),
    ]:
        async with rls_database.begin() as session:
            await session.execute(text("SET LOCAL ROLE runtime_rls_test"))
            await session.execute(
                text("SELECT set_config('app.workspace_id', :workspace, true)"),
                {"workspace": str(workspace)},
            )
            rows = (await session.scalars(select(TaskRow))).all()
            assert {row.id for row in rows} == {expected_task}
            assert all(row.workspace_id == workspace for row in rows)
            assert await session.scalar(select(TaskRow).where(TaskRow.id == forbidden_task)) is None


async def test_rls_missing_workspace_is_fail_closed(rls_database):
    await seed(rls_database)
    async with rls_database.begin() as session:
        await session.execute(text("SET LOCAL ROLE runtime_rls_test"))
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        assert (await session.scalars(select(TaskRow))).all() == []


async def test_rls_role_has_only_authorized_table_privilege(rls_database):
    async with rls_database.begin() as session:
        await session.execute(text("SET LOCAL ROLE runtime_rls_test"))
        assert await session.scalar(
            text("SELECT has_table_privilege(current_user, 'public.task_runs', 'SELECT')")
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await session.scalar(
                text("SELECT has_table_privilege(current_user, 'public.task_runs', :privilege)"),
                {"privilege": privilege},
            )
        assert not await session.scalar(
            text("SELECT has_table_privilege(current_user, 'public.evidence', 'SELECT')")
        )


async def test_every_workspace_scoped_platform_table_forces_fail_closed_rls(rls_database):
    async with rls_database.begin() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           count(p.policyname) FILTER (
                               WHERE p.qual LIKE '%app.workspace_id%'
                                 AND p.with_check LIKE '%app.workspace_id%'
                           ) AS workspace_policies
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN information_schema.columns col
                      ON col.table_schema = n.nspname
                     AND col.table_name = c.relname
                     AND col.column_name = 'workspace_id'
                    LEFT JOIN pg_policies p
                      ON p.schemaname = n.nspname
                     AND p.tablename = c.relname
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                    GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                    ORDER BY c.relname
                """)
            )
        ).all()
    assert len(rows) >= 30, "expected all core workspace-scoped tables after migrations"
    unprotected = [
        row.relname
        for row in rows
        if not row.relrowsecurity or not row.relforcerowsecurity or row.workspace_policies != 1
    ]
    assert unprotected == []
