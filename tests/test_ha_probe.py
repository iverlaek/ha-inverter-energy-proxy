"""Tests for the dependency-free probes in the HA integration."""

from __future__ import annotations

import importlib.util
import json
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


def test_configured_device_host_reads_core_link_status():
    status = {"device": {"name": "goodwe", "host": "192.168.20.13", "port": 8899}}
    assert probe.configured_device_host(status) == "192.168.20.13"


class _FakeResponse:
    """Minimal stand-in for an urllib HTTP response."""

    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def test_proxy_control_posts_bearer_token(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _FakeResponse({"ok": True, "command": "start"})

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.proxy_control("http://192.168.20.58:8790/", "dummy", "start")

    assert result == {"ok": True, "command": "start"}
    assert captured["url"] == "http://192.168.20.58:8790/command"
    assert json.loads(captured["body"]) == {"command": "start"}
    assert captured["headers"]["Authorization"] == "Bearer dummy"


def test_proxy_control_returns_none_when_service_rejects(monkeypatch):
    def fake_urlopen(_request, timeout=None):
        return _FakeResponse({"ok": False, "error": "unauthorized"})

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.proxy_control("http://host:8790", "dummy", "stop")
    assert result == {"ok": False, "error": "unauthorized"}


def test_proxy_control_status_reads_state(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return _FakeResponse({"ok": True, "containers": []})

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.proxy_control_status("http://host:8790", "dummy")

    assert result == {"ok": True, "containers": []}
    assert captured["url"] == "http://host:8790/status"
    assert captured["method"] == "GET"
