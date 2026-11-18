"""The brand-independent battery charge/discharge request.

A consumer never tells the proxy "write register 47511". It says *what it wants*:
"I want to charge the battery at 4 kW until 15:00", or "discharge", or "just
follow the house". The battery part of the proxy accepts that request — an
:class:`BatteryIntent` — and is free to realise it however the battery brand
requires. The GoodWe battery, which happens to live on the GoodWe inverter,
realises it with the inverter's EMS registers; a future brand would realise the
same request its own way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .schedule import Schedule, ScheduleWindow


class IntentMode(str, Enum):
    """What the requester wants the battery to do."""

    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"
    SELF_CONSUMPTION = "self_consumption"

    @property
    def sign(self) -> int:
        """+1 charges, -1 discharges, 0 leaves the battery alone."""
        if self is IntentMode.CHARGE:
            return 1
        if self is IntentMode.DISCHARGE:
            return -1
        return 0


@dataclass(frozen=True)
class BatteryIntent:
    """A request to charge, discharge, idle or self-consume."""

    mode: IntentMode = IntentMode.IDLE
    #: Requested power in W (magnitude). ``None`` means "as much as is sensible".
    power_w: float | None = None
    #: Requested power as a percentage of the battery's rating, when W is unknown.
    power_percent: float | None = None
    target_soc: float | None = None
    min_soc: float | None = None
    max_soc: float | None = None
    #: Epoch seconds after which the request is no longer valid.
    deadline: float | None = None
    source: str = "external"
    created: float = field(default_factory=time.time)
    note: str = ""

    def expired(self, now: float | None = None) -> bool:
        if self.deadline is None:
            return False
        return (now if now is not None else time.time()) >= self.deadline

    def remaining(self, now: float | None = None) -> float | None:
        """Seconds until the request expires, or ``None`` when open-ended."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - (now if now is not None else time.time()))

    def with_power_w(self, power_w: float | None) -> BatteryIntent:
        return replace(self, power_w=power_w)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "power_w": self.power_w,
            "power_percent": self.power_percent,
            "target_soc": self.target_soc,
            "min_soc": self.min_soc,
            "max_soc": self.max_soc,
            "deadline": self.deadline,
            "remaining": None if self.deadline is None else round(self.remaining(), 1),
            "source": self.source,
            "created": self.created,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatteryIntent:
        mode = data.get("mode", IntentMode.IDLE)
        return cls(
            mode=IntentMode(mode) if not isinstance(mode, IntentMode) else mode,
            power_w=data.get("power_w"),
            power_percent=data.get("power_percent"),
            target_soc=data.get("target_soc"),
            min_soc=data.get("min_soc"),
            max_soc=data.get("max_soc"),
            deadline=data.get("deadline"),
            source=data.get("source", "external"),
            note=data.get("note", ""),
        )

    # -- convenience constructors -----------------------------------------

    @classmethod
    def idle(cls, **kwargs: Any) -> BatteryIntent:
        return cls(mode=IntentMode.IDLE, **kwargs)

    @classmethod
    def charge(cls, **kwargs: Any) -> BatteryIntent:
        return cls(mode=IntentMode.CHARGE, **kwargs)

    @classmethod
    def discharge(cls, **kwargs: Any) -> BatteryIntent:
        return cls(mode=IntentMode.DISCHARGE, **kwargs)

    @classmethod
    def self_consumption(cls, **kwargs: Any) -> BatteryIntent:
        return cls(mode=IntentMode.SELF_CONSUMPTION, **kwargs)


def intent_for_window(
    window: ScheduleWindow | None,
    *,
    source: str = "schedule",
    deadline: float | None = None,
    power_w: float | None = None,
) -> BatteryIntent:
    """Turn the active schedule window into a battery request."""
    if window is None or not window.enabled:
        return BatteryIntent.idle(source=source, deadline=deadline)
    mode = IntentMode.CHARGE if window.mode == "charge" else IntentMode.DISCHARGE
    return BatteryIntent(
        mode=mode,
        power_w=power_w,
        power_percent=float(window.power),
        source=source,
        deadline=deadline,
        note=f"{window.start:%H:%M}-{window.stop:%H:%M}",
    )


def intent_for_schedule(
    schedule: Schedule,
    *,
    when: Any = None,
    source: str = "schedule",
    power_w: float | None = None,
) -> BatteryIntent:
    """The battery request implied by *schedule* at *when*."""
    window = schedule.active(when)
    deadline = None
    if window is not None:
        import datetime as _dt

        moment = when or _dt.datetime.now()
        stop = moment.replace(
            hour=window.stop.hour,
            minute=window.stop.minute,
            second=0,
            microsecond=0,
        )
        if stop <= moment:
            stop += _dt.timedelta(days=1)
        deadline = stop.timestamp()
    return intent_for_window(
        window, source=source, deadline=deadline, power_w=power_w
    )
