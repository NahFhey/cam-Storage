#!/bin/bash
# CAM Tracking Kiosk - database backup, safe while the server is running.
#
# Uses SQLite's online backup API (a plain `cp` of a live database can copy
# a half-written state). Keeps the newest $KEEP backups.
#
# Usage: ./backup_db.sh            (run nightly from cron, see INSTALL_RASPBERRY_PI.md)
# Overridable: CAM_DB_PATH, BACKUP_DIR, KEEP

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${CAM_DB_PATH:-$APP_DIR/cam_tracking.db}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/cam-backups}"
KEEP="${KEEP:-30}"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: database not found: $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR" || exit 1
TARGET="$BACKUP_DIR/cam_tracking_$(date +%Y%m%d_%H%M%S).db"

python3 - "$DB_PATH" "$TARGET" <<'EOF' || exit 1
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
ok = target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
target.close()
source.close()
if not ok:
    sys.exit("ERROR: backup failed its integrity check")
EOF

echo "Backed up to $TARGET"

# Remove all but the newest $KEEP backups
find "$BACKUP_DIR" -maxdepth 1 -name 'cam_tracking_*.db' -printf '%T@ %p\n' \
    | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f
