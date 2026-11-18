"""Tests for the wiring: factories, services and the command-line entry point."""

from __future__ import annotations

import asyncio
import json

import pytest

from proxy.battery.goodwe import GoodweBattery
from proxy.canonical import BATTERY_SOC
from proxy.config import Config, DriverConfig, ServerConfig
from proxy.core import (
    CoreService,
    EnergyProxy,
    FacadeService,
    _Service,
    build_battery,
    build_driver,
    build_local_links,
    build_remote_links,
    build_servers,
    build_service,
)
from proxy.drivers.goodwe import GoodweDriver
from proxy.drivers.solaredge import SolarEdgeDriver
from proxy.poller import Poller
from proxy.server.core_link import RemoteCore
from proxy.transport.goodwe_udp import READ_CMD, WRITE_CMD, _build_request

from conftest import FakeCore, FakeTransport, build_store


# --- factories ------------------------------------------------------------


def test_build_driver_goodwe():
    driver = build_driver(
        DriverConfig(name="goodwe", brand="goodwe", host="1.2.3.4", port=8899)
    )
    assert isinstance(driver, GoodweDriver)
    assert driver.host == "1.2.3.4"
    assert driver.enabled is True


def test_build_driver_paused_brand():
    driver = build_driver(DriverConfig(name="solaredge", brand="solaredge", enabled=False))
    assert isinstance(driver, SolarEdgeDriver)
    assert driver.enabled is False


def test_build_driver_unknown_brand_raises():
    with pytest.raises(ValueError):
        build_driver(DriverConfig(name="x", brand="x"))


def test_build_battery_uses_the_goodwe_driver():
    drivers = [build_driver(DriverConfig(name="goodwe", brand="goodwe"))]
    battery = build_battery(Config.default(), drivers)
    assert isinstance(battery, GoodweBattery)
    assert battery.control == "ems"


def test_build_battery_without_a_goodwe_driver_returns_none():
    drivers = [build_driver(DriverConfig(name="solaredge", brand="solaredge"))]
    assert build_battery(Config.default(), drivers) is None


def test_build_battery_unknown_brand_raises():
    config = Config.default()
    config.battery.brand = "tesla"
    with pytest.raises(ValueError):
        build_battery(config, [])


def test_build_local_links_only_covers_enabled_drivers():
    config = Config.default()
    drivers = [build_driver(item) for item in config.drivers]
    links = build_local_links(config, drivers, Poller(drivers))
    assert set(links) == {"goodwe"}


def test_build_remote_links_use_the_core_address():
    config = Config.default()
    config.core.remote = "10.0.0.9:1234"
    links = build_remote_links(config)
    assert set(links) == {"goodwe"}
    link = links["goodwe"]
    assert isinstance(link, RemoteCore)
    assert (link.host, link.port) == ("10.0.0.9", 1234)


def test_build_servers_unknown_kind_raises():
    config = Config.default()
    config.servers = [ServerConfig(kind="bogus", device="goodwe")]
    with pytest.raises(ValueError):
        build_servers(config, {"goodwe": FakeCore()})


def test_build_servers_without_a_link_raises():
    with pytest.raises(ValueError):
        build_servers(Config.default(), {})


def test_build_servers_use_the_device_link():
    core = FakeCore()
    servers = build_servers(Config.default(), {"goodwe": core})
    assert servers[0].core is core


# --- services -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("all", EnergyProxy), ("core", CoreService), ("facade", FacadeService)],
)
def test_build_service_selects_the_mode(mode, expected):
    config = Config.default()
    config.mode = mode
    assert isinstance(build_service(config), expected)


def test_energy_proxy_describe_and_status():
    proxy = build_service(Config.default())
    assert "3 driver(s)" in proxy.describe()
    status = proxy.status()
    assert status["mode"] == "all"
    assert len(status["drivers"]) == 3
    assert "goodwe_dongle" in status["servers"]


def test_core_service_status():
    config = Config.default()
    config.mode = "core"
    config.core.port = 0
    status = build_service(config).status()
    assert status["mode"] == "core"
    assert status["core"]["devices"] == ["goodwe"]


def test_facade_service_uses_a_remote_core():
    config = Config.default()
    config.mode = "facade"
    config.core.remote = "10.0.0.9:1234"
    service = build_service(config)
    assert service.core_address == "10.0.0.9:1234"
    assert isinstance(service.servers[0].core, RemoteCore)


def test_service_run_starts_ticks_and_stops():
    class Dummy(_Service):
        def __init__(self):
            super().__init__(Config.default())
            self.started = False
            self.stopped = False
            self.ticks = 0

        async def start(self):
            self.started = True

        async def tick(self):
            self.ticks += 1
            self.request_stop()

        async def stop(self):
            self.stopped = True
            await super().stop()

    service = Dummy()
    asyncio.run(service.run())
    assert service.started is True
    assert service.stopped is True
    assert service.ticks == 1


# --- end to end without a network -----------------------------------------


def test_energy_proxy_polls_and_serves_the_facade():
    proxy = build_service(Config.default())
    proxy.drivers[0].transport = FakeTransport(build_store().as_dict())

    proxy.poll_once()
    assert proxy.snapshot.get(BATTERY_SOC) == 73
    assert proxy.status()["poll"]["devices"]["goodwe"]["online"] is True

    server = proxy.servers[0]
    read = server.handle(_build_request(0xF7, READ_CMD, 35001, 1))
    assert read[4] == 2  # one register
    assert int.from_bytes(read[5:7], "big") == 10000

    server.handle(_build_request(0xF7, WRITE_CMD, 47000, 5))
    assert proxy.drivers[0].image()[47000] == 5


# --- the command-line entry point -----------------------------------------


def test_cli_check(tmp_path, capsys):
    from proxy.__main__ import main

    path = tmp_path / "cfg.yaml"
    path.write_text("mode: core\ncore:\n  port: 0\n")
    assert main(["--config", str(path), "--check"]) == 0
    assert "configuration OK" in capsys.readouterr().out


def test_cli_print_config(tmp_path, capsys):
    from proxy.__main__ import main

    path = tmp_path / "cfg.yaml"
    path.write_text("mode: facade\n")
    assert main(["--config", str(path), "--print-config"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "facade"


def test_cli_once_with_a_disabled_driver(tmp_path, capsys):
    from proxy.__main__ import main

    path = tmp_path / "cfg.yaml"
    path.write_text(
        "mode: core\n"
        "drivers:\n"
        "  - name: goodwe\n"
        "    brand: goodwe\n"
        "    enabled: false\n"
    )
    assert main(["--config", str(path), "--once"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "core"
