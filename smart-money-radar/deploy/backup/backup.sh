#!/bin/sh
# Daily pg_dump with retention. Runs inside the `backup` service (postgres client image).
#   BACKUP_AT_UTC    time of the daily backup (UTC)
#   BACKUP_CATCH_UP  1 = also back up right after start when the newest dump is older than 24 h
#                    (a personal computer is often off at the scheduled time)
set -eu
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
AT="${BACKUP_AT_UTC:-19:30}"   # 02:30 Vietnam time
mkdir -p /backups

do_backup() {
  f="/backups/radar-$(date -u +%Y%m%d-%H%M%S).dump"
  # TimescaleDB catalog tables always trigger harmless "circular foreign-key" warnings: hidden
  if pg_dump -Fc -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f.tmp" 2>/tmp/pg_dump.err; then
    mv "$f.tmp" "$f"; echo "backup written $f ($(du -h "$f" | cut -f1))"
  else
    echo "backup FAILED"; rm -f "$f.tmp"
  fi
  grep -v -E "circular foreign-key|detail: (hypertable|chunk|continuous_agg)|hint: (You might not|Consider using)" \
    /tmp/pg_dump.err || true
  find /backups -name 'radar-*.dump' -mtime +"$KEEP_DAYS" -delete
}

if [ "${1:-}" = "now" ]; then   # manual backup: sh /backup.sh now
  do_backup
  exit 0
fi

echo "backup service: daily at ${AT} UTC, keep ${KEEP_DAYS} days, catch-up=${BACKUP_CATCH_UP:-0}"
if [ "${BACKUP_CATCH_UP:-0}" = "1" ]; then
  sleep "${BACKUP_STARTUP_DELAY:-120}"   # let the application finish starting
  if [ -z "$(find /backups -name 'radar-*.dump' -mmin -1440 2>/dev/null | head -n 1)" ]; then
    echo "no backup in the last 24 h: backing up now"
    do_backup
  fi
fi
while true; do
  if [ "$(date -u +%H:%M)" = "$AT" ]; then
    do_backup
    sleep 61
  fi
  sleep 20
done
