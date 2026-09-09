import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.procurement.service import ProcurementBase


def apply(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=ProcurementBase.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run() -> None:
    engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    async with engine.connect() as connection:
        await connection.run_sync(apply)
    await engine.dispose()


asyncio.run(run())
