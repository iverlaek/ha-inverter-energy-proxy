"""SolarEdge driver — paused.

SolarEdge inverters are read over their Modbus TCP interface (port 502) or the
SolarEdge monitoring API. Neither is wired up yet: this placeholder keeps the
brand on the map so the canonical model stays honest about what is planned.
"""

from __future__ import annotations

from .stub import PausedDriver


class SolarEdgeDriver(PausedDriver):
    """Placeholder for a SolarEdge inverter (Modbus TCP / monitoring API)."""

    name = "solaredge"
    brand = "SolarEdge"
    plan = (
        "Read canonical telemetry over Modbus TCP; map SolarEdge's single "
        "Power/Energy registers and its battery (if any) onto the canonical model."
    )
