"""A placeholder driver for a brand that is not wired up yet.

SolarEdge and Enphase are deliberately paused until GoodWe works end to end.
Their drivers still exist and still satisfy :class:`InverterDriver`, so the rest
of the proxy — the poller, the canonical model, the consumers — does not have to
know they are unfinished. They simply report ``enabled = False`` and are skipped.
"""

from __future__ import annotations

from typing import Any

from ..canonical import Snapshot
from .base import DeviceInfo, InverterDriver


class PausedDriver(InverterDriver):
    """Base class for a driver that is defined but not yet implemented."""

    name = "paused"
    brand = "unknown"
    #: Paused drivers are skipped by the poller until they are implemented.
    enabled = False
    #: What the driver will do once implemented, for the status endpoint.
    plan = ""

    def read_state(self) -> Snapshot:
        raise NotImplementedError(f"the {self.brand} driver is not implemented yet")

    def write(self, key: str, value: Any) -> None:
        raise NotImplementedError(f"the {self.brand} driver is not implemented yet")

    def info(self) -> DeviceInfo:
        return DeviceInfo(brand=self.brand, model="(paused)")
