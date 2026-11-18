"""Brand-independent Time-of-Use / Eco-mode schedules.

A schedule is the *when*: a list of windows describing, per window, whether the
battery should charge or discharge and at what power. A schedule is not a
command — it is the plan a consumer (or a planner) wants the inverter to follow.
The :mod:`proxy.intents` module turns the active window of a schedule into a
charge/discharge *request*.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any, Iterable

MODE_OFF = "off"
MODE_CHARGE = "charge"
MODE_DISCHARGE = "discharge"
MODES = (MODE_OFF, MODE_CHARGE, MODE_DISCHARGE)

#: Day names in the inverter's bit order (bit 0 = Sunday .. bit 6 = Saturday).
DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
ALL_DAYS = 0x7F
WEEKDAYS = 0x3E
WEEKEND = 0x41


def day_bit(day: dt.date | int) -> int:
    """The schedule day bit for a date or a Python weekday index (Mon = 0)."""
    weekday = day.weekday() if isinstance(day, dt.date) else int(day)
    return 1 << ((weekday + 1) % 7)


def days_to_mask(text: str) -> int:
    """Parse ``all``/``weekdays``/``weekend``/``none`` or a day-name list."""
    normalized = (text or "").strip().lower()
    if not normalized or normalized in {"none", "off", "-"}:
        return 0
    if "all" in normalized or "every" in normalized or "mon-sun" in normalized:
        return ALL_DAYS
    if "weekday" in normalized:
        return WEEKDAYS
    if "weekend" in normalized:
        return WEEKEND
    mask = 0
    for index, name in enumerate(DAY_NAMES):
        if name in normalized:
            mask |= 1 << index
    return mask


def mask_to_days(mask: int) -> str:
    """Format a day bitmask as a comma-separated list of day names."""
    mask &= 0xFF
    names = [name for index, name in enumerate(DAY_NAMES) if mask & (1 << index)]
    return ",".join(names) if names else "none"


def _coerce_time(value: Any) -> dt.time:
    if isinstance(value, dt.time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        hour, _, minute = value.partition(":")
        return dt.time(hour=int(hour) % 24, minute=int(minute or 0) % 60)
    if isinstance(value, int):
        return dt.time(hour=(value >> 8) & 0xFF, minute=value & 0xFF)
    raise TypeError(f"cannot interpret {value!r} as a time")


@dataclass(frozen=True)
class ScheduleWindow:
    """One charge/discharge window of a Time-of-Use schedule."""

    start: dt.time
    stop: dt.time
    mode: str = MODE_OFF
    power: int = 100
    days: int = ALL_DAYS

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", _coerce_time(self.start))
        object.__setattr__(self, "stop", _coerce_time(self.stop))
        object.__setattr__(self, "mode", self.mode if self.mode in MODES else MODE_OFF)
        object.__setattr__(self, "power", max(0, min(100, int(self.power))))
        object.__setattr__(self, "days", int(self.days) & 0xFF)

    @property
    def enabled(self) -> bool:
        return self.mode != MODE_OFF

    @property
    def signed_power(self) -> int:
        """Signed percent: negative charges, positive discharges, 0 idles."""
        if self.mode == MODE_CHARGE:
            return -abs(self.power)
        if self.mode == MODE_DISCHARGE:
            return abs(self.power)
        return 0

    def covers(self, when: dt.datetime) -> bool:
        """Whether this window is active at *when* (day bit and time range)."""
        if not self.enabled:
            return False
        if not self.days & day_bit(when.date()):
            return False
        moment = when.time()
        if self.start <= self.stop:
            return self.start <= moment < self.stop
        # A window that wraps past midnight, e.g. 22:00 -> 06:00.
        return moment >= self.start or moment < self.stop

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.strftime("%H:%M"),
            "stop": self.stop.strftime("%H:%M"),
            "mode": self.mode,
            "power": self.power,
            "days": mask_to_days(self.days),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleWindow:
        days = data.get("days", ALL_DAYS)
        if isinstance(days, str):
            days = days_to_mask(days)
        return cls(
            start=_coerce_time(data["start"]),
            stop=_coerce_time(data["stop"]),
            mode=data.get("mode", MODE_OFF),
            power=int(data.get("power", 100)),
            days=int(days),
        )

    def with_mode(self, mode: str, power: int | None = None) -> ScheduleWindow:
        return replace(
            self, mode=mode, power=self.power if power is None else int(power)
        )


@dataclass
class Schedule:
    """An ordered set of :class:`ScheduleWindow`."""

    windows: tuple[ScheduleWindow, ...] = ()

    def __init__(self, windows: Iterable[ScheduleWindow] = ()) -> None:
        self.windows = tuple(windows)

    def active(self, when: dt.datetime | None = None) -> ScheduleWindow | None:
        """The first window covering *when*, or ``None``."""
        when = when or dt.datetime.now()
        for window in self.windows:
            if window.covers(when):
                return window
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"windows": [w.to_dict() for w in self.windows]}

    def as_list(self) -> list[dict[str, Any]]:
        return [w.to_dict() for w in self.windows]

    @classmethod
    def from_dict(cls, data: Any) -> Schedule:
        if data is None:
            return cls()
        if isinstance(data, Schedule):
            return data
        if isinstance(data, dict):
            items = data.get("windows", [])
        else:
            items = data
        return cls(ScheduleWindow.from_dict(item) for item in items)

    def __iter__(self):
        return iter(self.windows)

    def __len__(self) -> int:
        return len(self.windows)
