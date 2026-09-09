import argparse
from pathlib import Path

from dotenv import dotenv_values
from minio import Minio

from enterprise_agent.operations.backups import create_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up PostgreSQL, MinIO, and configuration")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    if not password:
        raise ValueError("MINIO_ROOT_PASSWORD is missing")
    manifest = create_backup(
        args.target,
        Minio("127.0.0.1:59000", access_key="platform", secret_key=str(password), secure=False),
    )
    print(
        f"Backup verified: {len(manifest.database_dumps)} databases, "
        f"{len(manifest.objects)} objects; secrets excluded."
    )


if __name__ == "__main__":
    main()

