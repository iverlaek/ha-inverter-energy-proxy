"""Tests for the core seam: in-process links and the TCP core server."""

from __future__ import annotations

import asyncio
import json

import pytest

from proxy.poller import Poller
from proxy.server.base import CoreError
from proxy.server.core_link import InProcessCore, RemoteCore
from proxy.server.core_server import CoreServer

from conftest import FakeTransport


def _link(driver) -> InProcessCore:
    return InProcessCore(driver, Poller([driver]))


# --- InProcessCore --------------------------------------------------------


def test_in_process_read_uses_the_register_image(driver):
    link = _link(driver)
    assert link.device == "goodwe"
    assert link.read(35001, 1) == [10000]
    assert link.read(99999, 2) == [0, 0]


def test_in_process_write_decodes_and_applies(driver, transport: FakeTransport):
    link = _link(driver)
    link.write(47000, 5)
    assert (47000, 5) in transport.writes


def test_in_process_snapshot_and_status(driver):
    link = _link(driver)
    snapshot = link.snapshot()
    assert snapshot["battery.soc"]["value"] == 73
    assert "devices" in link.status()


def test_in_process_close_closes_the_driver(transport: FakeTransport):
    from proxy.drivers.goodwe import GoodweDriver

    driver = GoodweDriver(transport=transport)
    _link(driver).close()
    assert transport.closed


# --- CoreServer dispatch (synchronous) ------------------------------------


def test_core_server_dispatch_ops(driver, transport: FakeTransport):
    server = CoreServer({"goodwe": _link(driver)})
    assert server.default_device == "goodwe"

    read = server._dispatch(b'{"op": "read", "offset": 35001, "count": 1}')
    assert read == {"ok": True, "registers": [10000]}

    assert server._dispatch(b'{"op": "write", "offset": 47000, "value": 5}')["ok"] is True
    assert (47000, 5) in transport.writes

    assert server._dispatch(b'{"op": "snapshot"}')["values"]
    assert "devices" in server._dispatch(b'{"op": "status"}')["status"]
    assert server._dispatch(b'{"op": "ping"}') == {"ok": True}


def test_core_server_dispatch_errors(driver):
    server = CoreServer({"goodwe": _link(driver)})
    assert server._dispatch(b"not json")["ok"] is False
    assert server._dispatch(b'{"op": "bogus"}')["ok"] is False
    assert server._dispatch(b'{"op": "read"}')["ok"] is False  # missing fields
    unknown = server._dispatch(b'{"op": "read", "device": "nope", "offset": 0, "count": 1}')
    assert unknown["ok"] is False
    assert "unknown device" in unknown["error"]
    assert server.errors == 4


def test_core_server_as_dict(driver):
    server = CoreServer({"goodwe": _link(driver)}, port=9999)
    data = server.as_dict()
    assert data["port"] == 9999
    assert data["devices"] == ["goodwe"]


# --- RemoteCore over a real TCP server ------------------------------------


def test_core_server_remote_seam(driver, transport: FakeTransport):
    link = _link(driver)

    async def scenario():
        server = CoreServer({"goodwe": link}, host="127.0.0.1", port=0)
        await server.start()
        port = server.bound_port
        loop = asyncio.get_running_loop()
        remote = RemoteCore("127.0.0.1", port, device="goodwe")
        try:
            registers = await loop.run_in_executor(None, remote.read, 35001, 1)
            await loop.run_in_executor(None, remote.write, 47000, 5)
            snapshot = await loop.run_in_executor(None, remote.snapshot)
            status = await loop.run_in_executor(None, remote.status)
            return registers, snapshot, status
        finally:
            await server.stop()

    registers, snapshot, status = asyncio.run(scenario())
    assert registers == [10000]
    assert (47000, 5) in transport.writes
    assert snapshot["battery.soc"]["value"] == 73
    assert "devices" in status


def test_remote_core_maps_server_errors(driver):
    link = _link(driver)

    async def scenario():
        server = CoreServer({"goodwe": link}, host="127.0.0.1", port=0)
        await server.start()
        port = server.bound_port
        loop = asyncio.get_running_loop()
        bad_device = RemoteCore("127.0.0.1", port, device="nope")
        good = RemoteCore("127.0.0.1", port, device="goodwe")

        def expect_error(fn, *args):
            with pytest.raises(CoreError):
                fn(*args)

        try:
            await loop.run_in_executor(None, expect_error, bad_device.read, 0, 1)
            await loop.run_in_executor(
                None, expect_error, good._request, {"op": "bogus"}
            )
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_remote_core_snapshot_and_status_tolerate_failure():
    # Nothing is listening on this port, so the convenience wrappers return empty.
    remote = RemoteCore("127.0.0.1", 9, device="goodwe", timeout=0.2)
    assert remote.snapshot() == {}
    assert remote.status() == {}
