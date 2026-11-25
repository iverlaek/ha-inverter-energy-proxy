"""Tests for the dependency-free probes in the HA integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_probe_path = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ha_inverter_proxy"
    / "probe.py"
)
_spec = importlib.util.spec_from_file_location("ha_inverter_proxy_probe", _probe_path)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def _valid_response(count: int = 1) -> bytes:
    payload = b"\x27\x10" * count
    body = bytes([0xF7, 0x03, count * 2]) + payload
    checksum = probe.crc16(body)
    return probe.GOODWE_HEADER + body + bytes(
        [checksum & 0xFF, (checksum >> 8) & 0xFF]
    )


def test_read_request_has_a_valid_crc():
    request = probe.build_goodwe_read_request(47000, 1)
    assert request[:6] == bytes([0xF7, 0x03, 0xB7, 0x98, 0x00, 0x01])
    assert probe.crc16(request[:6]) == int.from_bytes(request[6:8], "little")


def test_valid_response_is_accepted_with_padding():
    padded = b"\x00\xaa" + _valid_response() + b"\xaa\x55trailing"
    assert probe._is_valid_read_response(padded, 1) is True


def test_wrong_count_is_rejected():
    assert probe._is_valid_read_response(_valid_response(2), 1) is False


def test_bad_crc_is_rejected():
    response = bytearray(_valid_response())
    response[-1] ^= 0xFF
    assert probe._is_valid_read_response(bytes(response), 1) is False


def test_configured_device_host_extracts_goodwe():
    status = {"drivers": [{"name": "solaredge", "host": "1.2.3.4"}, {"name": "goodwe", "host": "192.168.20.13"}]}
    assert probe.configured_device_host(status) == "192.168.20.13"
