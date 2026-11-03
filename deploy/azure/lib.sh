# shellcheck shell=bash disable=SC2034  # variables are used by the scripts that source this
# Shared helpers for the Clashforces VM scripts. Installed as /opt/clashforces/lib.sh by apply.sh.
# CLASHFORCES_HOME is only for testing the scripts somewhere other than /opt/clashforces.

BASE="${CLASHFORCES_HOME:-/opt/clashforces}"
SRC="$BASE/src"
STATE="$BASE/state"
BACKUPS="$BASE/backups"
LOG="$BASE/deploy.log"
APP=clashforces            # container name (Caddy proxies to clashforces:8000)
VOLUME="${CLASHFORCES_VOLUME:-clashforces-data}"
export HOME="${HOME:-/root}"  # run-command and systemd start scripts without HOME; git wants one

mkdir -p "$STATE"

log() {
    local line
    line="$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"
    echo "$line"
    echo "$line" >> "$LOG"
}

# Log a skip message only when it differs from the previous one, so a timer that
# skips every 5 minutes doesn't fill the log.
log_once() {
    local key="$1"; shift
    if [ "$(cat "$STATE/last-skip" 2>/dev/null)" != "$key" ]; then
        log "$@"
        echo "$key" > "$STATE/last-skip"
    else
        echo "$*"
    fi
}

clear_skip() { rm -f "$STATE/last-skip"; }

app_running() {
    [ "$(docker container inspect -f '{{.State.Running}}' "$APP" 2>/dev/null)" = "true" ]
}

# Print how many matches are starting or running. Exit status 1 if that can't be told
# (the app is up but doesn't answer for 30 s). A stopped app counts as 0.
running_matches() {
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if ! app_running; then
            echo 0
            return 0
        fi
        _running_matches_once && return 0
        [ "$i" = 10 ] || sleep 3
    done
    return 1
}

_running_matches_once() {
    docker exec "$APP" python -c '
import json, sqlite3, urllib.request
h = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=10))
if "running_matches" in h:
    print(h["running_matches"])
else:  # an image from before /api/health had the field
    c = sqlite3.connect("file:/data/clashforces.db?mode=ro", uri=True)
    print(c.execute("SELECT COUNT(*) FROM matches WHERE status IN (\"starting\", \"running\")").fetchone()[0])
' 2>/dev/null
}

# Wait until the app container answers /api/health (up to $1 seconds, default 90).
wait_healthy() {
    local deadline=$((SECONDS + ${1:-90}))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if app_running && docker exec "$APP" python -c \
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5)' 2>/dev/null; then
            return 0
        fi
        # crash-looping (restart policy keeps bringing it back): no point waiting longer
        [ "$(docker container inspect -f '{{.RestartCount}}' "$APP" 2>/dev/null || echo 0)" -ge 3 ] && return 1
        sleep 3
    done
    return 1
}

run_app() {
    docker run -d --name "$APP" --restart unless-stopped --network web \
        --log-opt max-size=10m --log-opt max-file=3 \
        -v "$VOLUME:/data" --env-file "$BASE/app.env" "$1" > /dev/null
}

stop_app() {
    # A graceful stop lets SQLite checkpoint its WAL; docker rm -f would SIGKILL.
    docker stop -t 20 "$APP" > /dev/null 2>&1 || true
    docker rm "$APP" > /dev/null 2>&1 || true
}

# The image to use for one-off containers (backup/restore): the running app's image, else the latest build.
app_image() {
    docker container inspect -f '{{.Image}}' "$APP" 2>/dev/null || echo "clashforces:latest"
}
