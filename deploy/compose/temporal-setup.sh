#!/bin/sh
set -eu
for db in temporal temporal_visibility; do
  temporal-sql-tool --plugin postgres12 -p 5432 --ep "$POSTGRES_SEEDS" -u "$POSTGRES_USER" --db "$db" create || true
  temporal-sql-tool --plugin postgres12 -p 5432 --ep "$POSTGRES_SEEDS" -u "$POSTGRES_USER" --db "$db" setup-schema -v 0.0 || true
done
temporal-sql-tool --plugin postgres12 -p 5432 --ep "$POSTGRES_SEEDS" -u "$POSTGRES_USER" --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
temporal-sql-tool --plugin postgres12 -p 5432 --ep "$POSTGRES_SEEDS" -u "$POSTGRES_USER" --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
