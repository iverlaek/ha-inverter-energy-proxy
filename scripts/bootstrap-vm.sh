#!/usr/bin/env bash
# Bootstrap a fresh Debian/Ubuntu VM as a Goodwe Inverter Proxy host.
#
# Run as a normal user with sudo (or pass PROXY_USER when invoked with sudo):
#
#   curl -fsSL https://raw.githubusercontent.com/iverlaek/ha-inverter-energy-proxy/main/scripts/bootstrap-vm.sh \
#     | sudo env PROXY_USER="$USER" GOODWE_DONGLE_HOST=192.168.20.13 bash
#
# The script installs Docker/Compose, clones the repo, generates the lifecycle
# token outside the repo, installs the cron watchdog, starts the stack, and
# verifies the unauthenticated health endpoint. No token is printed or stored
# in the repository. The VM's static IP must be configured separately (for
# example with a DHCP reservation or cloud-init).

set -euo pipefail

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

TARGET_USER=${PROXY_USER:-${SUDO_USER:-$(id -un)}}
[ "$TARGET_USER" != "root" ] || die "run as a normal user with sudo, or set PROXY_USER"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)
[ -n "${TARGET_HOME:-}" ] && [ -d "$TARGET_HOME" ] || die "no home directory for user $TARGET_USER"

if [ "$(id -u)" -eq 0 ]; then
  ROOT_RUN=()
  AS_TARGET=(runuser -u "$TARGET_USER" --)
elif command -v sudo >/dev/null 2>&1; then
  ROOT_RUN=(sudo)
  AS_TARGET=(sudo -u "$TARGET_USER" --)
else
  die "sudo is required to install packages"
fi

REPO_URL=${PROXY_REPO_URL:-https://github.com/iverlaek/ha-inverter-energy-proxy.git}
REPO_DIR=${PROXY_REPO_DIR:-$TARGET_HOME/ha-inverter-energy-proxy}
GOODWE_HOST=${GOODWE_DONGLE_HOST:-}
BIN_DIR=$TARGET_HOME/.local/bin
WATCHDOG=$BIN_DIR/proxy-control-watchdog.sh
TOKEN_FILE=$TARGET_HOME/.secrets/proxy_control_token
LOG_FILE=$TARGET_HOME/.local/log/proxy-control.log
CONTROL_PORT=8790

root_run() { "${ROOT_RUN[@]}" "$@"; }
as_target() { "${AS_TARGET[@]}" "$@"; }

# Run a command with Docker available, even if the docker group was only just
# added and is not active in this shell yet.
with_docker_group() {
  if as_target docker info >/dev/null 2>&1; then
    as_target "$@"
    return
  fi
  command -v sg >/dev/null 2>&1 || die "docker group is not active and 'sg' is unavailable; log out/in and rerun"
  local quoted
  quoted=$(printf '%q ' "$@")
  as_target sg docker -c "$quoted"
}

log "Checking distribution"
[ -r /etc/os-release ] || die "/etc/os-release not found; supported distributions: Debian, Ubuntu"
# shellcheck disable=SC1091
. /etc/os-release
case "$ID" in
  debian|ubuntu) ;;
  *) die "unsupported distribution '$ID'; install git, curl, python3, cron and Docker Compose v2 manually" ;;
esac

log "Installing packages"
root_run apt-get update
root_run apt-get install -y ca-certificates curl gnupg git python3 cron

if ! command -v docker >/dev/null 2>&1; then
  log "Adding the Docker apt repository"
  root_run install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" \
    | root_run gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  root_run chmod a+r /etc/apt/keyrings/docker.gpg
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$VERSION_CODENAME" \
    | root_run tee /etc/apt/sources.list.d/docker.list >/dev/null
  root_run apt-get update
fi
root_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! id -nG "$TARGET_USER" | grep -qw docker; then
  log "Adding $TARGET_USER to the docker group"
  root_run usermod -aG docker "$TARGET_USER"
fi

log "Fetching the repository"
if [ -d "$REPO_DIR/.git" ]; then
  as_target git -C "$REPO_DIR" pull --ff-only
else
  [ ! -e "$REPO_DIR" ] || die "$REPO_DIR exists but is not a git checkout"
  as_target git clone "$REPO_URL" "$REPO_DIR"
fi

if [ -n "$GOODWE_HOST" ]; then
  log "Setting the GoodWe fallback host to $GOODWE_HOST"
  as_target python3 - "$REPO_DIR/config/inverter_proxy.yaml" "$GOODWE_HOST" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
host = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
changed = False
for index, line in enumerate(lines):
    if "# the real GoodWe dongle" in line:
        updated, count = re.subn(r"(host:\s+)\S+", lambda m: m.group(1) + host, line, count=1)
        if count and updated != line:
            lines[index] = updated
            changed = True
if changed:
    path.write_text("".join(lines), encoding="utf-8")
PY
fi

log "Preparing the lifecycle token and watchdog"
as_target python3 - "$TOKEN_FILE" <<'PY'
import os
import secrets
import sys

path = sys.argv[1]
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
if not os.path.exists(path) or os.path.getsize(path) == 0:
    temporary = f"{path}.tmp.{os.getpid()}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secrets.token_hex(32) + "\n")
    os.replace(temporary, path)
os.chmod(path, 0o600)
PY

as_target mkdir -p "$BIN_DIR" "$(dirname "$LOG_FILE")"
as_target install -m 0755 "$REPO_DIR/scripts/proxy-control-watchdog.sh" "$WATCHDOG"

CRON_TMP=$(mktemp)
trap 'rm -f "$CRON_TMP"' EXIT
as_target crontab -l >"$CRON_TMP" 2>/dev/null || true
if ! grep -Fq "$WATCHDOG" "$CRON_TMP"; then
  printf '@reboot %s\n* * * * * %s\n' "$WATCHDOG" "$WATCHDOG" >>"$CRON_TMP"
  as_target crontab "$CRON_TMP"
fi

log "Starting the proxy stack"
with_docker_group docker compose -f "$REPO_DIR/docker-compose.host.yml" up -d --build

log "Starting the lifecycle API watchdog"
with_docker_group "$WATCHDOG"

log "Waiting for the lifecycle API"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS -m 2 "http://127.0.0.1:$CONTROL_PORT/health" >/dev/null 2>&1; then
    echo "health: ok"
    healthy=1
    break
  fi
  sleep 1
done
[ "$healthy" = 1 ] || die "lifecycle API did not become healthy; check $LOG_FILE"

with_docker_group docker compose -f "$REPO_DIR/docker-compose.host.yml" ps

cat <<EOF

Bootstrap complete.

  Repository: $REPO_DIR
  Token file: $TOKEN_FILE
  Health URL: http://<this-host-ip>:$CONTROL_PORT/health

Copy the token into the Goodwe Inverter Proxy entry's Configure panel in each
Home Assistant instance, then add/configure that integration as described in
docs/ha-integration.md. If Docker commands fail in this shell, log out and back
in once to refresh the docker group; cron and new sessions already have it.
EOF
