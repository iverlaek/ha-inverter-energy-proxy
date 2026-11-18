"""The poller: the single place where devices are touched.

Everything a consumer reads comes out of one cached :class:`~proxy.canonical.Snapshot`
that this poller fills. Everything a consumer writes comes back through this
poller, which translates it to the right device. Because there is exactly one
poller per device and it backs off on failure, the physical dongle sees far less
traffic than a room full of independent clients would generate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .battery.base import Battery
from .canonical import (
    BATTERY_INTENT,
    INVERTER_TOU_WINDOWS,
    DeviceState,
    Snapshot,
    is_writable,
)
from .drivers.base import WRITE_CANONICAL, WRITE_RAW, InverterDriver, WriteRequest
from .intents import BatteryIntent, intent_for_schedule
from .schedule import Schedule

_LOGGER = logging.getLogger(__name__)


@dataclass
class PollStatus:
    """Health of one polled device."""

    name: str
    brand: str = ""
    online: bool = False
    last_poll: float = 0.0
    last_error: str = ""
    failures: int = 0
    next_attempt: float = 0.0
    polls: int = 0
    errors: int = 0

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        return {
            "name": self.name,
            "brand": self.brand,
            "online": self.online,
            "last_poll": self.last_poll,
            "age": round(now - self.last_poll, 1) if self.last_poll else None,
            "last_error": self.last_error,
            "failures": self.failures,
            "polls": self.polls,
            "errors": self.errors,
        }


class Poller:
    """Polls drivers and batteries, caches the result, and routes writes."""

    def __init__(
        self,
        drivers: Iterable[InverterDriver] = (),
        batteries: Iterable[Battery] = (),
        *,
        interval: float = 30.0,
        ttl: float = 120.0,
        min_backoff: float = 5.0,
        max_backoff: float = 300.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.drivers = list(drivers)
        self.batteries = list(batteries)
        self.interval = interval
        self.ttl = ttl
        self.min_backoff = min_backoff
        self.max_backoff = max_backoff
        self.logger = logger or _LOGGER
        self.snapshot = Snapshot()
        self.states: dict[str, DeviceState] = {}
        self.status: dict[str, PollStatus] = {
            driver.name: PollStatus(name=driver.name, brand=driver.brand)
            for driver in self.drivers
        }
        self.last_poll = 0.0
        self.polls = 0

    # -- polling -----------------------------------------------------------

    def poll_once(self, now: float | None = None) -> Snapshot:
        """Poll every due device once and refresh the shared snapshot."""
        now = now if now is not None else time.time()
        for driver in self.drivers:
            if not getattr(driver, "enabled", True):
                continue
            if now < self.status[driver.name].next_attempt:
                continue
            self._poll_driver(driver, now)
        for battery in self.batteries:
            self._poll_battery(battery, now)
        self.last_poll = now
        self.polls += 1
        return self.snapshot

    def _poll_driver(self, driver: InverterDriver, now: float) -> None:
        status = self.status[driver.name]
        try:
            readings = driver.read_state()
        except Exception as err:  # noqa: BLE001 - one bad device must not stop the rest
            self._record_failure(status, err, now)
            return
        status.online = True
        status.last_error = ""
        status.failures = 0
        status.last_poll = now
        status.polls += 1
        status.next_attempt = now + self.interval
        self.snapshot.merge(readings)
        info = driver.info()
        self.states[driver.name] = DeviceState(
            device="inverter",
            brand=info.brand,
            model=info.model,
            serial=info.serial,
            firmware=info.firmware,
            online=True,
            last_poll=now,
            readings=readings,
        )

    def _poll_battery(self, battery: Battery, now: float) -> None:
        try:
            readings = battery.read_state()
        except Exception as err:  # noqa: BLE001
            self.logger.debug("battery %s poll failed: %s", battery.name, err)
            return
        self.snapshot.merge(readings)
        try:
            battery.tick(now)
        except Exception as err:  # noqa: BLE001
            self.logger.debug("battery %s tick failed: %s", battery.name, err)

    def _record_failure(self, status: PollStatus, err: Exception, now: float) -> None:
        status.online = False
        status.last_error = str(err)
        status.failures += 1
        status.errors += 1
        backoff = min(self.max_backoff, self.min_backoff * (2 ** (status.failures - 1)))
        status.next_attempt = now + backoff
        self.logger.warning(
            "%s poll failed (%s); retrying in %.0fs", status.name, err, backoff
        )

    def due(self, now: float | None = None) -> bool:
        """Whether any enabled driver is due for a poll."""
        now = now if now is not None else time.time()
        return any(
            getattr(driver, "enabled", True) and now >= self.status[driver.name].next_attempt
            for driver in self.drivers
        )

    def stale_keys(self, now: float | None = None) -> list[str]:
        return self.snapshot.stale(self.ttl, now)

    # -- writes ------------------------------------------------------------

    def apply_write_request(self, request: WriteRequest) -> None:
        """Apply one decoded consumer write."""
        if request.kind == WRITE_CANONICAL:
            self.apply_canonical(request.key, request.value)
            return
        if request.kind == WRITE_RAW:
            driver = self._driver_for_offset(request.offset)
            if driver is None:
                raise KeyError(f"no driver can write offset {request.offset}")
            driver.write_raw(request.offset, request.raw_value or 0)
            return
        raise ValueError(f"unknown write request kind {request.kind!r}")

    def apply_canonical(self, key: str, value: Any) -> None:
        """Apply a canonical write, routing battery control to the battery."""
        if key == BATTERY_INTENT:
            self.apply_intent(_as_intent(value))
            return
        if key == INVERTER_TOU_WINDOWS:
            schedule = value if isinstance(value, Schedule) else Schedule.from_dict(value)
            driver = self._driver_for_key(key)
            if driver is None:
                raise KeyError(f"no driver can write {key}")
            driver.write(key, schedule)
            self._observe_schedule(schedule)
            return
        driver = self._driver_for_key(key)
        if driver is None:
            raise KeyError(f"no driver can write {key!r}")
        driver.write(key, value)

    def apply_intent(self, intent: BatteryIntent) -> None:
        """Hand a charge/discharge request to the battery."""
        battery = self._battery()
        if battery is None:
            raise RuntimeError("no battery is configured")
        battery.apply_intent(intent)

    def _observe_schedule(self, schedule: Schedule) -> None:
        """Record the intent implied by a schedule without re-writing it.

        The schedule is already on the device; the battery is told what it means
        so that the separated battery view stays authoritative.
        """
        battery = self._battery()
        if battery is None:
            return
        battery.observe_intent(intent_for_schedule(schedule))

    # -- lookups -----------------------------------------------------------

    def _driver_for_key(self, key: str) -> InverterDriver | None:
        for driver in self.drivers:
            if getattr(driver, "enabled", True) and driver.supports(key):
                return driver
        return None

    def _driver_for_offset(self, offset: int | None) -> InverterDriver | None:
        if offset is None:
            return None
        for driver in self.drivers:
            if not getattr(driver, "enabled", True):
                continue
            blocks = driver.blocks()
            if any(b.offset <= offset < b.offset + b.count for b in blocks):
                return driver
        return None

    def _battery(self) -> Battery | None:
        return self.batteries[0] if self.batteries else None

    # -- introspection -----------------------------------------------------

    def status_dict(self, now: float | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        battery = self._battery()
        return {
            "polls": self.polls,
            "last_poll": self.last_poll,
            "interval": self.interval,
            "ttl": self.ttl,
            "devices": {name: s.as_dict(now) for name, s in self.status.items()},
            "readings": len(self.snapshot),
            "stale": self.stale_keys(now),
            "battery": battery.info() if battery else None,
            "intent": (
                battery.active_intent.to_dict()
                if battery and battery.active_intent
                else None
            ),
        }


def _as_intent(value: Any) -> BatteryIntent:
    if isinstance(value, BatteryIntent):
        return value
    if isinstance(value, dict):
        return BatteryIntent.from_dict(value)
    raise TypeError(f"cannot interpret {value!r} as a BatteryIntent")


__all__ = ["Poller", "PollStatus", "is_writable"]
