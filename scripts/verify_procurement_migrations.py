"""Apply Procurement migrations to a disposable database and verify its schema."""

import asyncio
import os
import re
import sys
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import URL, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    password = dotenv_values(".env").get("PROCUREMENT_DB_PASSWORD")
    if not password:
        raise ValueError("PROCUREMENT_DB_PASSWORD is missing")
    database = "procurement_migration_verify_" + uuid4().hex
    if re.fullmatch(r"procurement_migration_verify_[0-9a-f]{32}", database) is None:
        raise ValueError("unsafe temporary database name")
    admin_url = URL.create(
        "postgresql+asyncpg",
        username="procurement",
        password=password,
        host="127.0.0.1",
        port=55433,
        database="postgres",
    )
    target_url = admin_url.set(database=database)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
        environment = os.environ.copy()
        environment["PROCUREMENT_DATABASE_URL"] = target_url.render_as_string(
            hide_password=False
        )
        migration = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "mcp_servers/procurement/alembic.ini",
            "upgrade",
            "head",
            env=environment,
        )
        if await migration.wait() != 0:
            raise RuntimeError("Procurement Alembic migration subprocess failed")
        target = create_async_engine(target_url)
        try:
            async with target.connect() as connection:
                tables = await connection.run_sync(
                    lambda sync: set(inspect(sync).get_table_names())
                )
                version = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            if not {"suppliers", "admissions"} <= tables:
                raise AssertionError("fresh Procurement schema is incomplete")
            if version != "0001_procurement":
                raise AssertionError("unexpected Procurement migration head")
        finally:
            await target.dispose()
        print("Fresh Procurement Alembic migration verification passed.")
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
