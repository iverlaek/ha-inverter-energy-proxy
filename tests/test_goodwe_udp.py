"""Framing and parsing tests for the GoodWe UDP transport (no network access).

These cases are ported from the ``ha-goodwe-10k-ET-tou`` integration, where they
guard a client that has been talking to a real GW10K-ET dongle for months. They
cover the dongle's nasty habits: oversized datagrams with concatenated frames,
fragments, foreign answers, and Modbus exceptions.
"""

from __future__ import annotations

import pytest

from proxy.transport import goodwe_udp as udp

ADDR = 0xF7


def _frame(payload: bytes) -> bytes:
    """Wrap ``payload`` (cmd..data) in the 0xAA55 header and append the CRC."""
    body = bytes([ADDR]) + payload
    crc = udp.crc16(body)
    return b"\xaa\x55" + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _exception_frame(cmd: int, code: int) -> bytes:
    body = bytes([ADDR, cmd | 0x80, code])
    crc = udp.crc16(body)
    return b"\xaa\x55" + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def test_build_request_layout_and_crc():
    frame = udp._build_request(ADDR, udp.READ_CMD, 47515, 16)
    assert len(frame) == 8
    assert frame[0] == ADDR
    assert frame[1] == udp.READ_CMD
    assert frame[2:4] == (47515).to_bytes(2, "big")
    assert frame[4:6] == (16).to_bytes(2, "big")
    assert udp.crc16(frame[:6]) == int.from_bytes(frame[6:], "little")


def test_crc16_known_vector():
    # Standard Modbus CRC-16 of b"\x01\x03\x00\x00\x00\x01" is 0x0A84.
    assert udp.crc16(b"\x01\x03\x00\x00\x00\x01") == 0x0A84


def test_parse_read_response():
    data = [0x0C1E, 0x0F1E]
    payload = bytes([udp.READ_CMD, len(data) * 2])
    for reg in data:
        payload += reg.to_bytes(2, "big")
    frame, partial = udp.GoodweUdpClient._parse(_frame(payload), udp.READ_CMD, 47515, 2)
    assert frame is not None
    assert partial is False
    assert frame[5:-2] == b"\x0c\x1e\x0f\x1e"


def test_parse_oversized_datagram():
    payload = bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")
    oversized = _frame(payload) + b"\x00" * 40
    frame, partial = udp.GoodweUdpClient._parse(oversized, udp.READ_CMD, 47515, 1)
    assert frame is not None
    assert partial is False


def test_parse_fragment_is_partial():
    frame = _frame(bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big"))
    frame, partial = udp.GoodweUdpClient._parse(frame[:4], udp.READ_CMD, 47515, 1)
    assert frame is None
    assert partial is True


def test_parse_bad_crc_rejected():
    frame = bytearray(_frame(bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")))
    frame[-1] ^= 0xFF
    parsed, partial = udp.GoodweUdpClient._parse(bytes(frame), udp.READ_CMD, 47515, 1)
    assert parsed is None
    assert partial is False


def test_parse_exception_response():
    frame = _exception_frame(udp.READ_CMD, 0x02)
    with pytest.raises(udp.GoodweRejectedError):
        udp.GoodweUdpClient._parse(frame, udp.READ_CMD, 47515, 1)


def test_parse_exception_bad_crc_is_ignored():
    corrupt = bytearray(_exception_frame(udp.READ_CMD, 0x02))
    corrupt[-1] ^= 0xFF
    parsed, partial = udp.GoodweUdpClient._parse(
        bytes(corrupt), udp.READ_CMD, 47515, 1
    )
    assert parsed is None
    assert partial is False


def test_parse_exception_fragment_is_partial():
    frame = _exception_frame(udp.READ_CMD, 0x02)
    parsed, partial = udp.GoodweUdpClient._parse(frame[:5], udp.READ_CMD, 47515, 1)
    assert parsed is None
    assert partial is True


def test_parse_foreign_read_exception_does_not_fail_a_write():
    # Another client's "illegal data address" (0x83) must not abort our write.
    echo = (
        bytes([udp.WRITE_CMD]) + (47529).to_bytes(2, "big") + (0xFF9C).to_bytes(2, "big")
    )
    frame, partial = udp.GoodweUdpClient._parse(
        _exception_frame(udp.READ_CMD, 0x02) + _frame(echo),
        udp.WRITE_CMD,
        47529,
        0xFF9C,
    )
    assert frame is not None
    assert partial is False


def test_parse_foreign_write_exception_is_skipped_when_our_echo_arrives():
    echo = (
        bytes([udp.WRITE_CMD]) + (47529).to_bytes(2, "big") + (0xFF9C).to_bytes(2, "big")
    )
    frame, partial = udp.GoodweUdpClient._parse(
        _exception_frame(udp.WRITE_CMD, 0x03) + _frame(echo),
        udp.WRITE_CMD,
        47529,
        0xFF9C,
    )
    assert frame is not None
    assert partial is False


def test_parse_write_echo():
    value = 0xFF9C
    payload = (
        bytes([udp.WRITE_CMD]) + (47529).to_bytes(2, "big") + value.to_bytes(2, "big")
    )
    frame, partial = udp.GoodweUdpClient._parse(
        _frame(payload), udp.WRITE_CMD, 47529, value
    )
    assert frame is not None
    assert partial is False


def test_parse_write_echo_mismatch():
    payload = (
        bytes([udp.WRITE_CMD])
        + (47529).to_bytes(2, "big")
        + (0x0001).to_bytes(2, "big")
    )
    parsed, partial = udp.GoodweUdpClient._parse(
        _frame(payload), udp.WRITE_CMD, 47529, 0xFF9C
    )
    assert parsed is None
    assert partial is False


def test_parse_concatenated_frames_prefers_last_match():
    # Two frames that both answer a 1-register read: the dongle appends ours
    # last, after a stale one, and we must return the last match.
    stale = bytes([udp.READ_CMD, 2]) + (0x1111).to_bytes(2, "big")
    ours = bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")
    data = _frame(stale) + _frame(ours)
    frame, partial = udp.GoodweUdpClient._parse(data, udp.READ_CMD, 47515, 1)
    assert frame is not None
    assert partial is False
    assert frame[5:-2] == (0x0400).to_bytes(2, "big")


def test_parse_skips_foreign_frame_with_wrong_byte_count():
    # First frame answers a 2-register read (byte_count 4), ours a 1-register
    # read (byte_count 2); only ours may be returned.
    foreign = bytes([udp.READ_CMD, 4]) + b"\x00" * 4
    ours = bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")
    frame, partial = udp.GoodweUdpClient._parse(
        _frame(foreign) + _frame(ours), udp.READ_CMD, 47515, 1
    )
    assert frame is not None
    assert partial is False
    assert frame[5:-2] == (0x0400).to_bytes(2, "big")


def test_parse_only_foreign_frame_returns_none():
    foreign = bytes([udp.READ_CMD, 4]) + b"\x00" * 4
    frame, partial = udp.GoodweUdpClient._parse(_frame(foreign), udp.READ_CMD, 47515, 1)
    assert frame is None
    assert partial is False


def test_parse_unknown_command_is_ignored():
    payload = bytes([0x42, 2]) + b"\x00\x00"
    frame, partial = udp.GoodweUdpClient._parse(_frame(payload), udp.READ_CMD, 47515, 1)
    assert frame is None
    assert partial is False


def test_read_registers_extracts_payload(monkeypatch):
    client = udp.GoodweUdpClient("127.0.0.1")
    payload = bytes([udp.READ_CMD, 4]) + (0x0102).to_bytes(2, "big") + (0xFFFE).to_bytes(
        2, "big"
    )
    monkeypatch.setattr(
        client, "_transceive", lambda cmd, offset, value: _frame(payload)
    )
    assert client.read_registers(47515, 2) == [0x0102, 0xFFFE]


def test_read_registers_rejects_short_payload(monkeypatch):
    client = udp.GoodweUdpClient("127.0.0.1")
    payload = bytes([udp.READ_CMD, 2]) + (0x0102).to_bytes(2, "big")
    monkeypatch.setattr(
        client, "_transceive", lambda cmd, offset, value: _frame(payload)
    )
    with pytest.raises(udp.GoodweProtocolError):
        client.read_registers(47515, 2)


def test_write_register_masks_to_16_bits(monkeypatch):
    client = udp.GoodweUdpClient("127.0.0.1")
    seen = {}

    def fake(cmd, offset, value):
        seen["cmd"] = cmd
        seen["offset"] = offset
        seen["value"] = value
        return _frame(bytes([udp.WRITE_CMD]) + offset.to_bytes(2, "big") + (value & 0xFFFF).to_bytes(2, "big"))

    monkeypatch.setattr(client, "_transceive", fake)
    client.write_register(47517, 0x1_0064)
    assert seen == {"cmd": udp.WRITE_CMD, "offset": 47517, "value": 0x0064}


# Real datagram captured from the Wi-Fi/LAN Kit 2.0 dongle: 296 bytes with
# several concatenated frames, the answer to our 16-register read being the
# final 39-byte frame.
_OVERSIZED = bytes.fromhex(
    "aa 55 f7 03 fa 1a 0b 10 09 3a 11 08 b9 00 03 00 00 00 42 09 6e 00 02 00 "
    "00 00 1f 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 02 02 09 "
    "1f 00 05 13 8d 00 00 00 37 09 3d 00 47 13 8c 00 00 06 90 09 1a 00 04 13 "
    "8b 00 00 00 31 00 01 00 00 06 ea ff ff ff cb 00 00 00 00 00 00 00 00 09 "
    "22 00 03 13 8c 00 01 00 00 00 1e 09 41 00 13 13 8c 00 01 00 00 01 3e 09 "
    "20 00 06 13 8b 00 01 00 00 00 52 00 00 00 49 00 00 05 39 00 00 00 02 00 "
    "00 01 99 00 00 05 86 00 06 01 05 00 00 00 fa 00 00 1d f5 0e fa 0a db 00 "
    "41 00 00 07 04 00 02 00 00 00 14 00 01 00 00 00 00 00 00 00 03 0f 6e 00 "
    "00 00 01 00 03 89 af 00 00 66 6d 00 2c 00 00 ea ff 00 ba 00 06 8f b3 00 "
    "5d 00 01 9f 1e 00 b1 00 01 86 4d 00 2e 00 06 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 03 00 00 40 00 03 06 f6 00 00 23 03 aa 55 f7 03 20 03 1e "
    "04 0f ff ba ff 01 09 00 0e 00 00 00 00 7f 0a 1e 00 06 00 00 00 40 01 00 "
    "06 00 ff 9c ff 7f d9 07"
)


def test_parse_real_oversized_datagram_returns_our_frame():
    frame, partial = udp.GoodweUdpClient._parse(_OVERSIZED, udp.READ_CMD, 47515, 16)
    assert frame is not None
    assert partial is False
    assert frame[3] == udp.READ_CMD
    assert frame[4] == 32
    assert udp.crc16(frame[2:-2]) == int.from_bytes(frame[-2:], "little")
    registers = [int.from_bytes(frame[5 + i * 2 : 7 + i * 2], "big") for i in range(16)]
    assert registers == [
        0x031E,
        0x040F,
        0xFFBA,
        0xFF01,
        0x0900,
        0x0E00,
        0x0000,
        0x007F,
        0x0A1E,
        0x0006,
        0x0000,
        0x0040,
        0x0100,
        0x0600,
        0xFF9C,
        0xFF7F,
    ]
