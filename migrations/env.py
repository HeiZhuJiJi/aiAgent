import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.storage.tables import Base


def apply(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as connection:
        await connection.run_sync(apply)
    await engine.dispose()


asyncio.run(run())
