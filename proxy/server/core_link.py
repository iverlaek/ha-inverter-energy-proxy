"""The seam between a facade and the core.

There are two implementations of :class:`~proxy.server.base.CoreLink`:

:class:`InProcessCore`
    Calls the driver and poller directly. This is the single-process deployment
    (and what the tests use).

:class:`RemoteCore`
    Speaks a tiny line-delimited JSON protocol over TCP to a core running in
    another container. This is what lets a facade live on its own IP.

The protocol is intentionally trivial so it can be re-implemented in any
language later::

    -> {"op": "read", "device": "goodwe", "offset": 47515, "count": 16}
    <- {"ok": true, "registers": [...]}

    -> {"op": "write", "device": "goodwe", "offset": 47517, "value": 50}
    <- {"ok": true}

    -> {"op": "snapshot", "device": "goodwe"}
    <- {"ok": true, "values": {...}}
"""

from __future__ import annotations

import json
import socket
from typing import Any

from .base import CoreError, CoreLink

DEFAULT_CORE_PORT = 8787
DEFAULT_CORE_TIMEOUT = 5.0


class InProcessCore(CoreLink):
    """A :class:`CoreLink` that talks to the driver and poller directly."""

    def __init__(self, driver: Any, poller: Any) -> None:
        self.driver = driver
        self.poller = poller
        self.device = driver.name

    def read(self, offset: int, count: int) -> list[int]:
        return self.driver.image_block(offset, count)

    def write(self, offset: int, value: int) -> None:
        for request in self.driver.decode_write(offset, value):
            self.poller.apply_write_request(request)

    def snapshot(self) -> dict[str, Any]:
        return self.driver.snapshot().serialized()

    def status(self) -> dict[str, Any]:
        return self.poller.status_dict()

    def configure(self, host: str, port: int | None = None) -> dict[str, Any]:
        self.driver.configure(host, port)  # type: ignore[attr-defined]
        return {
            "device": self.device,
            "host": getattr(self.driver, "host", host),
            "port": getattr(self.driver, "port", port),
        }

    def close(self) -> None:
        self.driver.close()


class RemoteCore(CoreLink):
    """A :class:`CoreLink` that reaches a core over TCP."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_CORE_PORT,
        *,
        device: str = "goodwe",
        timeout: float = DEFAULT_CORE_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.device = device
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("device", self.device)
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            ) as sock:
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while not buffer.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
        except OSError as err:
            raise CoreError(f"cannot reach core at {self.host}:{self.port}: {err}") from err
        if not buffer.strip():
            raise CoreError("core closed the connection without answering")
        try:
            response = json.loads(buffer.decode("utf-8").strip())
        except ValueError as err:
            raise CoreError(f"core sent a malformed answer: {err}") from err
        if not response.get("ok"):
            raise CoreError(str(response.get("error", "core refused the request")))
        return response

    # -- CoreLink ----------------------------------------------------------

    def read(self, offset: int, count: int) -> list[int]:
        response = self._request({"op": "read", "offset": offset, "count": count})
        registers = response.get("registers") or []
        return [int(value) & 0xFFFF for value in registers]

    def write(self, offset: int, value: int) -> None:
        self._request({"op": "write", "offset": offset, "value": value & 0xFFFF})

    def snapshot(self) -> dict[str, Any]:
        try:
            response = self._request({"op": "snapshot"})
        except CoreError:
            return {}
        return response.get("values", {})

    def status(self) -> dict[str, Any]:
        try:
            response = self._request({"op": "status"})
        except CoreError:
            return {}
        return response.get("status", {})

    def configure(self, host: str, port: int | None = None) -> dict[str, Any]:
        response = self._request({"op": "configure", "host": host, "port": port})
        return {key: value for key, value in response.items() if key != "ok"}
