"""HA Inverter Energy Proxy.

A single cached proxy in front of your inverters and batteries.

Programs that used to hammer the inverter's Wi-Fi dongle talk to this proxy
instead. It presents one combined, canonical view of every device, answers
consumers in the protocol they already speak (starting with the GoodWe UDP
dongle protocol), and translates their writes to whatever the real device —
possibly a different brand — needs.

The proxy is split so it can scale across brands and network identities:

* :mod:`proxy.canonical` — the brand-independent parameter registry;
* :mod:`proxy.drivers` — brand-specific register maps (GoodWe today);
* :mod:`proxy.battery` — the separated charge/discharge request boundary;
* :mod:`proxy.transport` — the dumb byte-moving layer;
* :mod:`proxy.server` — consumer-facing facades plus the core link between them;
* :mod:`proxy.poller` — the one place devices are polled and cached;
* :mod:`proxy.core` — the ``all`` / ``core`` / ``facade`` services.
"""

from .canonical import Snapshot
from .core import CoreService, EnergyProxy, FacadeService, build_service
from .intents import BatteryIntent, IntentMode
from .version import VERSION

__all__ = [
    "BatteryIntent",
    "CoreService",
    "EnergyProxy",
    "FacadeService",
    "IntentMode",
    "Snapshot",
    "VERSION",
    "build_service",
]
