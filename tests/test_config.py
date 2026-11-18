"""Tests for configuration loading and the three deployment modes."""

from __future__ import annotations

import json

import pytest

from proxy.config import (
    MODE_ALL,
    MODE_CORE,
    MODE_FACADE,
    MODES,
    BatteryConfig,
    Config,
    CoreConfig,
    DriverConfig,
    ServerConfig,
)


def test_default_config_is_the_goodwe_milestone():
    config = Config.default()
    assert config.mode == MODE_ALL
    assert config.servers[0].kind == "goodwe_dongle"
    assert config.battery.brand == "goodwe"
    by_name = {driver.name: driver for driver in config.drivers}
    assert by_name["goodwe"].enabled is True
    assert by_name["solaredge"].enabled is False
    assert by_name["enphase"].enabled is False


def test_from_dict_parses_everything():
    data = {
        "mode": "core",
        "log_level": "debug",
        "poll": {"interval": 15, "ttl": 60, "min_backoff": 2, "max_backoff": 100},
        "core": {
            "listen_host": "0.0.0.0",
            "port": 8788,
            "remote": "10.0.0.9:8787",
            "device": "goodwe",
        },
        "drivers": [
            {
                "name": "goodwe",
                "brand": "goodwe",
                "host": "192.168.20.13",
                "port": 8899,
                "comm_addr": "0xF7",
                "timeout": 2.0,
                "retries": 5,
                "options": {"x": 1},
            }
        ],
        "battery": {
            "brand": "goodwe",
            "control": "eco",
            "rated_power_w": 8000,
            "driver": "goodwe",
        },
        "servers": [
            {"kind": "goodwe_dongle", "device": "goodwe", "host": "0.0.0.0", "port": 8899}
        ],
    }
    config = Config.from_dict(data)
    assert config.mode == MODE_CORE
    assert config.log_level == "debug"
    assert config.poll_interval == 15
    assert config.poll_ttl == 60
    assert config.min_backoff == 2
    assert config.max_backoff == 100
    assert config.core.port == 8788
    assert config.core.remote_address() == ("10.0.0.9", 8787)

    driver = config.drivers[0]
    assert driver.name == "goodwe"
    assert driver.host == "192.168.20.13"
    assert driver.comm_addr == 0xF7
    assert driver.timeout == 2.0
    assert driver.retries == 5
    assert driver.options == {"x": 1}

    assert config.battery.control == "eco"
    assert config.battery.rated_power_w == 8000
    assert config.battery.driver == "goodwe"
    assert config.servers[0].device == "goodwe"


def test_servers_can_be_given_as_a_mapping():
    config = Config.from_dict({"servers": {"goodwe_dongle": {"port": 8899}}})
    assert len(config.servers) == 1
    assert config.servers[0].kind == "goodwe_dongle"


def test_servers_default_when_absent():
    config = Config.from_dict({})
    assert config.servers[0].kind == "goodwe_dongle"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"mode": "bogus"})
    assert set(MODES) == {MODE_ALL, MODE_CORE, MODE_FACADE}


def test_driver_brand_defaults_to_the_name():
    driver = DriverConfig.from_dict({"name": "solaredge"})
    assert driver.brand == "solaredge"
    assert driver.name == "solaredge"


def test_battery_defaults():
    battery = BatteryConfig.from_dict(None)
    assert battery.brand == "goodwe"
    assert battery.control == "ems"


def test_server_config_defaults():
    server = ServerConfig.from_dict("goodwe_dongle", None)
    assert server.enabled is True
    assert server.host == "0.0.0.0"
    assert server.device == "goodwe"


def test_core_remote_address_variants():
    core = CoreConfig()
    assert core.remote_address() == ("0.0.0.0", 8787)
    core.remote = "10.0.0.9:1234"
    assert core.remote_address() == ("10.0.0.9", 1234)
    core.remote = "10.0.0.9"
    assert core.remote_address() == ("10.0.0.9", 8787)


def test_load_missing_file_falls_back_to_defaults():
    config = Config.load("/nonexistent/inverter_proxy.yaml")
    assert config.mode == MODE_ALL
    assert any(driver.name == "goodwe" for driver in config.drivers)


def test_load_yaml_file(tmp_path):
    path = tmp_path / "inverter_proxy.yaml"
    path.write_text(
        "mode: core\n"
        "core:\n  port: 9999\n"
        "drivers:\n  - name: goodwe\n    host: 10.0.0.1\n"
    )
    config = Config.load(str(path))
    assert config.mode == MODE_CORE
    assert config.core.port == 9999
    assert config.drivers[0].host == "10.0.0.1"


def test_load_json_file(tmp_path):
    path = tmp_path / "inverter_proxy.json"
    path.write_text(json.dumps({"mode": "facade", "core": {"remote": "1.2.3.4:1234"}}))
    config = Config.load(str(path))
    assert config.mode == MODE_FACADE
    assert config.core.remote_address() == ("1.2.3.4", 1234)


def test_load_invalid_yaml_raises(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("mode: [unclosed\n")
    with pytest.raises(ValueError):
        Config.load(str(path))


def test_load_honours_the_env_var(tmp_path, monkeypatch):
    path = tmp_path / "env.yaml"
    path.write_text("mode: core\n")
    monkeypatch.setenv("PROXY_CONFIG", str(path))
    assert Config.load().mode == MODE_CORE
