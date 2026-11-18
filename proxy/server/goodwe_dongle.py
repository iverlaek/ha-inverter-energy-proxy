"""The GoodWe dongle facade: the proxy pretending to be a GoodWe inverter.

A consumer that speaks the GoodWe UDP protocol (for example the GoodWe Home
Assistant integration) points at the facade instead of at the real dongle. Reads
are answered from the core's cached register image, so the physical dongle is
never touched by the consumer. Writes are handed to the core, which decodes them
into canonical writes and translates them to whatever the real device —
possibly another brand — needs.

The facade is stateless: it holds a :class:`~proxy.server.base.CoreLink`, never a
driver. That is what lets it run in its own container, on its own IP.

Wire format (identical to the real dongle):

* request  ``[addr][cmd][off_hi][off_lo][val_hi][val_lo][crc_lo][crc_hi]``
* read answer  ``AA 55 [addr][03][byte_count][data...][crc_lo][crc_hi]``
* write answer ``AA 55 [addr][06][off_hi][off_lo][val_hi][val_lo][crc_lo][crc_hi]``
* exception    ``AA 55 [addr][cmd|80][code][crc_lo][crc_hi]``

The CRC is the standard Modbus CRC-16 over the frame without the two leading
``0xAA 0x55`` bytes and without the CRC itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..transport.goodwe_udp import (
    DEFAULT_COMM_ADDR,
    DEFAULT_PORT,
    READ_CMD,
    WRITE_CMD,
    crc16,
)
from .base import CoreError, CoreLink, Server, ServerStats

_HEADER = b"\xaa\x55"
#: The real dongle refuses reads longer than 125 registers (0x7D).
MAX_READ_REGISTERS = 125

_ILLEGAL_FUNCTION = 0x01
_ILLEGAL_DATA_VALUE = 0x03
_SLAVE_DEVICE_FAILURE = 0x04


def build_read_response(addr: int, registers: list[int]) -> bytes:
    """Build a Modbus read answer carrying *registers*."""
    frame = bytearray([0xAA, 0x55, addr & 0xFF, READ_CMD, len(registers) * 2])
    for value in registers:
        frame.append((value >> 8) & 0xFF)
        frame.append(value & 0xFF)
    checksum = crc16(frame[2:])
    frame.append(checksum & 0xFF)
    frame.append((checksum >> 8) & 0xFF)
    return bytes(frame)


def build_write_response(addr: int, offset: int, value: int) -> bytes:
    """Build a Modbus write answer echoing the offset and value."""
    frame = bytearray(
        [
            0xAA,
            0x55,
            addr & 0xFF,
            WRITE_CMD,
            (offset >> 8) & 0xFF,
            offset & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
    )
    checksum = crc16(frame[2:])
    frame.append(checksum & 0xFF)
    frame.append((checksum >> 8) & 0xFF)
    return bytes(frame)


def build_exception(addr: int, cmd: int, code: int) -> bytes:
    """Build a Modbus exception answer."""
    frame = bytearray([0xAA, 0x55, addr & 0xFF, (cmd | 0x80) & 0xFF, code & 0xFF])
    checksum = crc16(frame[2:])
    frame.append(checksum & 0xFF)
    frame.append((checksum >> 8) & 0xFF)
    return bytes(frame)


class _DongleProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: "GoodweDongleServer") -> None:
        self.server = server
        self.transport: asyncio.DatagramTransport | None = None
        self.tasks: set[asyncio.Task[None]] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # The core may be reached over the network, which blocks; answer off the
        # event loop so other datagrams keep flowing.
        task = asyncio.ensure_future(self._respond(data, addr))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _respond(self, data: bytes, addr: tuple[str, int]) -> None:
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(None, self.server.handle, data, addr)
        except Exception as err:  # noqa: BLE001 - never let a datagram kill the loop
            self.server.logger.warning("dongle request failed: %s", err)
            return
        if response and self.transport is not None:
            self.transport.sendto(response, addr)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        self.server.logger.debug("dongle socket error: %s", exc)


class GoodweDongleServer(Server):
    """A UDP server that emulates a GoodWe dongle for the proxy."""

    name = "goodwe_dongle"

    def __init__(
        self,
        core: CoreLink,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        comm_addr: int = DEFAULT_COMM_ADDR,
        logger: logging.Logger | None = None,
    ) -> None:
        self.core = core
        self.host = host
        self.port = port
        self.comm_addr = comm_addr
        self.logger = logger or logging.getLogger(__name__)
        self.stats = ServerStats()
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _DongleProtocol | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _DongleProtocol(self),
            local_addr=(self.host, self.port),
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        self.logger.info("goodwe dongle facade listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._protocol = None

    @property
    def bound_port(self) -> int | None:
        if self._transport is None:
            return None
        sock = self._transport.get_extra_info("socket")
        return sock.getsockname()[1] if sock is not None else None

    # -- request handling --------------------------------------------------

    def handle(self, data: bytes, addr: tuple[str, int] | None = None) -> bytes | None:
        """Answer one request datagram, or ``None`` when it is not ours."""
        self.stats.requests += 1
        if addr is not None:
            self.stats.clients.add(f"{addr[0]}:{addr[1]}")
        request = self._parse_request(data)
        if request is None:
            self.stats.bad_frames += 1
            return None
        comm_addr, cmd, offset, value = request

        if cmd == READ_CMD:
            return self._handle_read(comm_addr, offset, value)
        if cmd == WRITE_CMD:
            return self._handle_write(comm_addr, offset, value)
        self.stats.rejected += 1
        return build_exception(comm_addr, cmd, _ILLEGAL_FUNCTION)

    def _handle_read(self, comm_addr: int, offset: int, count: int) -> bytes:
        if count < 1 or count > MAX_READ_REGISTERS:
            self.stats.rejected += 1
            return build_exception(comm_addr, READ_CMD, _ILLEGAL_DATA_VALUE)
        try:
            registers = self.core.read(offset, count)
        except CoreError as err:
            self.stats.errors += 1
            self.logger.warning("core read failed: %s", err)
            return build_exception(comm_addr, READ_CMD, _SLAVE_DEVICE_FAILURE)
        self.stats.reads += 1
        return build_read_response(comm_addr, registers)

    def _handle_write(self, comm_addr: int, offset: int, value: int) -> bytes:
        self.stats.writes += 1
        try:
            self.core.write(offset, value)
        except CoreError as err:
            self.stats.errors += 1
            self.logger.warning("core write failed: %s", err)
            return build_exception(comm_addr, WRITE_CMD, _SLAVE_DEVICE_FAILURE)
        except Exception as err:  # noqa: BLE001 - a bad write must not kill the server
            self.stats.rejected += 1
            self.logger.warning("cannot apply write %s=%s: %s", offset, value, err)
            return build_exception(comm_addr, WRITE_CMD, _ILLEGAL_DATA_VALUE)
        return build_write_response(comm_addr, offset, value)

    @staticmethod
    def _parse_request(data: bytes) -> tuple[int, int, int, int] | None:
        if len(data) < 8:
            return None
        frame = data[:8]
        if crc16(frame[:6]) != int.from_bytes(frame[6:8], "little"):
            return None
        comm_addr = frame[0]
        cmd = frame[1]
        offset = (frame[2] << 8) | frame[3]
        value = (frame[4] << 8) | frame[5]
        return comm_addr, cmd, offset, value
