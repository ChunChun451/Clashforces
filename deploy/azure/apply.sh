#!/bin/bash
# Bring a Clashforces VM up to the current layout. Idempotent: run it as often as you like.
#
# On a new VM (cloud-init runs it): installs everything and starts the app and Caddy.
# On an existing VM: installs the helper scripts, systemd timers and Caddyfile, fixes
# ALLOWED_ORIGINS. It never recreates the VM, never touches the clashforces-data volume
# and never changes SECRET_KEY. Run as root, e.g. from Azure Cloud Shell (see README):
#
#   az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --scripts \
#     "curl -fsSL https://raw.githubusercontent.com/ChunChun451/Clashforces/main/deploy/azure/apply.sh -o /tmp/apply.sh && DOMAIN=clashforces.duckdns.org bash /tmp/apply.sh"
#
# Settings (environment, else the saved /opt/clashforces/deploy.env):
#   DOMAIN         the site's name, e.g. clashforces.duckdns.org (required the first time)
#   REDIRECT_FROM  other names that 301-redirect to DOMAIN, space or comma separated
#   REF            git branch the helper scripts come from (default main)
#   SCRIPTS_DIR    install the helper scripts from this folder instead of git (for testing)
set -euo pipefail

BASE="${CLASHFORCES_HOME:-/opt/clashforces}"
SRC="$BASE/src"
ENV_FILE="$BASE/deploy.env"
REPO_URL="https://github.com/ChunChun451/Clashforces.git"
export HOME="${HOME:-/root}"

[ "$(id -u)" = 0 ] || { echo "apply.sh must run as root"; exit 1; }
mkdir -p "$BASE"

saved() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "'\""; }
DOMAIN="${DOMAIN:-$(saved DOMAIN || true)}"
REDIRECT_FROM="${REDIRECT_FROM-$(saved REDIRECT_FROM || true)}"
REF="${REF:-$(saved SCRIPTS_REF || true)}"
REF="${REF:-main}"

host_re='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
[ -n "$DOMAIN" ] || { echo "Set DOMAIN, e.g. DOMAIN=clashforces.duckdns.org"; exit 1; }
[[ "$DOMAIN" =~ $host_re ]] || { echo "DOMAIN is not a host name: $DOMAIN"; exit 1; }
read -r -a redirects <<< "${REDIRECT_FROM//,/ }"
for h in "${redirects[@]}"; do
    [[ "$h" =~ $host_re ]] || { echo "REDIRECT_FROM has a bad host name: $h"; exit 1; }
    [ "$h" != "$DOMAIN" ] || { echo "REDIRECT_FROM must not contain DOMAIN itself"; exit 1; }
done
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "bad REF: $REF"; exit 1; }
REDIRECT_FROM="${redirects[*]}"
printf "DOMAIN=%s\nREDIRECT_FROM='%s'\nSCRIPTS_REF=%s\n" "$DOMAIN" "$REDIRECT_FROM" "$REF" > "$ENV_FILE"

say() { echo "apply: $*"; }

# ---------- packages, swap, docker ----------
if ! command -v docker > /dev/null || ! command -v git > /dev/null; then
    say "installing docker.io and git"
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io git > /dev/null
fi
if [ -z "${CLASHFORCES_HOME:-}" ] && [ ! -f /swapfile ]; then
    # 1 GB swap so the small VM doesn't run out of memory during docker build.
    fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile > /dev/null && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    say "added 1 GB swap"
fi
command -v systemctl > /dev/null && [ -d /run/systemd/system ] && systemctl enable --now docker > /dev/null 2>&1 || true
docker network inspect web > /dev/null 2>&1 || docker network create web > /dev/null

# ---------- helper scripts ----------
[ -d "$SRC/.git" ] || git clone -q "$REPO_URL" "$SRC"
FILES=(lib.sh update.sh autodeploy.sh backup-now.sh restore.sh apply.sh
       systemd/clashforces-backup.service systemd/clashforces-backup.timer
       systemd/clashforces-autodeploy.service systemd/clashforces-autodeploy.timer)
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
if [ -n "${SCRIPTS_DIR:-}" ]; then
    for f in "${FILES[@]}"; do mkdir -p "$stage/$(dirname "$f")"; cp "$SCRIPTS_DIR/$f" "$stage/$f"; done
else
    git -C "$SRC" fetch -q origin "$REF"
    if ! git -C "$SRC" cat-file -e FETCH_HEAD:deploy/azure/lib.sh 2> /dev/null; then
        echo "Branch '$REF' has no deploy/azure/lib.sh yet. Use REF=deploy-azure until PR #2 is merged."
        exit 1
    fi
    for f in "${FILES[@]}"; do
        mkdir -p "$stage/$(dirname "$f")"
        git -C "$SRC" show "FETCH_HEAD:deploy/azure/$f" > "$stage/$f"
    done
fi
for f in lib.sh update.sh autodeploy.sh backup-now.sh restore.sh apply.sh; do
    install -m 0755 "$stage/$f" "$BASE/$f"
done
mkdir -p "$BASE/backups" "$BASE/state"
chmod 700 "$BASE/backups"
say "installed scripts from ${SCRIPTS_DIR:-git $REF} into $BASE"

# ---------- app.env (SECRET_KEY is created once and never changed) ----------
want_origins="https://$DOMAIN"
env_changed=0
if [ ! -f "$BASE/app.env" ]; then
    printf 'SECRET_KEY=%s\nALLOWED_ORIGINS=%s\n' "$(openssl rand -hex 32)" "$want_origins" > "$BASE/app.env"
    say "created app.env with a new SECRET_KEY"
elif ! grep -q '^ALLOWED_ORIGINS=' "$BASE/app.env"; then
    echo "ALLOWED_ORIGINS=$want_origins" >> "$BASE/app.env"
    env_changed=1
elif [ "$(sed -n 's/^ALLOWED_ORIGINS=//p' "$BASE/app.env" | tail -n 1)" != "$want_origins" ]; then
    sed -i "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=$want_origins#" "$BASE/app.env"
    env_changed=1
fi
chmod 600 "$BASE/app.env"

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$BASE/lib.sh"

# ---------- the app ----------
if ! docker container inspect "$APP" > /dev/null 2>&1; then
    say "no app container yet: building and starting it"
    FORCE=1 DEPLOY_SOURCE=apply "$BASE/update.sh"
else
    [ -f "$STATE/deployed-commit" ] || git -C "$SRC" rev-parse HEAD > "$STATE/deployed-commit"
    if [ "$env_changed" = 1 ]; then
        if [ "$(running_matches || echo 1)" = 0 ]; then
            image="$(app_image)"
            if stop_app && run_app "$image" && wait_healthy 90; then
                log "apply: ALLOWED_ORIGINS set to $want_origins; app restarted"
            else
                log "apply: ALLOWED_ORIGINS set to $want_origins, but the restarted app is NOT healthy; see: docker logs $APP"
            fi
        else
            log "apply: ALLOWED_ORIGINS set to $want_origins; it takes effect on the next deploy (a match is live now)"
        fi
    fi
fi

# ---------- Caddy ----------
caddy_new="$stage/Caddyfile"
{
    echo "# Written by apply.sh from $ENV_FILE. Change DOMAIN / REDIRECT_FROM there and re-run apply.sh."
    echo "$DOMAIN {"
    echo "	encode zstd gzip"
    echo "	reverse_proxy clashforces:8000"
    echo "}"
    if [ "${#redirects[@]}" -gt 0 ]; then
        echo
        joined="$(IFS=,; echo "${redirects[*]}")"
        echo "${joined//,/, } {"
        echo "	redir https://$DOMAIN{uri} permanent"
        echo "}"
    fi
} > "$caddy_new"
if ! docker run --rm -v "$caddy_new:/etc/caddy/Caddyfile:ro" caddy:2 \
        caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile > /dev/null 2> "$stage/caddy.err"; then
    say "the generated Caddyfile is invalid, leaving the current one alone:"
    cat "$stage/caddy.err"
    exit 1
fi
if ! cmp -s "$caddy_new" "$BASE/Caddyfile"; then
    [ -f "$BASE/Caddyfile" ] && cp "$BASE/Caddyfile" "$BASE/Caddyfile.bak-$(date -u +%Y%m%d-%H%M%S)"
    # Write in place: Caddy bind-mounts this one file, and a new inode would be invisible to it.
    touch "$BASE/Caddyfile"
    cat "$caddy_new" > "$BASE/Caddyfile"
    if [ "$(docker container inspect -f '{{.State.Running}}' caddy 2> /dev/null)" = "true" ]; then
        if docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile > "$stage/reload.log" 2>&1; then
            log "apply: Caddyfile updated and reloaded ($DOMAIN; redirects: ${REDIRECT_FROM:-none})"
        else
            log "apply: Caddyfile updated but Caddy's reload FAILED:"
            tail -n 5 "$stage/reload.log"
        fi
    fi
fi
if ! docker container inspect caddy > /dev/null 2>&1; then
    docker run -d --name caddy --restart unless-stopped --network web -p 80:80 -p 443:443 \
        --log-opt max-size=10m --log-opt max-file=3 \
        -v "$BASE/Caddyfile:/etc/caddy/Caddyfile:ro" -v caddy-data:/data -v caddy-config:/config caddy:2 > /dev/null
    say "started Caddy"
fi

# ---------- timers, log rotation ----------
if [ -z "${CLASHFORCES_HOME:-}" ] && [ -d /run/systemd/system ]; then
    install -m 0644 "$stage"/systemd/clashforces-* /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now clashforces-backup.timer clashforces-autodeploy.timer > /dev/null 2>&1
    cat > /etc/logrotate.d/clashforces <<EOF
$BASE/deploy.log {
    monthly
    rotate 6
    compress
    missingok
    notifempty
    copytruncate
}
EOF
else
    say "no systemd here (or a test home): timers not installed"
fi

# A first backup, so there is always at least one.
if [ -z "$(find "$BACKUPS" -maxdepth 1 -name 'clashforces-*.db.gz' -print -quit)" ]; then
    "$BASE/backup-now.sh" || say "first backup failed (see above)"
fi

# ---------- summary ----------
echo
say "done. Site: https://$DOMAIN  redirects: ${REDIRECT_FROM:-none}"
say "deployed commit: $(cut -c1-8 "$STATE/deployed-commit" 2> /dev/null || echo '?')  running matches: $(running_matches || echo '?')"
[ -f "$BASE/NO_DEPLOY" ] && say "auto-deploy is PAUSED ($BASE/NO_DEPLOY exists)"
if [ -d /run/systemd/system ] && [ -z "${CLASHFORCES_HOME:-}" ]; then
    systemctl list-timers 'clashforces-*' --no-pager | head -n 3
fi
say "newest backup: $(find "$BACKUPS" -maxdepth 1 -name 'clashforces-*.db.gz' -printf '%f\n' | sort | tail -n 1)"
