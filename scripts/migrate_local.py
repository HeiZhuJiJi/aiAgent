import os

from dotenv import dotenv_values
from sqlalchemy import URL

values = dotenv_values(".env")
os.environ["DATABASE_URL"] = URL.create(
    "postgresql+asyncpg",
    username="platform",
    password=values["POSTGRES_PASSWORD"],
    host="127.0.0.1",
    port=55432,
    database="platform",
).render_as_string(hide_password=False)
from alembic import command
from alembic.config import Config

command.upgrade(Config("alembic.ini"), "head")
print("Migration applied to project PostgreSQL.")
