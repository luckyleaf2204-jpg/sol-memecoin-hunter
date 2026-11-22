#!/bin/sh
# Nightly pg_dump with retention. Runs inside the `backup` service (postgres client image).
set -eu
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
AT="${BACKUP_AT_UTC:-19:30}"   # 02:30 Vietnam time
mkdir -p /backups
echo "backup service: daily at ${AT} UTC, keep ${KEEP_DAYS} days"
while true; do
  now=$(date -u +%H:%M)
  if [ "$now" = "$AT" ]; then
    f="/backups/radar-$(date -u +%Y%m%d-%H%M).dump"
    if pg_dump -Fc -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f.tmp"; then
      mv "$f.tmp" "$f"; echo "backup written $f ($(du -h "$f" | cut -f1))"
    else
      echo "backup FAILED"; rm -f "$f.tmp"
    fi
    find /backups -name 'radar-*.dump' -mtime +"$KEEP_DAYS" -delete
    sleep 61
  fi
  sleep 20
done
