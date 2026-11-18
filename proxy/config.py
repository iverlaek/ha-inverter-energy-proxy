"""Configuration for the proxy.

The proxy is configured by one file (YAML or JSON). The same file drives all
three deployment shapes, selected by ``mode``:

``all``
    One process: poll the devices and serve the facades in-process. Simplest to
    run, and the default.

``core``
    Poll the devices and serve an internal TCP endpoint that facades connect to.
    Runs the brand knowledge.

``facade``
    Serve one or more consumer protocols and forward everything to a core over
    the network. Holds no device state, so each facade can live on its own IP.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CONFIG_PATH = "/config/inverter_proxy.yaml"

DEFAULT_POLL_INTERVAL = 30.0
DEFAULT_POLL_TTL = 120.0
DEFAULT_MIN_BACKOFF = 5.0
DEFAULT_MAX_BACKOFF = 300.0

DEFAULT_GOODWE_HOST = "192.168.20.13"
DEFAULT_GOODWE_PORT = 8899
DEFAULT_COMM_ADDR = 0xF7
DEFAULT_CORE_PORT = 8787

MODE_ALL = "all"
MODE_CORE = "core"
MODE_FACADE = "facade"
MODES = (MODE_ALL, MODE_CORE, MODE_FACADE)


@dataclass
class DriverConfig:
    """One inverter device to poll."""

    name: str
    brand: str
    enabled: bool = True
    host: str = ""
    port: int = DEFAULT_GOODWE_PORT
    comm_addr: int = DEFAULT_COMM_ADDR
    timeout: float = 1.0
    retries: int = 3
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriverConfig:
        brand = str(data.get("brand", data.get("name", "goodwe"))).lower()
        return cls(
            name=str(data.get("name", brand)),
            brand=brand,
            enabled=bool(data.get("enabled", True)),
            host=str(data.get("host", "")),
            port=int(data.get("port", DEFAULT_GOODWE_PORT)),
            comm_addr=_as_int(data.get("comm_addr", DEFAULT_COMM_ADDR)),
            timeout=float(data.get("timeout", 1.0)),
            retries=int(data.get("retries", 3)),
            options=dict(data.get("options", {})),
        )


@dataclass
class BatteryConfig:
    """The battery, and which inverter driver hosts it."""

    name: str = "goodwe"
    brand: str = "goodwe"
    control: str = "ems"
    rated_power_w: float = 6000.0
    #: Name of the driver the battery lives on; defaults to the first matching brand.
    driver: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BatteryConfig:
        data = data or {}
        return cls(
            name=str(data.get("name", "goodwe")),
            brand=str(data.get("brand", data.get("name", "goodwe"))).lower(),
            control=str(data.get("control", "ems")).lower(),
            rated_power_w=float(data.get("rated_power_w", 6000.0)),
            driver=data.get("driver"),
        )


@dataclass
class ServerConfig:
    """One consumer-facing interface."""

    kind: str
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = DEFAULT_GOODWE_PORT
    comm_addr: int = DEFAULT_COMM_ADDR
    #: Which device (driver) this facade fronts. Defaults to the core's default.
    device: str = "goodwe"

    @classmethod
    def from_dict(cls, kind: str, data: dict[str, Any] | None) -> ServerConfig:
        data = data or {}
        return cls(
            kind=kind,
            enabled=bool(data.get("enabled", True)),
            host=str(data.get("host", "0.0.0.0")),
            port=int(data.get("port", DEFAULT_GOODWE_PORT)),
            comm_addr=_as_int(data.get("comm_addr", DEFAULT_COMM_ADDR)),
            device=str(data.get("device", "goodwe")),
        )


@dataclass
class CoreConfig:
    """How the core is reached.

    In ``core`` mode, ``listen_host``/``port`` are where the core listens. In
    ``facade`` mode, ``remote`` is the ``host:port`` of the core to connect to
    (defaulting to ``listen_host:port``).
    """

    listen_host: str = "0.0.0.0"
    port: int = DEFAULT_CORE_PORT
    remote: str = ""
    device: str = "goodwe"

    def remote_address(self) -> tuple[str, int]:
        target = self.remote.strip()
        if target:
            host, _, port = target.rpartition(":")
            if host and port.isdigit():
                return host, int(port)
            return target, self.port
        return self.listen_host, self.port

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CoreConfig:
        data = data or {}
        return cls(
            listen_host=str(data.get("listen_host", data.get("host", "0.0.0.0"))),
            port=int(data.get("port", DEFAULT_CORE_PORT)),
            remote=str(data.get("remote", "")),
            device=str(data.get("device", "goodwe")),
        )


@dataclass
class Config:
    """The whole proxy configuration."""

    mode: str = MODE_ALL
    log_level: str = "info"
    poll_interval: float = DEFAULT_POLL_INTERVAL
    poll_ttl: float = DEFAULT_POLL_TTL
    min_backoff: float = DEFAULT_MIN_BACKOFF
    max_backoff: float = DEFAULT_MAX_BACKOFF
    drivers: list[DriverConfig] = field(default_factory=list)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    servers: list[ServerConfig] = field(default_factory=list)
    core: CoreConfig = field(default_factory=CoreConfig)

    # -- loading -----------------------------------------------------------

    @classmethod
    def default(cls) -> Config:
        return cls(
            drivers=[
                DriverConfig(
                    name="goodwe",
                    brand="goodwe",
                    host=DEFAULT_GOODWE_HOST,
                ),
                DriverConfig(name="solaredge", brand="solaredge", enabled=False),
                DriverConfig(name="enphase", brand="enphase", enabled=False),
            ],
            battery=BatteryConfig(),
            servers=[ServerConfig(kind="goodwe_dongle")],
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        poll = dict(data.get("poll", {}))
        drivers = [DriverConfig.from_dict(item) for item in data.get("drivers", [])]
        servers_raw = data.get("servers")
        if servers_raw is None:
            servers = [ServerConfig(kind="goodwe_dongle")]
        elif isinstance(servers_raw, list):
            servers = [
                ServerConfig.from_dict(str(item.get("kind", "goodwe_dongle")), item)
                for item in servers_raw
            ]
        else:
            servers = [
                ServerConfig.from_dict(str(kind), value)
                for kind, value in servers_raw.items()
            ]
        mode = str(data.get("mode", MODE_ALL)).lower()
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
        return cls(
            mode=mode,
            log_level=str(data.get("log_level", "info")).lower(),
            poll_interval=float(poll.get("interval", DEFAULT_POLL_INTERVAL)),
            poll_ttl=float(poll.get("ttl", DEFAULT_POLL_TTL)),
            min_backoff=float(poll.get("min_backoff", DEFAULT_MIN_BACKOFF)),
            max_backoff=float(poll.get("max_backoff", DEFAULT_MAX_BACKOFF)),
            drivers=drivers,
            battery=BatteryConfig.from_dict(data.get("battery")),
            servers=servers,
            core=CoreConfig.from_dict(data.get("core")),
        )

    @classmethod
    def load(cls, path: str | None = None) -> Config:
        """Load the configuration from *path*, or fall back to the defaults."""
        path = path or os.environ.get("PROXY_CONFIG", DEFAULT_CONFIG_PATH)
        if not path or not os.path.exists(path):
            logging.getLogger(__name__).warning(
                "no config file at %s; using built-in defaults", path
            )
            return cls.default()
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        data = _parse(text, path)
        config = cls.from_dict(data)
        logging.getLogger(__name__).info("loaded configuration from %s", path)
        return config


def _parse(text: str, path: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    except Exception as err:  # noqa: BLE001 - fall back to JSON with a clear error
        if path.endswith((".yaml", ".yml")):
            raise ValueError(f"invalid YAML in {path}: {err}") from err
    return json.loads(text)


def _as_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 0)
    return int(value)
