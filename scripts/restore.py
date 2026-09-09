import argparse
from pathlib import Path

from dotenv import dotenv_values
from minio import Minio

from enterprise_agent.operations.backups import (
    load_and_verify_backup,
    restore_database,
    restore_minio,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a verified runtime backup")
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--database",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help="restore a named source database to a new target database",
    )
    parser.add_argument("--restore-minio", action="store_true")
    parser.add_argument("--bucket-prefix", default="")
    args = parser.parse_args()
    manifest = load_and_verify_backup(args.source)
    dumps = {item.database: item for item in manifest.database_dumps}
    for mapping in args.database:
        source_name, separator, target_name = mapping.partition("=")
        if not separator or source_name not in dumps:
            raise ValueError(f"invalid database mapping: {mapping}")
        restore_database(args.source, dumps[source_name], target_name)
    if args.restore_minio:
        password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
        if not password:
            raise ValueError("MINIO_ROOT_PASSWORD is missing")
        client = Minio(
            "127.0.0.1:59000", access_key="platform", secret_key=str(password), secure=False
        )
        mapping = restore_minio(args.source, manifest, client, args.bucket_prefix)
        print(f"Restored {len(manifest.objects)} objects into {len(mapping)} buckets.")
    print("Restore completed after manifest and payload integrity verification.")


if __name__ == "__main__":
    main()

