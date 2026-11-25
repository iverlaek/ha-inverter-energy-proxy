#!/usr/bin/env python3
"""Token-protected lifecycle control for the inverter proxy stack.

This small HTTP service runs on the proxy host (not inside a container) so it
survives stopping or removing the proxy containers. Home Assistant calls it to
provide the Start/Stop/Reboot/Shutdown buttons.

Endpoints::

    GET  /status    -> container states
    POST /command   -> {"command": "start"|"stop"|"reboot"|"shutdown"}

Authentication is ``Authorization: Bearer <token>``. The token is read from a
mode-600 file outside the repository, or from the ``PROXY_CONTROL_TOKEN``
environment variable. It is compared in constant time and is never logged.

Example systemd unit is in ``scripts/proxy-control.service``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8790
DEFAULT_COMPOSE_FILE = "docker-compose.host.yml"
MAX_BODY = 4096

# Every command is a docker compose subcommand appended to the shared flags.
COMMANDS: dict[str, list[str]] = {
    "start": ["up", "-d"],
    "stop": ["stop"],
    "reboot": ["up", "-d", "--force-recreate"],
    "restart": ["up", "-d", "--force-recreate"],
    "shutdown": ["down", "--remove-orphans"],
}

_LOCK = threading.Lock()
_TOKEN = ""
_BASE: list[str] = []


def _run(args: list[str], *, timeout: int = 180) -> tuple[int, str]:
    """Run a docker compose command and return (returncode, combined output)."""
    try:
        completed = subprocess.run(
            _BASE + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, str(err)
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output


def _status() -> dict[str, Any]:
    """Return the current state of the proxy containers."""
    code, output = _run(["ps", "--format", "json"], timeout=30)
    containers: Any = output
    if output:
        try:
            containers = json.loads(output)
        except ValueError:
            lines = []
            for line in output.splitlines():
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    lines.append(line)
            containers = lines
    return {
        "ok": code == 0,
        "containers": containers,
        "compose_file": DEFAULT_COMPOSE_FILE,
    }


def _command(name: str) -> dict[str, Any]:
    """Run a lifecycle command, guarding against concurrent calls."""
    with _LOCK:
        code, output = _run(COMMANDS[name])
    return {
        "ok": code == 0,
        "command": name,
        "returncode": code,
        "output": output[-2000:],
        "status": _status(),
    }


class Handler(BaseHTTPRequestHandler):
    """Minimal authenticated JSON API for proxy lifecycle control."""

    server_version = "InverterProxyControl/1.0"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return False
        return hmac.compare_digest(supplied[7:].strip(), _TOKEN)

    def _reject(self) -> None:
        self.log_message("unauthorized request from %s", self.client_address[0])
        self._send(401, {"ok": False, "error": "unauthorized"})

    def do_GET(self) -> None:  # noqa: N802
        """Handle the status endpoint."""
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, {"ok": True})
            return
        if not self._authorized():
            self._reject()
            return
        if self.path.split("?", 1)[0] == "/status":
            self._send(200, _status())
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        """Handle lifecycle commands."""
        if not self._authorized():
            self._reject()
            return
        if self.path.split("?", 1)[0] != "/command":
            self._send(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send(400, {"ok": False, "error": "invalid_body"})
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"ok": False, "error": "invalid_json"})
            return
        name = request.get("command") if isinstance(request, dict) else None
        if name not in COMMANDS:
            self._send(400, {"ok": False, "error": "unknown_command"})
            return
        self.log_message("command %s from %s", name, self.client_address[0])
        result = _command(str(name))
        self._send(200 if result["ok"] else 500, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        """Log without ever including request headers (which carry the token)."""
        super().log_message("%s - %s", self.client_address[0], fmt % args)


def main() -> int:
    """Parse arguments, load the token, and serve requests."""
    global _TOKEN, _BASE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--compose-file", default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--working-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()

    if args.token_file:
        _TOKEN = args.token_file.read_text(encoding="utf-8").strip()
    else:
        _TOKEN = os.environ.get("PROXY_CONTROL_TOKEN", "").strip()
    if not _TOKEN:
        parser.error("no control token: use --token-file or PROXY_CONTROL_TOKEN")

    os.chdir(args.working_dir)
    _BASE = [
        "docker",
        "compose",
        "-f",
        args.compose_file,
    ]
    server = ThreadingHTTPServer((args.listen_host, args.port), Handler)
    print(
        f"proxy control listening on {args.listen_host}:{args.port} "
        f"({args.working_dir})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
