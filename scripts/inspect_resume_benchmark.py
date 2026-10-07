"""Inspect only tasks created by one retained benchmark run without changing outcomes."""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.storage.tables import TaskRow
from scripts.run_resume_benchmark import emit, environment, write_json


async def inspect(directory):
    environment()
    metadata = json.loads((directory / "environment.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    result = {"observed_at": datetime.now(UTC).isoformat(), "tasks": []}
    async with sessions() as session:
        for row in rows:
            if not row.get("task_id"):
                continue
            task = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == UUID(metadata["workspace_id"]),
                    TaskRow.id == UUID(row["task_id"]),
                )
            )
            result["tasks"].append(
                {
                    "case_id": row["case_id"],
                    "task_id": row["task_id"],
                    "state_at_deadline": row.get("task_state"),
                    "state_now": task.state if task else "MISSING",
                    "version": task.version if task else None,
                }
            )
    await engine.dispose()
    write_json(directory / "latest_task_states.json", result)
    emit(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(inspect(parser.parse_args().directory))
