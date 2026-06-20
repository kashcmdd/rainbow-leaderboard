#!/bin/bash

BACKUP_DIR="/home/riley/docker/rainbow-leaderboard/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/rainbow_$TIMESTAMP.sql"

if docker exec rainbow-leaderboard-db pg_dump -U rainbow rainbow > "$BACKUP_FILE"; then
    gzip "$BACKUP_FILE"
    logger -t rainbow-backup "Backup succeeded: rainbow_$TIMESTAMP.sql.gz"
else
    rm -f "$BACKUP_FILE"
    logger -t rainbow-backup "Backup FAILED"
    exit 1
fi

ls -1t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --


