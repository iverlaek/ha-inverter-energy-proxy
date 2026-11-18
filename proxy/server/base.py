"""The server layer: how consumers talk to the proxy.

A server (a *facade*) exposes the proxy to the outside world. The first — and for
now only — facade makes the proxy look like a GoodWe Wi-Fi dongle on UDP port
8899, so an existing GoodWe integration can point at the proxy's address
unchanged.

The facade deliberately holds no brand knowledge and no device state. It only
speaks the consumer's wire protocol and forwards to a :class:`CoreLink`, which
may be the core running in the same process or a core reached over the network.
That split is what lets each facade run as its own container with its own IP
address on a macvlan network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

#: A write sink receives the canonical consequence of a consumer write.
#: Kept for the in-process core; facades use :class:`CoreLink` instead.
WriteSink = Callable[[Any], None]


class CoreError(Exception):
    """Raised when the core cannot answer a facade's request."""


class CoreLink(ABC):
    """What a facade needs from the core.

    ``read`` returns the cached registers for a device; ``write`` decodes and
    applies a raw register write. Both are brand-agnostic — the core decides
    what the registers mean.
    """

    #: Which device (driver name) this link fronts.
    device: str = "goodwe"

    @abstractmethod
    def read(self, offset: int, count: int) -> list[int]:
        """Return ``count`` cached registers starting at *offset*."""

    @abstractmethod
    def write(self, offset: int, value: int) -> None:
        """Decode and apply a raw register write."""

    def snapshot(self) -> dict[str, Any]:
        """The canonical readings, if the link can provide them."""
        return {}

    def status(self) -> dict[str, Any]:
        """Core health, if the link can provide it."""
        return {}

    def close(self) -> None:  # pragma: no cover - nothing to release by default
        """Release any resources held by the link."""


@dataclass
class ServerStats:
    """Simple counters a server keeps about the traffic it has seen."""

    requests: int = 0
    reads: int = 0
    writes: int = 0
    bad_frames: int = 0
    rejected: int = 0
    errors: int = 0
    clients: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "reads": self.reads,
            "writes": self.writes,
            "bad_frames": self.bad_frames,
            "rejected": self.rejected,
            "errors": self.errors,
            "clients": sorted(self.clients),
        }


class Server(ABC):
    """A consumer-facing interface to the proxy."""

    name = "server"

    @abstractmethod
    async def start(self) -> None:
        """Begin serving."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop serving and release resources."""
