#!/usr/bin/env bash
#
# Build and start the whole proxy stack, and set up the host shim so this machine
# can reach the facades. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found; copying .env.example. Review the IPs before using this for real." >&2
  cp .env.example .env
fi

./scripts/host-macvlan-shim.sh
docker compose up -d --build
echo
docker compose ps
