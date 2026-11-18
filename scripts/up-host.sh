#!/usr/bin/env bash
#
# Build and start the whole proxy stack on the host's own IP, without macvlan.
# No host shim is needed, because the containers share the host network. Safe to
# re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose -f docker-compose.host.yml up -d --build
echo
docker compose -f docker-compose.host.yml ps
