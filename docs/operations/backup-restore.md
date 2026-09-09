# Backup and restore

The runtime backup contains logical PostgreSQL dumps for `platform`, `procurement`,
`temporal`, and `temporal_visibility`; every MinIO object; the deployment configuration;
and a sanitized environment inventory. Secret values are deliberately excluded and must
be supplied from the organization's secret manager during recovery.

Create a backup while the local infrastructure is healthy:

```powershell
.venv312\Scripts\python.exe scripts/backup.py work/backups/runtime-2026-09-09
```

Restore databases only into new database names. The command refuses unsafe identifiers
and refuses an existing target database:

```powershell
.venv312\Scripts\python.exe scripts/restore.py work/backups/runtime-2026-09-09 `
  --database platform=platform_restored `
  --database procurement=procurement_restored
```

On an empty MinIO deployment, omit the prefix to restore the original bucket names. To
test recovery beside a live system, supply a prefix so objects go to isolated buckets:

```powershell
.venv312\Scripts\python.exe scripts/restore.py work/backups/runtime-2026-09-09 `
  --restore-minio --bucket-prefix restored-
```

After a disaster recovery, supply new secret values, point `DATABASE_URL` and
`PROCUREMENT_DATABASE_URL` at the restored databases, start Temporal and the worker, then
verify Task, Plan, Evidence, Artifact, and Audit records before admitting new work. The
automated verification procedure is `scripts/verify_backup_restore.py`; it creates and
runs the flagship Supplier A task, generates the DOCX artifact, backs up all stores,
restores both application databases and MinIO data, and records observed hashes/counts.

