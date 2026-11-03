#!/bin/bash
# Restore the database from a backup made by backup-now.sh:
#   /opt/clashforces/restore.sh /opt/clashforces/backups/clashforces-20261113-1200.db.gz
# Takes a safety backup of the current database first, stops the app, swaps the file in,
# and starts the app again. Refuses while a match is live unless FORCE=1.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

file="${1:-}"
if [ -z "$file" ]; then
    echo "usage: $0 <backup.db.gz>   (newest backups below)"
    find "$BACKUPS" -maxdepth 1 -name 'clashforces-*.db.gz' -printf '%f\n' 2> /dev/null | sort | tail -n 5
    exit 2
fi
[ -f "$file" ] || file="$BACKUPS/$file"   # a bare file name means one in the backups folder
[ -f "$file" ] || { echo "no such file: $1"; exit 2; }
gzip -t "$file" || { echo "$file is not a valid .gz"; exit 2; }

exec 9> "$STATE/deploy.lock"
flock -n 9 || { echo "A deploy is running; try again in a few minutes."; exit 1; }

if [ "${FORCE:-0}" != 1 ]; then
    if ! n="$(running_matches)"; then
        echo "The app does not answer /api/health; can't tell whether a match is live. FORCE=1 to restore anyway."
        exit 1
    fi
    if [ "$n" -gt 0 ]; then
        echo "$n match(es) are running. Restoring now would lose them. FORCE=1 to restore anyway."
        exit 1
    fi
fi

image="$(app_image)"
log "restore: starting from $file (safety backup first)"
if ! "$BASE/backup-now.sh"; then
    if [ "${FORCE:-0}" != 1 ]; then
        log "restore: aborted, the safety backup failed (FORCE=1 to restore without it)"
        exit 1
    fi
fi

had_app=0
docker container inspect "$APP" > /dev/null 2>&1 && had_app=1
docker stop -t 20 "$APP" > /dev/null 2>&1 || true

# Runs inside a container with the volume at /data; the backup arrives gzipped on stdin.
PY='
import gzip, os, shutil, sqlite3, sys
tmp = "/data/.restore.db"
with gzip.GzipFile(fileobj=sys.stdin.buffer) as g, open(tmp, "wb") as f:
    shutil.copyfileobj(g, f)
c = sqlite3.connect(tmp)
check = c.execute("PRAGMA integrity_check").fetchone()[0]
counts = {t: c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("users", "matches")}
c.close()
if check != "ok":
    os.unlink(tmp)
    sys.exit("backup failed integrity_check: " + check)
# The old WAL belongs to the old database; it must not be replayed onto the restored one.
for ext in ("-wal", "-shm"):
    try:
        os.unlink("/data/clashforces.db" + ext)
    except FileNotFoundError:
        pass
os.replace(tmp, "/data/clashforces.db")
print("users=%(users)d matches=%(matches)d" % counts)
'
if ! counts="$(docker run --rm -i -v "$VOLUME:/data" --network none --entrypoint python "$image" -c "$PY" < "$file")"; then
    log "restore: FAILED; the old database is untouched"
    [ "$had_app" = 1 ] && docker start "$APP" > /dev/null
    exit 1
fi

if [ "$had_app" = 1 ]; then
    docker start "$APP" > /dev/null
else
    run_app "$image"
fi
if wait_healthy 90; then
    log "restore: OK from $(basename "$file") ($counts); the app is up"
else
    log "restore: database restored ($counts) but the app FAILED its health check; see: docker logs $APP"
    exit 1
fi
