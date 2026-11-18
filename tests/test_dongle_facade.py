"""Tests for the GoodWe dongle facade: framing, handling and the UDP server."""

from __future__ import annotations

import asyncio

from proxy.server.goodwe_dongle import (
    MAX_READ_REGISTERS,
    GoodweDongleServer,
    build_exception,
    build_read_response,
    build_write_response,
)
from proxy.transport.goodwe_udp import (
    READ_CMD,
    WRITE_CMD,
    GoodweUdpClient,
    _build_request,
    crc16,
)

from conftest import FakeCore


def _registers(response: bytes) -> list[int]:
    count = response[4] // 2
    return [int.from_bytes(response[5 + i * 2 : 7 + i * 2], "big") for i in range(count)]


# --- framing --------------------------------------------------------------


def test_build_read_response():
    response = build_read_response(0xF7, [0x1234, 0xABCD])
    assert response[:5] == bytes([0xAA, 0x55, 0xF7, READ_CMD, 4])
    assert response[5:9] == bytes([0x12, 0x34, 0xAB, 0xCD])
    assert crc16(response[2:-2]) == int.from_bytes(response[-2:], "little")


def test_build_write_response_echoes_the_request():
    response = build_write_response(0xF7, 47517, 50)
    assert len(response) == 10
    assert response[:4] == bytes([0xAA, 0x55, 0xF7, WRITE_CMD])
    assert response[4:6] == (47517).to_bytes(2, "big")
    assert response[6:8] == (50).to_bytes(2, "big")
    assert crc16(response[2:-2]) == int.from_bytes(response[-2:], "little")


def test_build_exception():
    response = build_exception(0xF7, READ_CMD, 4)
    assert response[:4] == bytes([0xAA, 0x55, 0xF7, READ_CMD | 0x80])
    assert response[4] == 4
    assert crc16(response[2:-2]) == int.from_bytes(response[-2:], "little")


# --- request handling -----------------------------------------------------


def test_handle_read_answers_from_the_core(fake_core):
    server = GoodweDongleServer(fake_core)
    response = server.handle(_build_request(0xF7, READ_CMD, 47000, 1))
    assert response is not None
    assert _registers(response) == [3]
    assert server.stats.reads == 1
    assert server.stats.requests == 1


def test_handle_read_multiple_registers(fake_core):
    server = GoodweDongleServer(fake_core)
    response = server.handle(_build_request(0xF7, READ_CMD, 47515, 2))
    assert _registers(response) == [0x0200, 0x0600]


def test_handle_write_reaches_the_core(fake_core):
    server = GoodweDongleServer(fake_core)
    response = server.handle(_build_request(0xF7, WRITE_CMD, 47517, 50))
    assert response[:4] == bytes([0xAA, 0x55, 0xF7, WRITE_CMD])
    assert fake_core.writes == [(47517, 50)]
    assert server.stats.writes == 1


def test_handle_rejects_a_bad_crc(fake_core):
    server = GoodweDongleServer(fake_core)
    frame = bytearray(_build_request(0xF7, READ_CMD, 47000, 1))
    frame[-1] ^= 0xFF
    assert server.handle(bytes(frame)) is None
    assert server.stats.bad_frames == 1


def test_handle_rejects_a_short_frame(fake_core):
    server = GoodweDongleServer(fake_core)
    assert server.handle(b"\xaa\x55\xf7") is None
    assert server.stats.bad_frames == 1


def test_handle_unknown_command_is_an_illegal_function(fake_core):
    server = GoodweDongleServer(fake_core)
    response = server.handle(_build_request(0xF7, 0x04, 0, 0))
    assert response[3] == 0x84
    assert response[4] == 1
    assert server.stats.rejected == 1


def test_handle_read_count_bounds(fake_core):
    server = GoodweDongleServer(fake_core)
    too_many = server.handle(_build_request(0xF7, READ_CMD, 0, MAX_READ_REGISTERS + 1))
    assert too_many[3] == READ_CMD | 0x80
    assert too_many[4] == 3  # illegal data value
    zero = server.handle(_build_request(0xF7, READ_CMD, 0, 0))
    assert zero[4] == 3


def test_handle_read_core_error_becomes_a_slave_failure():
    server = GoodweDongleServer(FakeCore(fail=True))
    response = server.handle(_build_request(0xF7, READ_CMD, 47000, 1))
    assert response[3] == READ_CMD | 0x80
    assert response[4] == 4
    assert server.stats.errors == 1


def test_handle_write_core_error_becomes_a_slave_failure():
    server = GoodweDongleServer(FakeCore(fail=True))
    response = server.handle(_build_request(0xF7, WRITE_CMD, 47000, 1))
    assert response[3] == WRITE_CMD | 0x80
    assert response[4] == 4
    assert server.stats.errors == 1


def test_handle_write_rejected_value():
    class RejectingCore(FakeCore):
        def write(self, offset: int, value: int) -> None:
            raise ValueError("cannot apply")

    server = GoodweDongleServer(RejectingCore())
    response = server.handle(_build_request(0xF7, WRITE_CMD, 47000, 1))
    assert response[3] == WRITE_CMD | 0x80
    assert response[4] == 3
    assert server.stats.rejected == 1


def test_handle_records_clients(fake_core):
    server = GoodweDongleServer(fake_core)
    server.handle(_build_request(0xF7, READ_CMD, 47000, 1), addr=("10.0.0.5", 1234))
    assert server.stats.clients == {"10.0.0.5:1234"}


def test_stats_as_dict(fake_core):
    server = GoodweDongleServer(fake_core)
    server.handle(_build_request(0xF7, READ_CMD, 47000, 1), addr=("10.0.0.5", 1))
    data = server.stats.as_dict()
    assert data["requests"] == 1
    assert data["reads"] == 1
    assert data["clients"] == ["10.0.0.5:1"]


# --- the UDP server -------------------------------------------------------


def test_facade_serves_reads_and_writes_over_udp(fake_core):
    server = GoodweDongleServer(fake_core, host="127.0.0.1", port=0)

    async def scenario():
        await server.start()
        port = server.bound_port
        loop = asyncio.get_running_loop()
        client = GoodweUdpClient("127.0.0.1", port, timeout=2.0, retries=1)
        try:
            registers = await loop.run_in_executor(
                None, client.read_registers, 47515, 2
            )
            await loop.run_in_executor(None, client.write_register, 47517, 50)
            return registers
        finally:
            await server.stop()

    assert asyncio.run(scenario()) == [0x0200, 0x0600]
    assert fake_core.writes == [(47517, 50)]
    assert server.bound_port is None
