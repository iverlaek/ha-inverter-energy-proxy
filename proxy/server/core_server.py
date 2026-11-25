"""The core's TCP endpoint: how remote facades reach the core.

One :class:`CoreServer` serves every device the core knows about. A facade sends
a line of JSON and gets a line of JSON back. Blocking work (a driver read that
goes out to the real inverter) runs in a thread so the event loop keeps serving
other facades.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

from .base import CoreLink

_LOGGER = logging.getLogger(__name__)


class CoreServer:
    """A line-delimited JSON server in front of one or more cores."""

    def __init__(
        self,
        devices: Mapping[str, CoreLink],
        *,
        host: str = "0.0.0.0",
        port: int = 8787,
        default_device: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.devices = dict(devices)
        self.host = host
        self.port = port
        self.default_device = default_device or next(iter(self.devices), None)
        self.logger = logger or _LOGGER
        self._server: asyncio.AbstractServer | None = None
        self.requests = 0
        self.errors = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_client, self.host, self.port
        )
        self.logger.info(
            "core listening on %s:%s for devices %s",
            self.host,
            self.port,
            ", ".join(self.devices) or "(none)",
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def bound_port(self) -> int | None:
        if self._server is None:
            return None
        for sock in self._server.sockets or ():
            return sock.getsockname()[1]
        return None

    # -- request handling --------------------------------------------------

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, self._dispatch, line)
                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as err:  # noqa: BLE001 - never let one client kill the server
            self.logger.debug("core client %s failed: %s", peer, err)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _dispatch(self, line: bytes) -> dict[str, Any]:
        self.requests += 1
        try:
            request = json.loads(line.decode("utf-8"))
        except ValueError:
            return self._error("malformed request")
        if not isinstance(request, dict):
            return self._error("request must be an object")

        device = request.get("device") or self.default_device
        core = self.devices.get(device) if device else None
        if core is None:
            return self._error(f"unknown device {device!r}")

        op = request.get("op")
        try:
            if op == "read":
                registers = core.read(int(request["offset"]), int(request["count"]))
                return {"ok": True, "registers": registers}
            if op == "write":
                core.write(int(request["offset"]), int(request["value"]) & 0xFFFF)
                return {"ok": True}
            if op == "snapshot":
                return {"ok": True, "values": core.snapshot()}
            if op == "status":
                return {"ok": True, "status": core.status()}
            if op == "configure":
                applied = core.configure(
                    str(request["host"]), request.get("port")
                ) or {}
                return {"ok": True, **applied}
            if op == "ping":
                return {"ok": True}
            return self._error(f"unknown op {op!r}")
        except KeyError as err:
            return self._error(f"missing field {err}")
        except Exception as err:  # noqa: BLE001 - report, do not crash
            return self._error(str(err))

    def _error(self, message: str) -> dict[str, Any]:
        self.errors += 1
        return {"ok": False, "error": message}

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "devices": sorted(self.devices),
            "requests": self.requests,
            "errors": self.errors,
        }
