"""The GoodWe battery, which lives on the GoodWe inverter.

This is the concrete answer to "the current GoodWe batteries happen to be on the
GoodWe inverter": the battery's telemetry is read by the inverter driver, and a
charge/discharge request is realised by writing the inverter's control registers.

Two realisation strategies are supported:

``ems``
    The official external-EMS channel (registers 47511/47512). A request maps to
    an EMS mode plus a signed power setpoint. This is the direct "I want to
    charge/discharge" interface and the default.

``eco``
    The Time-of-Use / Eco-mode channel: a single all-day window whose power sign
    is the request. Used when the firmware has no EMS registers.
"""

from __future__ import annotations

import datetime as dt

from ..canonical import (
    BATTERY_INTENT,
    DEVICE_BATTERY,
    INVERTER_EMS_MODE,
    INVERTER_EMS_POWER_LIMIT,
    INVERTER_TOU_WINDOWS,
    INVERTER_WORK_MODE,
    Snapshot,
    keys,
)
from ..intents import BatteryIntent, IntentMode
from ..schedule import ALL_DAYS, MODE_CHARGE, MODE_DISCHARGE, Schedule, ScheduleWindow
from .base import Battery

CONTROL_EMS = "ems"
CONTROL_ECO = "eco"
CONTROL_STRATEGIES = (CONTROL_EMS, CONTROL_ECO)

#: How an intent mode maps onto a GoodWe EMS mode.
_EMS_MODE: dict[IntentMode, str] = {
    IntentMode.IDLE: "battery_standby",
    IntentMode.CHARGE: "import_ac",
    IntentMode.DISCHARGE: "discharge_pv",
    IntentMode.SELF_CONSUMPTION: "auto",
}

#: The GoodWe EMS setpoint is positive when discharging.
_EMS_SIGN: dict[IntentMode, int] = {
    IntentMode.CHARGE: -1,
    IntentMode.DISCHARGE: 1,
}

_DEFAULT_RATED_POWER_W = 6000.0


class GoodweBattery(Battery):
    """A GoodWe battery realised through its host inverter's registers."""

    name = "goodwe"
    brand = "GoodWe"

    def __init__(
        self,
        driver,
        *,
        control: str = CONTROL_EMS,
        rated_power_w: float = _DEFAULT_RATED_POWER_W,
    ) -> None:
        super().__init__()
        if control not in CONTROL_STRATEGIES:
            raise ValueError(f"unknown control strategy {control!r}")
        self.driver = driver
        self.control = control
        self.rated_power_w = rated_power_w

    # -- telemetry ---------------------------------------------------------

    def read_state(self) -> Snapshot:
        source = self.driver.snapshot()
        snapshot = Snapshot()
        for key in keys(DEVICE_BATTERY):
            reading = source.reading(key)
            if reading is not None:
                snapshot.set(key, reading.value, source=self.name, now=reading.updated)
        if self._intent is not None:
            snapshot.set(BATTERY_INTENT, self._intent, source=self.name)
        return snapshot

    # -- realisation -------------------------------------------------------

    def _realise(self, intent: BatteryIntent) -> None:
        if self.control == CONTROL_ECO:
            self._realise_eco(intent)
        else:
            self._realise_ems(intent)

    def _realise_ems(self, intent: BatteryIntent) -> None:
        self.driver.write(INVERTER_EMS_MODE, _EMS_MODE[intent.mode])
        sign = _EMS_SIGN.get(intent.mode)
        power = self._power_w(intent)
        if sign is not None and power is not None:
            self.driver.write(INVERTER_EMS_POWER_LIMIT, int(sign * power))

    def _realise_eco(self, intent: BatteryIntent) -> None:
        if intent.mode in (IntentMode.IDLE, IntentMode.SELF_CONSUMPTION):
            self.driver.write(INVERTER_WORK_MODE, "general")
            return
        mode = MODE_CHARGE if intent.mode is IntentMode.CHARGE else MODE_DISCHARGE
        window = ScheduleWindow(
            start=dt.time(0, 0),
            stop=dt.time(23, 59),
            mode=mode,
            power=int(self._power_percent(intent)),
            days=ALL_DAYS,
        )
        self.driver.write(INVERTER_WORK_MODE, "eco")
        self.driver.write(INVERTER_TOU_WINDOWS, Schedule((window,)))

    # -- helpers -----------------------------------------------------------

    def _power_w(self, intent: BatteryIntent) -> float | None:
        if intent.power_w is not None:
            return abs(float(intent.power_w))
        if intent.power_percent is not None:
            return abs(self.rated_power_w * float(intent.power_percent) / 100.0)
        return None

    def _power_percent(self, intent: BatteryIntent) -> float:
        if intent.power_percent is not None:
            return max(0.0, min(100.0, float(intent.power_percent)))
        if intent.power_w is not None and self.rated_power_w:
            return max(0.0, min(100.0, abs(intent.power_w) / self.rated_power_w * 100.0))
        return 100.0
