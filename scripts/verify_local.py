import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import URL

temp_root = Path("work/pytest-temp").resolve()
temp_root.mkdir(parents=True, exist_ok=True)
run_temp = temp_root / str(uuid4())
assert run_temp.parent == temp_root and not run_temp.exists()
v = dotenv_values(".env")
for key, user, port, db, pw in [
    ("TEST_DATABASE_URL", "platform", 55432, "platform", "POSTGRES_PASSWORD"),
    ("PROCUREMENT_DATABASE_URL", "procurement", 55433, "procurement", "PROCUREMENT_DB_PASSWORD"),
]:
    os.environ[key] = URL.create(
        "postgresql+asyncpg",
        username=user,
        password=v[pw],
        host="127.0.0.1",
        port=port,
        database=db,
    ).render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
subprocess.run([sys.executable, "scripts/init_procurement.py"], check=True)
raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp=" + str(run_temp),
            "--junitxml=work/verification/pytest.xml",
            *sys.argv[1:],
        ]
    )
)
