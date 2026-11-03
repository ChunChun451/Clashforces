#!/bin/bash
# Run every 5 minutes by clashforces-autodeploy.timer. Deploys when origin/main has moved,
# unless /opt/clashforces/NO_DEPLOY exists. update.sh itself skips while a match is live.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

cd "$SRC"
if ! git fetch -q origin main 2> "$STATE/fetch.err"; then
    log_once "fetch-failed" "autodeploy: git fetch failed: $(head -c 200 "$STATE/fetch.err")"
    exit 0
fi
target="$(git rev-parse origin/main)"
deployed="$(cat "$STATE/deployed-commit" 2>/dev/null || git rev-parse HEAD)"
[ "$target" = "$deployed" ] && exit 0

short="${target:0:8}"
if [ -f "$BASE/NO_DEPLOY" ]; then
    log_once "paused-$short" "autodeploy: $short is waiting, but deploys are paused ($BASE/NO_DEPLOY exists)"
    exit 0
fi
read -r failed why 2> /dev/null < "$STATE/failed-commit" || true
if [ "$target" = "${failed:-}" ]; then
    # A failed health check means the code is broken: wait for a new commit.
    # A failed build may be transient (network, Docker Hub limits): retry it once an hour.
    if [ "${why:-}" != build ] || [ -z "$(find "$STATE/failed-commit" -mmin +60)" ]; then
        log_once "failed-$short" "autodeploy: $short failed before (${why:-?}); waiting for a new commit (or run update.sh by hand)"
        exit 0
    fi
fi

DEPLOY_SOURCE=auto exec "$BASE/update.sh"
