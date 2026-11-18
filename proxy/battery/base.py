"""The battery layer: where charge/discharge requests are accepted.

A battery is deliberately separate from an inverter. Consumers never command
registers; they express *what they want* — an :class:`proxy.intents.BatteryIntent`
— and the battery decides how to realise it. Today the only battery is a GoodWe
one that happens to live on the GoodWe inverter, so its realisation is a set of
inverter writes. A future battery of another brand will realise the same intent
over its own interface without any consumer noticing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..canonical import Snapshot
from ..intents import BatteryIntent


class Battery(ABC):
    """A battery that accepts charge/discharge requests."""

    name = "battery"
    brand = "unknown"

    def __init__(self) -> None:
        self._intent: BatteryIntent | None = None

    # -- requests ----------------------------------------------------------

    @property
    def active_intent(self) -> BatteryIntent | None:
        """The request currently in force, if any."""
        return self._intent

    def apply_intent(self, intent: BatteryIntent) -> None:
        """Accept a request and realise it on the physical battery."""
        self._intent = intent
        self._realise(intent)

    def observe_intent(self, intent: BatteryIntent) -> None:
        """Record a request that is already realised elsewhere.

        Used when a consumer writes a schedule straight to the device: the
        schedule *is* the realisation, so the battery records what it means
        without issuing writes of its own.
        """
        self._intent = intent

    def clear_intent(self) -> None:
        """Drop any request and let the battery fall back to self-consumption."""
        self.apply_intent(
            BatteryIntent.self_consumption(source="clear", note="no active request")
        )

    def tick(self, now: float | None = None) -> None:
        """Expire the active request when its deadline has passed."""
        if self._intent is None:
            return
        if self._intent.expired(now):
            self.clear_intent()

    def supports_intent(self, intent: BatteryIntent) -> bool:
        return True

    @abstractmethod
    def _realise(self, intent: BatteryIntent) -> None:
        """Make the physical battery follow *intent*."""

    # -- telemetry ---------------------------------------------------------

    @abstractmethod
    def read_state(self) -> Snapshot:
        """The battery's canonical readings (``battery.*`` keys)."""

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "brand": self.brand}
