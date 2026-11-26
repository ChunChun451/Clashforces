#!/bin/bash
# Deploy the latest origin/main. Builds first and only swaps containers if the build works,
# then health-checks the new container and rolls back to the previous image if it fails.
# Skips (exit 0) while a match is starting or running. FORCE=1 deploys anyway.
# Data stays in the clashforces-data volume. Everything is logged to /opt/clashforces/deploy.log.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

FORCE="${FORCE:-0}"
WHO="${DEPLOY_SOURCE:-manual}"

exec 9> "$STATE/deploy.lock"
if ! flock -n 9; then
    echo "Another deploy, backup restore or apply is running; skipping."
    exit 0
fi

# Exit 0 (skip) if a match is live, unless FORCE=1.
check_idle() {
    [ "$FORCE" = 1 ] && return 0
    local n
    if ! n="$(running_matches)"; then
        log_once "unknown-$1" "deploy($WHO): skipped $1: the app did not answer /api/health, so it can't tell whether a match is live (FORCE=1 to deploy anyway)"
        exit 0
    fi
    if [ "$n" -gt 0 ]; then
        log_once "live-$1" "deploy($WHO): skipped $1: $n match(es) running"
        exit 0
    fi
}

cd "$SRC"
git fetch -q origin main
target="$(git rev-parse origin/main)"
short="${target:0:8}"
check_idle "$short"

# Build, unless a previous run already built this commit and only the swap was skipped.
if [ "$(docker image inspect -f '{{ index .Config.Labels "org.clashforces.commit" }}' clashforces:new 2>/dev/null)" != "$target" ]; then
    git reset -q --hard "$target"
    log "deploy($WHO): building $short ($(git log -1 --format=%s | cut -c1-70))"
    if ! docker build -q -t clashforces:new --label "org.clashforces.commit=$target" . > "$STATE/build.log" 2>&1; then
        log "deploy($WHO): BUILD FAILED for $short; the running app is unchanged. Last lines:"
        tail -n 15 "$STATE/build.log" | sed 's/^/    /' | tee -a "$LOG"
        echo "$target build" > "$STATE/failed-commit"
        exit 1
    fi
fi

check_idle "$short"   # a match may have started during the build

old_image="$(docker container inspect -f '{{.Image}}' "$APP" 2>/dev/null || true)"
[ -n "$old_image" ] || old_image="$(docker image inspect -f '{{.Id}}' clashforces:latest 2>/dev/null || true)"
[ -n "$old_image" ] && docker tag "$old_image" clashforces:previous

log "deploy($WHO): swapping in $short"
stop_app
run_app clashforces:new

if wait_healthy 90; then
    docker tag clashforces:new clashforces:latest
    docker rmi clashforces:new > /dev/null 2>&1 || true
    echo "$target" > "$STATE/deployed-commit"
    rm -f "$STATE/failed-commit"
    clear_skip
    docker image prune -f > /dev/null 2>&1 || true
    log "deploy($WHO): OK, $short is live"
    exit 0
fi

log "deploy($WHO): $short FAILED its health check. Last log lines:"
docker logs --tail 20 "$APP" 2>&1 | sed 's/^/    /' | tee -a "$LOG"
echo "$target health" > "$STATE/failed-commit"
docker rmi -f clashforces:new > /dev/null 2>&1 || true
stop_app
if [ -z "$old_image" ]; then
    log "deploy($WHO): no previous image to roll back to; the app is DOWN"
    exit 1
fi
run_app clashforces:previous
if wait_healthy 90; then
    log "deploy($WHO): rolled back to the previous image; the app is up"
else
    log "deploy($WHO): ROLLBACK ALSO FAILED its health check; the app is DOWN"
fi
exit 1
