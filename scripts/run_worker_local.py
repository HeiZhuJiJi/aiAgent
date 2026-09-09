"""Run the independent worker with local development configuration."""

import asyncio
import os

from dotenv import dotenv_values
from sqlalchemy import URL

from apps.worker.main import main


def configure() -> None:
    values = dotenv_values(".env")
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise ValueError("POSTGRES_PASSWORD is missing")
    os.environ.setdefault(
        "DATABASE_URL",
        URL.create(
            "postgresql+asyncpg",
            username="platform",
            password=password,
            host="127.0.0.1",
            port=55432,
            database="platform",
        ).render_as_string(hide_password=False),
    )
    minio_password = values.get("MINIO_ROOT_PASSWORD")
    if not minio_password:
        raise ValueError("MINIO_ROOT_PASSWORD is missing")
    os.environ.setdefault("MINIO_ENDPOINT", "127.0.0.1:59000")
    os.environ.setdefault("MINIO_ACCESS_KEY", "platform")
    os.environ.setdefault("MINIO_SECRET_KEY", str(minio_password))


if __name__ == "__main__":
    configure()
    asyncio.run(main())
