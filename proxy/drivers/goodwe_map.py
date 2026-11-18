"""The GoodWe GW/ET-family register map, as data.

This is the brand knowledge for GoodWe, kept deliberately declarative so the
driver stays thin and the map is easy to extend or override. The offsets,
scales and layouts come from two sources that agree with each other:

* the open-source ``goodwe`` library (``goodwe/et.py`` and ``goodwe/sensor.py``),
  which documents the ET/EH/BT/BH families;
* the ``ha-goodwe-10k-ET-tou`` integration, whose register constants and Eco-mode
  encoding have been verified against a live GW10K-ET dongle.

Scales (from the ``goodwe`` library):

===============  ==========================================
kind             encoding
===============  ==========================================
``u16`` / ``s16``  raw register, unsigned / signed
``u32`` / ``s32``  two registers, big-endian, unsigned / signed
``voltage``      ``u16 / 10``  (V)
``current``      ``u16 / 10``  (A)
``current_s``    ``s16 / 10``  (A)
``freq``         ``s16 / 100`` (Hz)
``temp``         ``s16 / 10``  (degC)
``energy``       ``u16 / 10``  (kWh)
``energy4``      ``u32 / 10``  (kWh)
``cell_mv``      raw register, mV
``bool``         ``u16 != 0``
``byteh``/``bytel``  high / low byte of the register
``enum``         mapped through the field's ``enum`` table
===============  ==========================================
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..canonical import (
    BATTERY_CELL_TEMPERATURE_MAX,
    BATTERY_CELL_TEMPERATURE_MIN,
    BATTERY_CELL_VOLTAGE_MAX,
    BATTERY_CELL_VOLTAGE_MIN,
    BATTERY_CHARGE_LIMIT,
    BATTERY_CURRENT,
    BATTERY_DISCHARGE_LIMIT,
    BATTERY_MODE,
    BATTERY_POWER,
    BATTERY_SOC,
    BATTERY_SOH,
    BATTERY_TEMPERATURE,
    BATTERY_VOLTAGE,
    INVERTER_AC_POWER,
    INVERTER_EMS_MODE,
    INVERTER_EMS_POWER_LIMIT,
    INVERTER_ENERGY_BATTERY_CHARGE_TODAY,
    INVERTER_ENERGY_BATTERY_DISCHARGE_TODAY,
    INVERTER_ENERGY_EXPORT_TODAY,
    INVERTER_ENERGY_IMPORT_TODAY,
    INVERTER_ENERGY_PV_TODAY,
    INVERTER_ENERGY_PV_TOTAL,
    INVERTER_EXPORT_ENABLED,
    INVERTER_EXPORT_LIMIT,
    INVERTER_FIRMWARE,
    INVERTER_GRID_FREQUENCY,
    INVERTER_GRID_POWER,
    INVERTER_GRID_VOLTAGE,
    INVERTER_LOAD_POWER,
    INVERTER_MODEL,
    INVERTER_PV_POWER,
    INVERTER_RATED_POWER,
    INVERTER_SERIAL,
    INVERTER_STATUS_MODE,
    INVERTER_TEMPERATURE,
    INVERTER_TOU_WINDOWS,
    INVERTER_WORK_MODE,
)
from ..schedule import ALL_DAYS, MODE_CHARGE, MODE_DISCHARGE, MODE_OFF, Schedule, ScheduleWindow

# --- enumerations ---------------------------------------------------------

WORK_MODE_ENUM: Mapping[int, str] = {
    0: "general",
    1: "off_grid",
    2: "backup",
    3: "eco",
    4: "peak_shaving",
    5: "self_use",
}

STATUS_MODE_ENUM: Mapping[int, str] = {
    0: "wait",
    1: "normal",
    2: "off_grid",
    3: "fault",
    4: "flash",
    5: "check",
}

EMS_MODE_ENUM: Mapping[int, str] = {
    1: "auto",
    2: "charge_pv",
    3: "discharge_pv",
    4: "import_ac",
    5: "export_ac",
    6: "conserve",
    7: "off_grid",
    8: "battery_standby",
    9: "buy_power",
    10: "sell_power",
}

BATTERY_MODE_ENUM: Mapping[int, str] = {
    0: "none",
    1: "standby",
    2: "discharge",
    3: "charge",
    4: "to_charge",
    5: "to_discharge",
}

# --- register-map data types ---------------------------------------------


@dataclass(frozen=True)
class Field:
    """One canonical value decoded from a register (or two)."""

    index: int
    key: str
    kind: str
    unit: str = ""
    enum: Mapping[int, str] | None = None
    size: int = 1


@dataclass(frozen=True)
class Derived:
    """A canonical value computed from the whole block's registers."""

    key: str
    func: Callable[[list[int]], Any]


@dataclass(frozen=True)
class Block:
    """A contiguous register span with its fields and derived values."""

    name: str
    offset: int
    count: int
    fields: tuple[Field, ...] = ()
    derived: tuple[Derived, ...] = ()
    optional: bool = False


# --- low-level register helpers ------------------------------------------


def u16(regs: list[int], index: int) -> int:
    return regs[index] & 0xFFFF


def s16(regs: list[int], index: int) -> int:
    value = regs[index] & 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


def u32(regs: list[int], index: int) -> int:
    return ((regs[index] & 0xFFFF) << 16) | (regs[index + 1] & 0xFFFF)


def s32(regs: list[int], index: int) -> int:
    value = u32(regs, index)
    return value - 0x100000000 if value >= 0x80000000 else value


def regs_to_bytes(regs: list[int]) -> bytes:
    return b"".join((reg & 0xFFFF).to_bytes(2, "big") for reg in regs)


def decode_string(regs: list[int], index: int, length: int) -> str:
    """Decode a fixed-length, NUL-padded ASCII string starting at *index*."""
    data = regs_to_bytes(regs)[index * 2 : index * 2 + length]
    return data.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def decode_time(register: int) -> dt.time:
    register &= 0xFFFF
    hour, minute = register >> 8, register & 0xFF
    return dt.time(hour=hour % 24, minute=minute % 60)


def encode_time(value: dt.time) -> int:
    return value.hour * 256 + value.minute


# --- field decoding / encoding -------------------------------------------


def decode_field(field_: Field, regs: list[int]) -> Any:
    kind = field_.kind
    if kind == "u16":
        return u16(regs, field_.index)
    if kind == "s16":
        return s16(regs, field_.index)
    if kind == "u32":
        return u32(regs, field_.index)
    if kind == "s32":
        return s32(regs, field_.index)
    if kind == "voltage":
        return round(u16(regs, field_.index) / 10, 1)
    if kind == "current":
        return round(u16(regs, field_.index) / 10, 1)
    if kind == "current_s":
        return round(s16(regs, field_.index) / 10, 1)
    if kind == "freq":
        return round(s16(regs, field_.index) / 100, 2)
    if kind == "temp":
        raw = s16(regs, field_.index)
        return None if raw in (-1, 32767) else round(raw / 10, 1)
    if kind == "energy":
        return round(u16(regs, field_.index) / 10, 1)
    if kind == "energy4":
        return round(u32(regs, field_.index) / 10, 1)
    if kind == "cell_mv":
        return u16(regs, field_.index)
    if kind == "bool":
        return u16(regs, field_.index) != 0
    if kind == "byteh":
        return (u16(regs, field_.index) >> 8) & 0xFF
    if kind == "bytel":
        return u16(regs, field_.index) & 0xFF
    if kind == "enum":
        raw = u16(regs, field_.index)
        if field_.enum and raw in field_.enum:
            return field_.enum[raw]
        return raw
    raise ValueError(f"unknown field kind {kind!r}")


def encode_field(field_: Field, value: Any) -> list[int]:
    """Encode a canonical value into the register(s) it occupies."""
    kind = field_.kind
    if kind == "bool":
        return [1 if value else 0]
    if kind == "enum":
        if field_.enum:
            reverse = {name: raw for raw, name in field_.enum.items()}
            if isinstance(value, str):
                if value not in reverse:
                    raise ValueError(f"{value!r} is not a valid {field_.key}")
                return [reverse[value]]
        return [int(value) & 0xFFFF]
    if kind == "voltage":
        return [round(float(value) * 10) & 0xFFFF]
    if kind == "current":
        return [round(float(value) * 10) & 0xFFFF]
    if kind == "current_s":
        return [round(float(value) * 10) & 0xFFFF]
    if kind == "freq":
        return [round(float(value) * 100) & 0xFFFF]
    if kind == "temp":
        return [round(float(value) * 10) & 0xFFFF]
    if kind == "energy":
        return [round(float(value) * 10) & 0xFFFF]
    if kind in ("u32", "s32", "energy4"):
        raw = int(value)
        if kind == "energy4":
            raw = round(float(value) * 10)
        raw &= 0xFFFFFFFF
        return [(raw >> 16) & 0xFFFF, raw & 0xFFFF]
    if kind in ("u16", "s16", "cell_mv", "byteh", "bytel"):
        return [int(value) & 0xFFFF]
    raise ValueError(f"unknown field kind {kind!r}")


# --- Time-of-Use window decoding / encoding ------------------------------


def decode_windows_v1(regs: list[int]) -> Schedule:
    """Decode the ARM-firmware-<19 Eco-mode layout: 4 registers per window."""
    windows: list[ScheduleWindow] = []
    for base in range(0, len(regs) - 3, 4):
        start = decode_time(regs[base])
        stop = decode_time(regs[base + 1])
        power = s16(regs, base + 2)
        on_off = (regs[base + 3] >> 8) & 0xFF
        days = regs[base + 3] & 0xFF
        if power < 0:
            mode, magnitude = MODE_CHARGE, -power
        elif power > 0:
            mode, magnitude = MODE_DISCHARGE, power
        else:
            mode, magnitude = MODE_OFF, 0
        if on_off == 0:
            mode = MODE_OFF
        windows.append(
            ScheduleWindow(start, stop, mode=mode, power=magnitude, days=days)
        )
    return Schedule(windows)


def encode_windows_v1(schedule: Schedule, count: int = 4) -> list[int]:
    """Encode a schedule into the ARM-firmware-<19 Eco-mode layout."""
    windows = list(schedule.windows)[:count]
    while len(windows) < count:
        windows.append(ScheduleWindow(dt.time(0, 0), dt.time(0, 0), MODE_OFF, 0, 0))
    regs: list[int] = []
    for window in windows:
        regs.append(encode_time(window.start))
        regs.append(encode_time(window.stop))
        regs.append(window.signed_power & 0xFFFF)
        on_off = 0xFF if window.enabled else 0x00
        regs.append((on_off << 8) | (window.days & 0xFF))
    return regs


def decode_windows_v2(regs: list[int]) -> Schedule:
    """Decode the ARM-firmware->=19 Eco-mode layout: 6 registers per window."""
    windows: list[ScheduleWindow] = []
    for base in range(0, len(regs) - 5, 6):
        start = decode_time(regs[base])
        stop = decode_time(regs[base + 1])
        on_off = (regs[base + 2] >> 8) & 0xFF
        days = regs[base + 2] & 0xFF
        power = s16(regs, base + 3)
        if power < 0:
            mode, magnitude = MODE_CHARGE, -power
        elif power > 0:
            mode, magnitude = MODE_DISCHARGE, power
        else:
            mode, magnitude = MODE_OFF, 0
        if on_off == 0:
            mode = MODE_OFF
        windows.append(
            ScheduleWindow(start, stop, mode=mode, power=magnitude, days=days)
        )
    return Schedule(windows)


def encode_windows_v2(schedule: Schedule, count: int = 4) -> list[int]:
    """Encode a schedule into the ARM-firmware->=19 Eco-mode layout."""
    windows = list(schedule.windows)[:count]
    while len(windows) < count:
        windows.append(ScheduleWindow(dt.time(0, 0), dt.time(0, 0), MODE_OFF, 0, 0))
    regs: list[int] = []
    for window in windows:
        regs.append(encode_time(window.start))
        regs.append(encode_time(window.stop))
        on_off = 0xFF if window.enabled else 0x00
        regs.append((on_off << 8) | (window.days & 0xFF))
        regs.append(window.signed_power & 0xFFFF)
        regs.append(100)  # SoC target
        regs.append(0)  # month bits (all months)
    return regs


def _pv_power(regs: list[int]) -> int:
    total = 0
    for index in (5, 9, 13, 17):
        if index + 1 < len(regs):
            total += max(0, s32(regs, index))
    return total


# --- the blocks -----------------------------------------------------------


DEVICE_INFO = Block(
    name="device_info",
    offset=35000,
    count=33,
    optional=True,
    fields=(
        Field(1, INVERTER_RATED_POWER, "u16", unit="W"),
    ),
    derived=(
        Derived(INVERTER_SERIAL, lambda regs: decode_string(regs, 3, 16)),
        Derived(INVERTER_MODEL, lambda regs: decode_string(regs, 11, 10)),
        Derived(INVERTER_FIRMWARE, lambda regs: decode_string(regs, 21, 12)),
    ),
)

RUNTIME = Block(
    name="runtime",
    offset=35100,
    count=125,
    fields=(
        Field(21, INVERTER_GRID_VOLTAGE, "voltage", unit="V"),
        Field(23, INVERTER_GRID_FREQUENCY, "freq", unit="Hz"),
        Field(38, INVERTER_AC_POWER, "s16", unit="W"),
        Field(40, INVERTER_GRID_POWER, "s16", unit="W"),
        Field(72, INVERTER_LOAD_POWER, "s16", unit="W"),
        Field(76, INVERTER_TEMPERATURE, "temp", unit="C"),
        Field(80, BATTERY_VOLTAGE, "voltage", unit="V"),
        Field(81, BATTERY_CURRENT, "current_s", unit="A"),
        Field(82, BATTERY_POWER, "s32", unit="W"),
        Field(84, BATTERY_MODE, "enum", enum=BATTERY_MODE_ENUM),
        Field(87, INVERTER_STATUS_MODE, "enum", enum=STATUS_MODE_ENUM),
        Field(91, INVERTER_ENERGY_PV_TOTAL, "energy4", unit="kWh"),
        Field(93, INVERTER_ENERGY_PV_TODAY, "energy4", unit="kWh"),
        Field(99, INVERTER_ENERGY_EXPORT_TODAY, "energy", unit="kWh"),
        Field(102, INVERTER_ENERGY_IMPORT_TODAY, "energy", unit="kWh"),
        Field(108, INVERTER_ENERGY_BATTERY_CHARGE_TODAY, "energy", unit="kWh"),
        Field(111, INVERTER_ENERGY_BATTERY_DISCHARGE_TODAY, "energy", unit="kWh"),
    ),
    derived=(Derived(INVERTER_PV_POWER, _pv_power),),
)

BATTERY_INFO = Block(
    name="battery_info",
    offset=37000,
    count=24,
    optional=True,
    fields=(
        Field(3, BATTERY_TEMPERATURE, "temp", unit="C"),
        Field(4, BATTERY_CHARGE_LIMIT, "u16", unit="A"),
        Field(5, BATTERY_DISCHARGE_LIMIT, "u16", unit="A"),
        Field(7, BATTERY_SOC, "u16", unit="%"),
        Field(8, BATTERY_SOH, "u16", unit="%"),
        Field(20, BATTERY_CELL_TEMPERATURE_MAX, "temp", unit="C"),
        Field(21, BATTERY_CELL_TEMPERATURE_MIN, "temp", unit="C"),
        Field(22, BATTERY_CELL_VOLTAGE_MAX, "cell_mv", unit="mV"),
        Field(23, BATTERY_CELL_VOLTAGE_MIN, "cell_mv", unit="mV"),
    ),
)

METER = Block(
    name="meter",
    offset=36000,
    count=45,
    optional=True,
)

WORK_MODE = Block(
    name="work_mode",
    offset=47000,
    count=1,
    fields=(Field(0, INVERTER_WORK_MODE, "enum", enum=WORK_MODE_ENUM),),
)

EXPORT = Block(
    name="export",
    offset=47509,
    count=2,
    fields=(
        Field(0, INVERTER_EXPORT_ENABLED, "bool"),
        Field(1, INVERTER_EXPORT_LIMIT, "u16", unit="W"),
    ),
)

EMS = Block(
    name="ems",
    offset=47511,
    count=2,
    fields=(
        Field(0, INVERTER_EMS_MODE, "enum", enum=EMS_MODE_ENUM),
        Field(1, INVERTER_EMS_POWER_LIMIT, "s16", unit="W"),
    ),
)

TOU_V1 = Block(
    name="tou_v1",
    offset=47515,
    count=16,
    derived=(Derived(INVERTER_TOU_WINDOWS, decode_windows_v1),),
)

TOU_V2 = Block(
    name="tou_v2",
    offset=47547,
    count=24,
    optional=True,
    derived=(Derived(INVERTER_TOU_WINDOWS, decode_windows_v2),),
)

#: The default map: one poll touches each of these spans once.
DEFAULT_BLOCKS: tuple[Block, ...] = (
    DEVICE_INFO,
    RUNTIME,
    METER,
    BATTERY_INFO,
    WORK_MODE,
    EXPORT,
    EMS,
    TOU_V1,
)

#: Extra spans that may be enabled when the firmware supports them.
OPTIONAL_BLOCKS: tuple[Block, ...] = (TOU_V2,)

#: Offsets whose single-register write changes the whole Time-of-Use schedule.
TOU_WRITE_RANGES: tuple[tuple[int, int], ...] = (
    (47515, 16),
    (47547, 24),
)

#: Registers the proxy never forwards blindly to the device.
PROTECTED_OFFSETS: frozenset[int] = frozenset()


def windows_for_block(block: Block, schedule: Schedule) -> list[int]:
    """Encode *schedule* in the layout of the given Time-of-Use block."""
    if block.name == "tou_v2":
        return encode_windows_v2(schedule)
    return encode_windows_v1(schedule)


__all__ = [
    "Block",
    "Derived",
    "Field",
    "DEFAULT_BLOCKS",
    "OPTIONAL_BLOCKS",
    "TOU_WRITE_RANGES",
    "decode_field",
    "decode_string",
    "decode_windows_v1",
    "decode_windows_v2",
    "encode_field",
    "encode_time",
    "encode_windows_v1",
    "encode_windows_v2",
    "regs_to_bytes",
    "u16",
    "u32",
    "s16",
    "s32",
    "windows_for_block",
]
