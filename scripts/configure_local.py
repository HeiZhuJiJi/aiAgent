import secrets
from pathlib import Path

from cryptography.fernet import Fernet

p = Path(".env")
if not p.exists():
    p.write_text(
        "\n".join(
            [
                f"{k}={secrets.token_urlsafe(32)}"
                for k in ["POSTGRES_PASSWORD", "PROCUREMENT_DB_PASSWORD", "MINIO_ROOT_PASSWORD"]
            ]
        )
        + f"\nSECRET_MASTER_KEY={Fernet.generate_key().decode()}\n",
        encoding="utf-8",
    )
print("Local secrets configured; values withheld.")
