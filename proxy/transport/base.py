"""The transport layer: how bytes reach a device.

A transport is deliberately dumb — it knows how to move register reads and
writes over some link (GoodWe's UDP dongle protocol, Modbus TCP, a serial bus,
a vendor cloud API). It knows nothing about inverters, batteries or the
canonical model. Drivers sit on top of a transport and give it meaning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TransportError(Exception):
    """Base class for every transport failure."""


class Transport(ABC):
    """A read/write register link to one device."""

    @abstractmethod
    def read_registers(self, offset: int, count: int) -> list[int]:
        """Read *count* consecutive 16-bit registers starting at *offset*."""

    @abstractmethod
    def write_register(self, register: int, value: int) -> None:
        """Write a single 16-bit register."""

    def close(self) -> None:  # pragma: no cover - nothing to release by default
        """Release any resources held by the transport."""
