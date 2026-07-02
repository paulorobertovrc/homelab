#!/usr/bin/env bash
# Nightly backup of the media stack's app configs/DBs.
# Source: /docker/appdata (ext4 WSL disk)  ->  /mnt/d/backups/arr_server (D:, survives WSL reset)
# Excludes regenerable junk (logs, artwork cache, gluetun server list).
# Retention: keep the last N daily archives.
# Runs unprivileged as the owner of /docker/appdata (uid 1000).
set -euo pipefail

SRC="/docker/appdata"
DEST="/mnt/d/backups/arr_server"
RETENTION=14
STAMP="$(date +%Y-%m-%d_%H%M%S)"
ARCHIVE="${DEST}/appdata-${STAMP}.tar.gz"
LOG="${DEST}/backup.log"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

mkdir -p "$DEST"

if [[ ! -d "$SRC" ]]; then
  log "ERROR: source $SRC missing — aborting"
  exit 1
fi

log "START backup of $SRC -> $ARCHIVE"

# --ignore-failed-read: don't abort if a live file (e.g. an open SQLite -wal) vanishes mid-read.
# Each *arr app also keeps its own consistent zip under */Backups, which IS included.
tar --ignore-failed-read \
    --exclude='*/logs' \
    --exclude='*/MediaCover' \
    --exclude='*/gluetun/servers.json' \
    --exclude='*.log' \
    --exclude='*.log.*' \
    -czf "${ARCHIVE}.tmp" -C "$(dirname "$SRC")" "$(basename "$SRC")"

mv "${ARCHIVE}.tmp" "$ARCHIVE"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
if [[ ! -s "$ARCHIVE" ]]; then
  log "ERROR: archive is empty — removing"
  rm -f "$ARCHIVE"
  exit 1
fi
log "OK archive ${ARCHIVE} (${SIZE})"

# Rotation: keep only the newest $RETENTION archives.
mapfile -t OLD < <(ls -1t "${DEST}"/appdata-*.tar.gz 2>/dev/null | tail -n +$((RETENTION + 1)))
for f in "${OLD[@]:-}"; do
  [[ -n "$f" ]] || continue
  rm -f "$f" && log "pruned old backup $f"
done

log "DONE ($(ls -1 "${DEST}"/appdata-*.tar.gz | wc -l) archives retained)"
