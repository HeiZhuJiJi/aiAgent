import asyncio
import os
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.procurement.demo import DEMO_WORKSPACE_ID, seed_demo_suppliers


async def main() -> None:
    engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    workspace_id = UUID(os.environ.get("DEMO_WORKSPACE_ID", str(DEMO_WORKSPACE_ID)))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await seed_demo_suppliers(session, workspace_id)
    await engine.dispose()


command.upgrade(Config("mcp_servers/procurement/alembic.ini"), "head")
asyncio.run(main())
