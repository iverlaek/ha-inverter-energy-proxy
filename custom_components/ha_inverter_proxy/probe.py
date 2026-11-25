"""Dependency-free probes used by the HA inverter proxy integration.

The core control API is a line-delimited JSON socket. The GoodWe facade is a
UDP dongle emulator; a tiny read request is enough to know whether consumers
can currently get cached data from it.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

GOODWE_HEADER = b"\xaa\x55"
GOODWE_READ_CMD = 0x03
GOODWE_COMM_ADDR = 0xF7
GOODWE_PORT = 8899


def _build_crc_table() -> tuple[int, ...]:
    table: list[int] = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _build_crc_table()


def crc16(data: bytes) -> int:
    """Return the Modbus CRC-16 used by the GoodWe dongle protocol."""
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def build_goodwe_read_request(
    offset: int = 47000, count: int = 1, comm_addr: int = GOODWE_COMM_ADDR
) -> bytes:
    """Build a one-register GoodWe UDP read request."""
    frame = bytearray(
        [
            comm_addr & 0xFF,
            GOODWE_READ_CMD,
            (offset >> 8) & 0xFF,
            offset & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ]
    )
    checksum = crc16(frame)
    frame.extend([checksum & 0xFF, (checksum >> 8) & 0xFF])
    return bytes(frame)


def _is_valid_read_response(data: bytes, count: int) -> bool:
    """Check for a CRC-valid read response inside a (possibly padded) datagram."""
    expected = 7 + 2 * count
    pos = data.find(GOODWE_HEADER)
    while pos != -1:
        frame = data[pos : pos + expected]
        if (
            len(frame) == expected
            and frame[3] == GOODWE_READ_CMD
            and frame[4] == count * 2
            and crc16(frame[2:-2]) == int.from_bytes(frame[-2:], "little")
        ):
            return True
        pos = data.find(GOODWE_HEADER, pos + 1)
    return False


def goodwe_udp_alive(
    host: str,
    port: int = GOODWE_PORT,
    *,
    timeout: float = 2.0,
    retries: int = 2,
) -> bool:
    """Return True when something answers a minimal GoodWe read request."""
    request = build_goodwe_read_request()
    for _ in range(retries + 1):
        deadline = time.monotonic() + timeout
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.sendto(request, (host, port))
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
                    try:
                        data, _ = sock.recvfrom(4096)
                    except TimeoutError:
                        break
                    if _is_valid_read_response(data, 1):
                        return True
            except OSError:
                continue
    return False


def _core_request(
    host: str, port: int, request: dict[str, Any], timeout: float
) -> dict[str, Any] | None:
    """Send one request to the core control API and parse its reply."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            raw = sock.recv(8192)
    except OSError:
        return None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            response = json.loads(line)
        except ValueError:
            continue
        if isinstance(response, dict):
            return response
    return None


def ping_proxy(
    host: str, port: int, *, device: str = "goodwe", timeout: float = 3.0
) -> bool:
    """Verify that the proxy core control API is reachable."""
    response = _core_request(host, port, {"op": "ping", "device": device}, timeout)
    return bool(response and response.get("ok"))


def proxy_status(
    host: str, port: int, *, device: str = "goodwe", timeout: float = 3.0
) -> dict[str, Any] | None:
    """Read the core's status, including the driver's configured host."""
    response = _core_request(host, port, {"op": "status", "device": device}, timeout)
    if not response or not response.get("ok"):
        return None
    status = response.get("status")
    return status if isinstance(status, dict) else None


def configured_device_host(status: dict[str, Any] | None) -> str | None:
    """Extract the GoodWe host currently configured in the proxy."""
    if not status:
        return None
    for driver in status.get("drivers", []):
        if isinstance(driver, dict) and driver.get("name") == "goodwe":
            host = driver.get("host")
            return str(host) if host else None
    return None


def configure_proxy(
    host: str,
    port: int,
    device_host: str,
    device_port: int,
    *,
    device: str = "goodwe",
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Hand the original GoodWe address to the proxy core."""
    response = _core_request(
        host,
        port,
        {
            "op": "configure",
            "device": device,
            "host": device_host,
            "port": int(device_port),
        },
        timeout,
    )
    if not response or not response.get("ok"):
        return None
    return response
