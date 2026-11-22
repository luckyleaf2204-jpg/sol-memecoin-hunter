#!/bin/sh
# Restore a dump made by backup.sh into the running postgres service.
#   sh /restore.sh radar-20261122-0500.dump
# The database is dropped and recreated. TimescaleDB requires timescaledb_pre_restore() /
# timescaledb_post_restore() around pg_restore. Stop the application services first.
set -eu
f="/backups/${1:?usage: restore.sh <file name in the backups folder>}"
[ -f "$f" ] || { echo "file not found: $f"; exit 1; }
U="$POSTGRES_USER"; DB="$POSTGRES_DB"
echo "restoring $f into database $DB ..."
psql -v ON_ERROR_STOP=1 -h postgres -U "$U" -d postgres -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE);" \
     -c "CREATE DATABASE \"$DB\" OWNER \"$U\";"
psql -v ON_ERROR_STOP=1 -h postgres -U "$U" -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" \
     -c "SELECT timescaledb_pre_restore();" >/dev/null
# pg_restore may print harmless notices about TimescaleDB catalog objects; the exit code is checked below
set +e
pg_restore -h postgres -U "$U" -d "$DB" --no-owner "$f"
rc=$?
set -e
psql -v ON_ERROR_STOP=1 -h postgres -U "$U" -d "$DB" -c "SELECT timescaledb_post_restore();" >/dev/null
n=$(psql -At -h postgres -U "$U" -d "$DB" -c "SELECT count(*) FROM wallets")
echo "restore finished (pg_restore exit code $rc). wallets in database: $n"
