"""The driver layer: brand-specific meaning on top of a transport.

A driver owns exactly one physical device. It knows that device's register map
and translates it to and from the canonical model in :mod:`proxy.canonical`.
Drivers are the *only* place where brand knowledge lives.

Two things make the proxy more than a thin passthrough:

* a driver keeps a cached **register image** — the raw ``{offset: value}`` map
  from its last poll — so the dongle facade can answer any consumer read
  without touching the real device again;
* a driver can **decode a raw register write** into canonical writes, so a
  consumer that speaks the brand's protocol is understood by the proxy rather
  than blindly forwarded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Final

from ..canonical import Snapshot

WRITE_CANONICAL: Final = "canonical"
WRITE_RAW: Final = "raw"


@dataclass(frozen=True)
class DeviceInfo:
    """Identity of a device as reported by its driver."""

    brand: str
    model: str = ""
    serial: str = ""
    firmware: str = ""
    rated_power: float | None = None


@dataclass(frozen=True)
class WriteRequest:
    """The canonical consequence of a raw register write.

    ``kind`` is either :data:`WRITE_CANONICAL` (the caller should apply
    ``key``/``value`` through the driver) or :data:`WRITE_RAW` (the offset and
    value should be forwarded to the device unchanged).
    """

    kind: str
    key: str | None = None
    value: Any = None
    offset: int | None = None
    raw_value: int | None = None

    @classmethod
    def canonical(cls, key: str, value: Any) -> WriteRequest:
        return cls(kind=WRITE_CANONICAL, key=key, value=value)

    @classmethod
    def raw(cls, offset: int, value: int) -> WriteRequest:
        return cls(kind=WRITE_RAW, offset=offset, raw_value=value & 0xFFFF)


@dataclass
class RegisterBlock:
    """A contiguous span of registers a driver reads on every poll."""

    name: str
    offset: int
    count: int
    optional: bool = False


class InverterDriver(ABC):
    """Brand-specific access to one inverter."""

    name: str = "driver"
    brand: str = "unknown"

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Prepare the transport (open sockets, probe the device)."""

    def close(self) -> None:
        """Release the transport."""

    def configure(self, host: str, port: int | None = None) -> None:
        """Retarget the physical device at runtime; no-op by default."""

    # -- canonical model ---------------------------------------------------

    @abstractmethod
    def read_state(self) -> Snapshot:
        """Poll the device and return its canonical readings.

        The returned snapshot may contain both ``inverter.*`` and ``battery.*``
        keys: for many hybrid inverters the battery is an integrated part of the
        inverter, and one poll reads both.
        """

    @abstractmethod
    def write(self, key: str, value: Any) -> None:
        """Write a canonical parameter to the device."""

    def capabilities(self) -> frozenset[str]:
        """Canonical keys this driver can read or write."""
        return frozenset()

    def supports(self, key: str) -> bool:
        return key in self.capabilities()

    def info(self) -> DeviceInfo:
        return DeviceInfo(brand=self.brand)

    # -- register image (for the dongle facade) ----------------------------

    def blocks(self) -> tuple[RegisterBlock, ...]:
        """The register spans this driver polls."""
        return ()

    def image(self) -> dict[int, int]:
        """The cached raw register image from the last poll."""
        return {}

    def image_block(self, offset: int, count: int) -> list[int]:
        """``count`` registers from the image, zero-filling the unknown ones."""
        image = self.image()
        return [image.get(offset + i, 0) for i in range(count)]

    def decode_write(self, offset: int, value: int) -> list[WriteRequest]:
        """Interpret a raw register write from a consumer."""
        return [WriteRequest.raw(offset, value)]

    def write_raw(self, offset: int, value: int) -> None:
        """Forward a raw register write straight to the device."""
        raise NotImplementedError(f"{self.name} cannot write raw registers")

    # -- helpers -----------------------------------------------------------

    def _read_block(self, transport: Any, block: RegisterBlock) -> list[int] | None:
        """Read one block, returning ``None`` when it is absent or fails."""
        try:
            return transport.read_registers(block.offset, block.count)
        except Exception:  # noqa: BLE001 - a missing optional block is not fatal
            if block.optional:
                return None
            raise
