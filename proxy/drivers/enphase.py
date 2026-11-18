"""Enphase driver — paused.

Enphase systems are read through the Envoy local API (JSON) or its Modbus TCP
gateway. Neither is wired up yet: this placeholder keeps the brand on the map so
the canonical model stays honest about what is planned.
"""

from __future__ import annotations

from .stub import PausedDriver


class EnphaseDriver(PausedDriver):
    """Placeholder for an Enphase Envoy / IQ battery system."""

    name = "enphase"
    brand = "Enphase"
    plan = (
        "Read the Envoy local JSON API for PV, load and battery data and map "
        "IQ battery charge/discharge control onto the canonical battery intent."
    )
