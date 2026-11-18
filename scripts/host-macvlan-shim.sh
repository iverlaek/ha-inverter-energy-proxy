#!/usr/bin/env bash
#
# Let this host reach the macvlan containers.
#
# macvlan deliberately blocks traffic between a container and its parent host,
# so a consumer running on this machine (for example Home Assistant) cannot talk
# to a facade by its LAN IP. A macvlan "shim" interface on the host restores that
# path without giving up the per-facade IPs.
#
# Run it again after changing IPs in .env; it is idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PARENT="${LAN_PARENT:-enp3s0}"
SHIM="${SHIM_NAME:-mv-shim}"
SHIM_IP="${SHIM_IP:-}"
IPS="${CORE_IP:-} ${GOODWE_FACADE_IP:-} ${SOLAREDGE_FACADE_IP:-} ${ENPHASE_FACADE_IP:-}"

if [ -z "$SHIM_IP" ]; then
  echo "SHIM_IP is not set in .env; skipping host shim." >&2
  exit 0
fi

if ! ip link show "$SHIM" >/dev/null 2>&1; then
  sudo ip link add "$SHIM" link "$PARENT" type macvlan mode bridge
fi
sudo ip addr replace "${SHIM_IP}/32" dev "$SHIM"
sudo ip link set "$SHIM" up

for ip in $IPS; do
  [ -n "$ip" ] || continue
  sudo ip route replace "$ip/32" dev "$SHIM"
done

echo "host shim $SHIM up at $SHIM_IP; routes: $IPS"
