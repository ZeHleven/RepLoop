#!/usr/bin/env bash
# Dumps the PostgreSQL database and rotates backups older than 7 days.
# Run via cron: 0 3 * * * /opt/fitness_agent/scripts/backup_db.sh

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/fitness_agent}"
CONTAINER="${DB_CONTAINER:-fitness_agent_postgres_1}"
POSTGRES_USER="${POSTGRES_USER:-fitness}"
POSTGRES_DB="${POSTGRES_DB:-fitness_db}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_FILE="${BACKUP_DIR}/db_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

docker exec "$CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DUMP_FILE"

echo "[$(date)] Backup saved: $DUMP_FILE"

# Remove backups older than 7 days
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime +7 -delete
echo "[$(date)] Old backups cleaned."
