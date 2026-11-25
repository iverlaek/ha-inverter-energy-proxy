"""Wiring: build the proxy from configuration and run it.

This is the one place that knows about every concrete driver, battery and
server. Everything below it depends only on the interfaces, which is what keeps
brands swappable.

Three services share this wiring:

* :class:`EnergyProxy` — ``mode: all``: poll and serve in one process;
* :class:`CoreService` — ``mode: core``: poll, and serve facades over TCP;
* :class:`FacadeService` — ``mode: facade``: serve consumers, forwarding to a core.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .battery.base import Battery
from .battery.goodwe import GoodweBattery
from .config import MODE_CORE, MODE_FACADE, Config, DriverConfig
from .drivers.base import InverterDriver
from .drivers.enphase import EnphaseDriver
from .drivers.goodwe import GoodweDriver
from .drivers.solaredge import SolarEdgeDriver
from .poller import Poller
from .server.base import CoreLink, Server
from .server.core_link import InProcessCore, RemoteCore
from .server.core_server import CoreServer
from .server.goodwe_dongle import GoodweDongleServer

_LOGGER = logging.getLogger("proxy")

DRIVERS: dict[str, type[InverterDriver]] = {
    "goodwe": GoodweDriver,
    "solaredge": SolarEdgeDriver,
    "enphase": EnphaseDriver,
}

#: How often the async loop wakes to check whether a poll is due.
TICK_SECONDS = 1.0


# --- factories -------------------------------------------------------------


def build_driver(config: DriverConfig) -> InverterDriver:
    """Construct one driver from its configuration."""
    factory = DRIVERS.get(config.brand)
    if factory is None:
        raise ValueError(f"unknown driver brand {config.brand!r}")
    if config.brand == "goodwe":
        driver: InverterDriver = GoodweDriver(
            host=config.host,
            port=config.port,
            comm_addr=config.comm_addr,
            timeout=config.timeout,
            retries=config.retries,
        )
    else:
        driver = factory()
    driver.enabled = config.enabled  # type: ignore[attr-defined]
    return driver


def build_battery(config: Config, drivers: list[InverterDriver]) -> Battery | None:
    """Construct the battery, hosted on the driver that owns it."""
    if config.battery.brand != "goodwe":
        raise ValueError(f"unknown battery brand {config.battery.brand!r}")
    host: InverterDriver | None = None
    if config.battery.driver:
        host = next((d for d in drivers if d.name == config.battery.driver), None)
    if host is None:
        host = next((d for d in drivers if isinstance(d, GoodweDriver)), None)
    if host is None:
        _LOGGER.warning("no GoodWe driver hosts the battery; battery disabled")
        return None
    return GoodweBattery(
        host,
        control=config.battery.control,
        rated_power_w=config.battery.rated_power_w,
    )


def build_local_links(
    config: Config, drivers: list[InverterDriver], poller: Poller
) -> dict[str, CoreLink]:
    """In-process links for the ``all`` and ``core`` modes."""
    return {
        driver.name: InProcessCore(driver, poller)
        for driver in drivers
        if getattr(driver, "enabled", True)
    }


def build_remote_links(config: Config) -> dict[str, CoreLink]:
    """Network links for the ``facade`` mode."""
    host, port = config.core.remote_address()
    links: dict[str, CoreLink] = {}
    for server in config.servers:
        if not server.enabled:
            continue
        links[server.device] = RemoteCore(host, port, device=server.device)
    return links


def build_servers(config: Config, links: dict[str, CoreLink]) -> list[Server]:
    """Construct every enabled facade."""
    servers: list[Server] = []
    for server_config in config.servers:
        if not server_config.enabled:
            continue
        if server_config.kind != "goodwe_dongle":
            raise ValueError(f"unknown server kind {server_config.kind!r}")
        core = links.get(server_config.device)
        if core is None:
            core = links.get(config.core.device)
        if core is None and links:
            core = next(iter(links.values()))
        if core is None:
            raise ValueError(
                f"no core link for device {server_config.device!r}; "
                "is a driver of that name enabled?"
            )
        servers.append(
            GoodweDongleServer(
                core,
                host=server_config.host,
                port=server_config.port,
                comm_addr=server_config.comm_addr,
            )
        )
    return servers


# --- services --------------------------------------------------------------


class _Service:
    """Common lifecycle for the runnable services."""

    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or _LOGGER
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        """Start whatever this service serves."""

    async def tick(self) -> None:
        """One periodic step (a poll, usually)."""

    async def stop(self) -> None:
        """Release resources."""
        if self._stop is not None:
            self._stop.set()

    def request_stop(self) -> None:
        if self._stop is not None:
            self._stop.set()

    async def run(self) -> None:
        """Serve until :meth:`request_stop` is called."""
        self._stop = asyncio.Event()
        await self.start()
        self.logger.info("proxy started (%s)", self.describe())
        try:
            while not self._stop.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.stop()

    def describe(self) -> str:
        return type(self).__name__

    def status(self) -> dict[str, Any]:
        return {}


class EnergyProxy(_Service):
    """The assembled proxy: pollers, battery and servers in one process."""

    def __init__(self, config: Config | None = None, logger: logging.Logger | None = None) -> None:
        super().__init__(config or Config.default(), logger)
        self.drivers = [build_driver(item) for item in self.config.drivers]
        battery = build_battery(self.config, self.drivers)
        self.batteries: list[Battery] = [battery] if battery else []
        self.poller = Poller(
            self.drivers,
            self.batteries,
            interval=self.config.poll_interval,
            ttl=self.config.poll_ttl,
            min_backoff=self.config.min_backoff,
            max_backoff=self.config.max_backoff,
            write_interval=self.config.write_interval,
            logger=self.logger,
        )
        self.links = build_local_links(self.config, self.drivers, self.poller)
        self.servers = build_servers(self.config, self.links)

    # -- convenience -------------------------------------------------------

    @property
    def snapshot(self):
        return self.poller.snapshot

    def poll_once(self) -> None:
        """Poll synchronously (used by tests and ``--once``)."""
        self.poller.poll_once()

    def describe(self) -> str:
        return (
            f"{len(self.drivers)} driver(s), {len(self.batteries)} battery(ies), "
            f"{len(self.servers)} server(s)"
        )

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        for server in self.servers:
            await server.start()

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.poller.poll_once)

    async def stop(self) -> None:
        await super().stop()
        for server in self.servers:
            await server.stop()
        for driver in self.drivers:
            try:
                driver.close()
            except Exception as err:  # noqa: BLE001
                self.logger.debug("closing %s failed: %s", driver.name, err)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "poll": self.poller.status_dict(),
            "servers": {
                server.name: server.stats.as_dict()
                for server in self.servers
                if hasattr(server, "stats")
            },
            "drivers": [
                {
                    "name": d.name,
                    "brand": d.brand,
                    "enabled": getattr(d, "enabled", True),
                    "host": getattr(d, "host", ""),
                    "port": getattr(d, "port", None),
                }
                for d in self.drivers
            ],
        }


class CoreService(_Service):
    """``mode: core`` — poll the devices and serve facades over TCP."""

    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        super().__init__(config, logger)
        self.drivers = [build_driver(item) for item in self.config.drivers]
        battery = build_battery(self.config, self.drivers)
        self.batteries: list[Battery] = [battery] if battery else []
        self.poller = Poller(
            self.drivers,
            self.batteries,
            interval=self.config.poll_interval,
            ttl=self.config.poll_ttl,
            min_backoff=self.config.min_backoff,
            max_backoff=self.config.max_backoff,
            write_interval=self.config.write_interval,
            logger=self.logger,
        )
        self.links = build_local_links(self.config, self.drivers, self.poller)
        self.server = CoreServer(
            self.links,
            host=self.config.core.listen_host,
            port=self.config.core.port,
            default_device=self.config.core.device,
            logger=self.logger,
        )

    def poll_once(self) -> None:
        self.poller.poll_once()

    def describe(self) -> str:
        return (
            f"core on {self.config.core.listen_host}:{self.config.core.port}, "
            f"{len(self.drivers)} driver(s), {len(self.batteries)} battery(ies)"
        )

    async def start(self) -> None:
        self.poller.poll_once()
        await self.server.start()

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.poller.poll_once)

    async def stop(self) -> None:
        await super().stop()
        await self.server.stop()
        for driver in self.drivers:
            try:
                driver.close()
            except Exception as err:  # noqa: BLE001
                self.logger.debug("closing %s failed: %s", driver.name, err)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "core": self.server.as_dict(),
            "poll": self.poller.status_dict(),
            "drivers": [
                {
                    "name": d.name,
                    "brand": d.brand,
                    "enabled": getattr(d, "enabled", True),
                    "host": getattr(d, "host", ""),
                    "port": getattr(d, "port", None),
                }
                for d in self.drivers
            ],
        }


class FacadeService(_Service):
    """``mode: facade`` — serve consumers, forwarding to a remote core."""

    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        super().__init__(config, logger)
        self.links = build_remote_links(self.config)
        self.servers = build_servers(self.config, self.links)
        host, port = self.config.core.remote_address()
        self.core_address = f"{host}:{port}"

    def describe(self) -> str:
        return f"facade(s) for {self.core_address}: {len(self.servers)} server(s)"

    async def start(self) -> None:
        for server in self.servers:
            await server.start()

    async def stop(self) -> None:
        await super().stop()
        for server in self.servers:
            await server.stop()
        for link in self.links.values():
            link.close()

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "core": self.core_address,
            "servers": {
                server.name: server.stats.as_dict()
                for server in self.servers
                if hasattr(server, "stats")
            },
        }


def build_service(config: Config, logger: logging.Logger | None = None) -> _Service:
    """Construct the service the configuration asks for."""
    if config.mode == MODE_CORE:
        return CoreService(config, logger)
    if config.mode == MODE_FACADE:
        return FacadeService(config, logger)
    return EnergyProxy(config, logger)
