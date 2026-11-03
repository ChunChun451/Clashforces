#!/bin/bash
# Consistent SQLite backup (SQLite's online backup API, safe while the app is writing)
# to /opt/clashforces/backups/clashforces-YYYYMMDD-HHMM.db.gz (UTC). Keeps the newest $KEEP (14).
# Run nightly by clashforces-backup.timer, and by hand before the event.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

KEEP="${KEEP:-14}"
mkdir -p "$BACKUPS"
chmod 700 "$BACKUPS"

name="clashforces-$(date -u +%Y%m%d-%H%M).db.gz"
[ -e "$BACKUPS/$name" ] && name="clashforces-$(date -u +%Y%m%d-%H%M%S).db.gz"
tmp="$BACKUPS/.partial-$$.gz"
trap 'rm -f "$tmp"' EXIT

# Runs inside a container that has the volume at /data. Writes the gzipped copy to stdout.
PY='
import gzip, os, shutil, sqlite3, sys, tempfile
path = "/data/clashforces.db"
if not os.path.exists(path):
    sys.exit("no database at " + path)
src = sqlite3.connect(path, timeout=30)
fd, tmp = tempfile.mkstemp(suffix=".db")
os.close(fd)
dst = sqlite3.connect(tmp)
src.backup(dst)
src.close()
check = dst.execute("PRAGMA integrity_check").fetchone()[0]
counts = {t: dst.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("users", "matches")}
dst.close()
if check != "ok":
    sys.exit("backup failed integrity_check: " + check)
with open(tmp, "rb") as f, gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb") as g:
    shutil.copyfileobj(f, g)
os.unlink(tmp)
print("users=%(users)d matches=%(matches)d" % counts, file=sys.stderr)
'

if app_running; then
    docker exec "$APP" python -c "$PY" > "$tmp" 2> "$STATE/backup.err" || failed=1
else
    docker run --rm -v "$VOLUME:/data" --network none --entrypoint python "$(app_image)" -c "$PY" \
        > "$tmp" 2> "$STATE/backup.err" || failed=1
fi
if [ -n "${failed:-}" ] || ! gzip -t "$tmp" 2> /dev/null; then
    log "backup: FAILED: $(tail -c 300 "$STATE/backup.err")"
    exit 1
fi
mv "$tmp" "$BACKUPS/$name"
chmod 600 "$BACKUPS/$name"

# Keep the newest $KEEP backups (names sort by time).
mapfile -t all < <(find "$BACKUPS" -maxdepth 1 -name 'clashforces-*.db.gz' -printf '%f\n' | sort)
removed=0
if [ "${#all[@]}" -gt "$KEEP" ]; then
    for old in "${all[@]:0:${#all[@]}-KEEP}"; do
        rm -f "$BACKUPS/$old"
        removed=$((removed + 1))
    done
fi
log "backup: $BACKUPS/$name ($(du -h "$BACKUPS/$name" | cut -f1), $(tail -n 1 "$STATE/backup.err"), removed $removed old)"
