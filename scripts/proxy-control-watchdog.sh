#!/bin/sh
# Keep the token-protected lifecycle API running without a lingering user login.
#
# A systemd --user unit stops when the last session for that user logs out.
# If sudo is unavailable to enable linger, run this from cron instead:
#
#   * * * * * /home/verlaek/.local/bin/proxy-control-watchdog.sh
#   @reboot /home/verlaek/.local/bin/proxy-control-watchdog.sh
#
# All settings can be overridden with environment variables; no token is stored
# in this script.

set -eu

PYTHON=${PYTHON:-/usr/bin/python3}
WORKDIR=${PROXY_CONTROL_WORKDIR:-/home/verlaek/ha-inverter-energy-proxy}
SCRIPT=${PROXY_CONTROL_SCRIPT:-$WORKDIR/scripts/proxy_control.py}
TOKEN_FILE=${PROXY_CONTROL_TOKEN_FILE:-/home/verlaek/.secrets/proxy_control_token}
LOG_FILE=${PROXY_CONTROL_LOG_FILE:-/home/verlaek/.local/log/proxy-control.log}
HOST=${PROXY_CONTROL_HOST:-0.0.0.0}
PORT=${PROXY_CONTROL_PORT:-8790}

mkdir -p "$(dirname "$LOG_FILE")"

# The bracket in [p]ython3 keeps pgrep from matching its own command line.
if pgrep -f "[p]ython3 .*proxy_control.py" >/dev/null 2>&1; then
  exit 0
fi

setsid "$PYTHON" "$SCRIPT" \
  --listen-host "$HOST" \
  --port "$PORT" \
  --token-file "$TOKEN_FILE" \
  --working-dir "$WORKDIR" \
  >>"$LOG_FILE" 2>&1 </dev/null &
