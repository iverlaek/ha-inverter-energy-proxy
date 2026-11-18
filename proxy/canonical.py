"""The canonical, brand-independent parameter registry.

Every value the proxy knows about an inverter or a battery is described here by
a *canonical* key such as ``battery.soc`` or ``inverter.pv_power``. Brand
drivers translate their own register map to and from these keys, so the core of
the proxy never has to know which brand sits behind a device.

The registry is deliberately data-driven. Adding a brand means writing a driver
that maps its parameters onto a subset of these keys — it never means editing
this module or the core. That is what makes it possible to keep adding brands.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Final, Iterator, Mapping

# --- devices --------------------------------------------------------------

DEVICE_INVERTER: Final = "inverter"
DEVICE_BATTERY: Final = "battery"
DEVICES: Final = (DEVICE_INVERTER, DEVICE_BATTERY)

# --- value kinds ----------------------------------------------------------

KIND_FLOAT: Final = "float"
KIND_INT: Final = "int"
KIND_BOOL: Final = "bool"
KIND_ENUM: Final = "enum"
KIND_STR: Final = "str"
KIND_JSON: Final = "json"

# --- access ---------------------------------------------------------------

ACCESS_R: Final = "r"
ACCESS_RW: Final = "rw"


@dataclass(frozen=True)
class Parameter:
    """One canonical parameter."""

    key: str
    device: str
    unit: str = ""
    kind: str = KIND_FLOAT
    access: str = ACCESS_R
    description: str = ""
    enum: tuple[str, ...] = ()

    @property
    def writable(self) -> bool:
        return self.access == ACCESS_RW


# --- canonical keys: inverter --------------------------------------------

INVERTER_MODEL: Final = "inverter.model"
INVERTER_SERIAL: Final = "inverter.serial"
INVERTER_FIRMWARE: Final = "inverter.firmware"
INVERTER_RATED_POWER: Final = "inverter.rated_power"
#: The configured operating mode (the *setting*, not the live status).
INVERTER_WORK_MODE: Final = "inverter.work_mode"
#: The live operating status reported by the device.
INVERTER_STATUS_MODE: Final = "inverter.status_mode"
#: The external Energy-Management-System mode (the write-side control channel).
INVERTER_EMS_MODE: Final = "inverter.ems_mode"
INVERTER_EMS_POWER_LIMIT: Final = "inverter.ems_power_limit"
INVERTER_PV_POWER: Final = "inverter.pv_power"
INVERTER_AC_POWER: Final = "inverter.ac_power"
INVERTER_LOAD_POWER: Final = "inverter.load_power"
#: Signed grid power at the meter: positive imports, negative exports.
INVERTER_GRID_POWER: Final = "inverter.grid_power"
INVERTER_GRID_VOLTAGE: Final = "inverter.grid_voltage"
INVERTER_GRID_FREQUENCY: Final = "inverter.grid_frequency"
INVERTER_TEMPERATURE: Final = "inverter.temperature"
INVERTER_EXPORT_ENABLED: Final = "inverter.export_enabled"
INVERTER_EXPORT_LIMIT: Final = "inverter.export_limit"
INVERTER_ENERGY_PV_TODAY: Final = "inverter.energy_pv_today"
INVERTER_ENERGY_PV_TOTAL: Final = "inverter.energy_pv_total"
INVERTER_ENERGY_EXPORT_TODAY: Final = "inverter.energy_export_today"
INVERTER_ENERGY_IMPORT_TODAY: Final = "inverter.energy_import_today"
INVERTER_ENERGY_BATTERY_CHARGE_TODAY: Final = "inverter.energy_battery_charge_today"
INVERTER_ENERGY_BATTERY_DISCHARGE_TODAY: Final = (
    "inverter.energy_battery_discharge_today"
)
#: The Time-of-Use / Eco-mode schedule: a list of :class:`proxy.schedule.ScheduleWindow`.
INVERTER_TOU_WINDOWS: Final = "inverter.tou_windows"

# --- canonical keys: battery ---------------------------------------------

BATTERY_SOC: Final = "battery.soc"
BATTERY_SOH: Final = "battery.soh"
BATTERY_VOLTAGE: Final = "battery.voltage"
BATTERY_CURRENT: Final = "battery.current"
#: Signed battery power: positive charges the battery, negative discharges it.
BATTERY_POWER: Final = "battery.power"
BATTERY_MODE: Final = "battery.mode"
BATTERY_TEMPERATURE: Final = "battery.temperature"
BATTERY_CELL_VOLTAGE_MIN: Final = "battery.cell_voltage_min"
BATTERY_CELL_VOLTAGE_MAX: Final = "battery.cell_voltage_max"
BATTERY_CELL_TEMPERATURE_MIN: Final = "battery.cell_temperature_min"
BATTERY_CELL_TEMPERATURE_MAX: Final = "battery.cell_temperature_max"
BATTERY_CHARGE_LIMIT: Final = "battery.charge_limit"
BATTERY_DISCHARGE_LIMIT: Final = "battery.discharge_limit"
BATTERY_CAPACITY: Final = "battery.capacity"
#: The currently accepted charge/discharge request, as a :class:`proxy.intents.BatteryIntent`.
BATTERY_INTENT: Final = "battery.intent"

#: Power sign convention shared by every brand: positive = into the battery.
POWER_SIGN_CHARGE_POSITIVE: Final = True

WORK_MODE_VALUES: Final = (
    "general",
    "off_grid",
    "backup",
    "eco",
    "peak_shaving",
    "self_use",
)
STATUS_MODE_VALUES: Final = (
    "wait",
    "normal",
    "off_grid",
    "fault",
    "flash",
    "check",
)
EMS_MODE_VALUES: Final = (
    "auto",
    "charge_pv",
    "discharge_pv",
    "import_ac",
    "export_ac",
    "conserve",
    "off_grid",
    "battery_standby",
    "buy_power",
    "sell_power",
)
BATTERY_MODE_VALUES: Final = (
    "none",
    "standby",
    "discharge",
    "charge",
    "to_charge",
    "to_discharge",
)

_PARAMETERS: tuple[Parameter, ...] = (
    # --- inverter identity ---
    Parameter(INVERTER_MODEL, DEVICE_INVERTER, kind=KIND_STR, description="Model name"),
    Parameter(
        INVERTER_SERIAL, DEVICE_INVERTER, kind=KIND_STR, description="Serial number"
    ),
    Parameter(
        INVERTER_FIRMWARE, DEVICE_INVERTER, kind=KIND_STR, description="Firmware"
    ),
    Parameter(
        INVERTER_RATED_POWER,
        DEVICE_INVERTER,
        unit="W",
        description="Rated AC power",
    ),
    # --- inverter control ---
    Parameter(
        INVERTER_WORK_MODE,
        DEVICE_INVERTER,
        kind=KIND_ENUM,
        access=ACCESS_RW,
        enum=WORK_MODE_VALUES,
        description="Configured operating mode",
    ),
    Parameter(
        INVERTER_STATUS_MODE,
        DEVICE_INVERTER,
        kind=KIND_ENUM,
        enum=STATUS_MODE_VALUES,
        description="Live operating status",
    ),
    Parameter(
        INVERTER_EMS_MODE,
        DEVICE_INVERTER,
        kind=KIND_ENUM,
        access=ACCESS_RW,
        enum=EMS_MODE_VALUES,
        description="External EMS mode",
    ),
    Parameter(
        INVERTER_EMS_POWER_LIMIT,
        DEVICE_INVERTER,
        unit="W",
        access=ACCESS_RW,
        description="EMS power setpoint (positive discharges, negative charges)",
    ),
    Parameter(
        INVERTER_EXPORT_ENABLED,
        DEVICE_INVERTER,
        kind=KIND_BOOL,
        access=ACCESS_RW,
        description="Whether grid export is allowed",
    ),
    Parameter(
        INVERTER_EXPORT_LIMIT,
        DEVICE_INVERTER,
        unit="W",
        access=ACCESS_RW,
        description="Maximum grid export power",
    ),
    Parameter(
        INVERTER_TOU_WINDOWS,
        DEVICE_INVERTER,
        kind=KIND_JSON,
        access=ACCESS_RW,
        description="Time-of-Use / Eco-mode schedule",
    ),
    # --- inverter telemetry ---
    Parameter(INVERTER_PV_POWER, DEVICE_INVERTER, unit="W", description="PV power"),
    Parameter(INVERTER_AC_POWER, DEVICE_INVERTER, unit="W", description="AC power"),
    Parameter(
        INVERTER_LOAD_POWER, DEVICE_INVERTER, unit="W", description="House load power"
    ),
    Parameter(
        INVERTER_GRID_POWER,
        DEVICE_INVERTER,
        unit="W",
        description="Signed grid power (+ import, - export)",
    ),
    Parameter(INVERTER_GRID_VOLTAGE, DEVICE_INVERTER, unit="V"),
    Parameter(INVERTER_GRID_FREQUENCY, DEVICE_INVERTER, unit="Hz"),
    Parameter(INVERTER_TEMPERATURE, DEVICE_INVERTER, unit="C"),
    Parameter(INVERTER_ENERGY_PV_TODAY, DEVICE_INVERTER, unit="kWh"),
    Parameter(INVERTER_ENERGY_PV_TOTAL, DEVICE_INVERTER, unit="kWh"),
    Parameter(INVERTER_ENERGY_EXPORT_TODAY, DEVICE_INVERTER, unit="kWh"),
    Parameter(INVERTER_ENERGY_IMPORT_TODAY, DEVICE_INVERTER, unit="kWh"),
    Parameter(INVERTER_ENERGY_BATTERY_CHARGE_TODAY, DEVICE_INVERTER, unit="kWh"),
    Parameter(INVERTER_ENERGY_BATTERY_DISCHARGE_TODAY, DEVICE_INVERTER, unit="kWh"),
    # --- battery telemetry ---
    Parameter(BATTERY_SOC, DEVICE_BATTERY, unit="%", description="State of charge"),
    Parameter(BATTERY_SOH, DEVICE_BATTERY, unit="%", description="State of health"),
    Parameter(BATTERY_VOLTAGE, DEVICE_BATTERY, unit="V"),
    Parameter(BATTERY_CURRENT, DEVICE_BATTERY, unit="A"),
    Parameter(
        BATTERY_POWER,
        DEVICE_BATTERY,
        unit="W",
        description="Signed battery power (+ charging)",
    ),
    Parameter(BATTERY_MODE, DEVICE_BATTERY, kind=KIND_ENUM, enum=BATTERY_MODE_VALUES),
    Parameter(BATTERY_TEMPERATURE, DEVICE_BATTERY, unit="C"),
    Parameter(BATTERY_CELL_VOLTAGE_MIN, DEVICE_BATTERY, unit="mV"),
    Parameter(BATTERY_CELL_VOLTAGE_MAX, DEVICE_BATTERY, unit="mV"),
    Parameter(BATTERY_CELL_TEMPERATURE_MIN, DEVICE_BATTERY, unit="C"),
    Parameter(BATTERY_CELL_TEMPERATURE_MAX, DEVICE_BATTERY, unit="C"),
    Parameter(BATTERY_CHARGE_LIMIT, DEVICE_BATTERY, unit="A"),
    Parameter(BATTERY_DISCHARGE_LIMIT, DEVICE_BATTERY, unit="A"),
    Parameter(BATTERY_CAPACITY, DEVICE_BATTERY, unit="Wh", access=ACCESS_RW),
    # --- battery control ---
    Parameter(
        BATTERY_INTENT,
        DEVICE_BATTERY,
        kind=KIND_JSON,
        access=ACCESS_RW,
        description="The accepted charge/discharge request",
    ),
)

REGISTRY: Final[dict[str, Parameter]] = {p.key: p for p in _PARAMETERS}


def parameter(key: str) -> Parameter | None:
    """Return the :class:`Parameter` for *key*, or ``None``."""
    return REGISTRY.get(key)


def parameters(device: str | None = None) -> tuple[Parameter, ...]:
    """All registered parameters, optionally filtered to one device."""
    if device is None:
        return _PARAMETERS
    return tuple(p for p in _PARAMETERS if p.device == device)


def keys(device: str | None = None) -> tuple[str, ...]:
    """All canonical keys, optionally filtered to one device."""
    return tuple(p.key for p in parameters(device))


def is_writable(key: str) -> bool:
    """Whether *key* is a parameter that may be written."""
    param = REGISTRY.get(key)
    return bool(param and param.writable)


def serialize(value: Any) -> Any:
    """Turn a canonical value into something JSON-friendly."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): serialize(v) for k, v in value.items()}
    return str(value)


# --- readings and snapshots ----------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One canonical value with the time it was observed."""

    key: str
    value: Any
    updated: float
    source: str = ""

    def age(self, now: float | None = None) -> float:
        """Seconds since this reading was taken."""
        return max(0.0, (now if now is not None else time.time()) - self.updated)

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        param = REGISTRY.get(self.key)
        return {
            "key": self.key,
            "value": serialize(self.value),
            "unit": param.unit if param else "",
            "source": self.source,
            "updated": self.updated,
            "age": round(self.age(now), 3),
        }


@dataclass
class Snapshot:
    """A mutable mapping of canonical key -> :class:`Reading`.

    This is the single point where every device's parameters are combined. All
    consumers read from one shared snapshot, so the physical dongle is only ever
    touched by the proxy's own poller.
    """

    readings: dict[str, Reading] = field(default_factory=dict)

    def set(
        self,
        key: str,
        value: Any,
        *,
        source: str = "",
        now: float | None = None,
    ) -> Reading:
        """Record a value for *key* and return the new reading."""
        reading = Reading(
            key=key,
            value=value,
            updated=now if now is not None else time.time(),
            source=source,
        )
        self.readings[key] = reading
        return reading

    def update(
        self,
        values: Mapping[str, Any],
        *,
        source: str = "",
        now: float | None = None,
    ) -> None:
        """Record many values at once."""
        for key, value in values.items():
            self.set(key, value, source=source, now=now)

    def get(self, key: str, default: Any = None) -> Any:
        reading = self.readings.get(key)
        return reading.value if reading is not None else default

    def reading(self, key: str) -> Reading | None:
        return self.readings.get(key)

    def has(self, key: str) -> bool:
        return key in self.readings

    def values(self) -> dict[str, Any]:
        return {key: reading.value for key, reading in self.readings.items()}

    def serialized(self, now: float | None = None) -> dict[str, Any]:
        return {key: reading.as_dict(now) for key, reading in self.readings.items()}

    def merge(self, other: Snapshot) -> None:
        """Copy every reading of *other* into this snapshot (other wins)."""
        self.readings.update(other.readings)

    def copy(self) -> Snapshot:
        return Snapshot(readings=dict(self.readings))

    def stale(self, ttl: float, now: float | None = None) -> list[str]:
        """Keys whose reading is older than *ttl* seconds."""
        return [key for key, r in self.readings.items() if r.age(now) > ttl]

    def keys(self) -> Iterator[str]:
        return iter(self.readings)

    def __contains__(self, key: object) -> bool:
        return key in self.readings

    def __len__(self) -> int:
        return len(self.readings)

    def __iter__(self) -> Iterator[str]:
        return iter(self.readings)


@dataclass
class DeviceState:
    """Identity and health of one physical device behind the proxy."""

    device: str
    brand: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    online: bool = False
    last_error: str = ""
    last_poll: float = 0.0
    readings: Snapshot = field(default_factory=Snapshot)

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "device": self.device,
            "brand": self.brand,
            "model": self.model,
            "serial": self.serial,
            "firmware": self.firmware,
            "online": self.online,
            "last_error": self.last_error,
            "last_poll": self.last_poll,
            "readings": self.readings.serialized(now),
        }
