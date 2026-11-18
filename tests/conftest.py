"""Shared test fixtures.

The proxy is built so that brand knowledge sits behind small interfaces: a
:class:`~proxy.transport.base.Transport` moves bytes, a driver gives them
meaning, a battery accepts requests. These fixtures supply an in-memory
transport and a realistic GoodWe ET register image, so every layer can be
exercised without a dongle on the network.
"""

from __future__ import annotations

import pytest

from proxy.server.base import CoreError, CoreLink
from proxy.transport.base import Transport, TransportError


class FakeTransport(Transport):
    """An in-memory stand-in for a GoodWe UDP dongle."""

    def __init__(
        self,
        store: dict[int, int] | None = None,
        *,
        fail: bool = False,
        fail_offsets: set[int] | None = None,
    ) -> None:
        self.store: dict[int, int] = dict(store or {})
        self.writes: list[tuple[int, int]] = []
        self.reads: list[tuple[int, int]] = []
        self.fail = fail
        self.fail_offsets = set(fail_offsets or ())
        self.closed = False

    def read_registers(self, offset: int, count: int) -> list[int]:
        self.reads.append((offset, count))
        if self.fail or offset in self.fail_offsets:
            raise TransportError("simulated transport failure")
        return [self.store.get(offset + i, 0) for i in range(count)]

    def write_register(self, register: int, value: int) -> None:
        value &= 0xFFFF
        self.store[register] = value
        self.writes.append((register, value))

    def close(self) -> None:
        self.closed = True


class GoodweStore:
    """A builder for a realistic GoodWe ET register image.

    The offsets match the ``goodwe`` library's ``et.py`` sensor map, which is the
    same map :mod:`proxy.drivers.goodwe_map` uses.
    """

    def __init__(self) -> None:
        self.regs: dict[int, int] = {}

    # -- raw writes --------------------------------------------------------

    def set(self, offset: int, value: int) -> GoodweStore:
        self.regs[offset] = value & 0xFFFF
        return self

    def set_s16(self, offset: int, value: int) -> GoodweStore:
        return self.set(offset, value & 0xFFFF)

    def set_u32(self, offset: int, value: int) -> GoodweStore:
        self.regs[offset] = (value >> 16) & 0xFFFF
        self.regs[offset + 1] = value & 0xFFFF
        return self

    def set_s32(self, offset: int, value: int) -> GoodweStore:
        return self.set_u32(offset, value & 0xFFFFFFFF)

    def set_string(self, offset: int, text: str) -> GoodweStore:
        data = text.encode("ascii")
        if len(data) % 2:
            data += b"\x00"
        for index in range(0, len(data), 2):
            self.regs[offset + index // 2] = (data[index] << 8) | data[index + 1]
        return self

    def set_window_v1(
        self,
        index: int,
        start: tuple[int, int],
        stop: tuple[int, int],
        power: int,
        *,
        enabled: bool = True,
        days: int = 0x7F,
    ) -> GoodweStore:
        base = 47515 + index * 4
        self.regs[base] = start[0] * 256 + start[1]
        self.regs[base + 1] = stop[0] * 256 + stop[1]
        self.regs[base + 2] = power & 0xFFFF
        self.regs[base + 3] = ((0xFF if enabled else 0x00) << 8) | (days & 0xFF)
        return self

    def set_window_v2(
        self,
        index: int,
        start: tuple[int, int],
        stop: tuple[int, int],
        power: int,
        *,
        enabled: bool = True,
        days: int = 0x7F,
        soc: int = 100,
        months: int = 0,
    ) -> GoodweStore:
        base = 47547 + index * 6
        self.regs[base] = start[0] * 256 + start[1]
        self.regs[base + 1] = stop[0] * 256 + stop[1]
        self.regs[base + 2] = ((0xFF if enabled else 0x00) << 8) | (days & 0xFF)
        self.regs[base + 3] = power & 0xFFFF
        self.regs[base + 4] = soc & 0xFFFF
        self.regs[base + 5] = months & 0xFFFF
        return self

    def as_dict(self) -> dict[int, int]:
        return dict(self.regs)


def build_store() -> GoodweStore:
    """A realistic GW10K-ET image with one charge window configured."""
    store = GoodweStore()
    # -- device info (35000) --
    store.set(35001, 10000)  # rated power
    store.set_string(35003, "GW10K12345678")  # serial (16 bytes)
    store.set_string(35011, "GW10K-ET")  # model (10 bytes)
    store.set_string(35021, "ARM:19.35")  # firmware (12 bytes)
    # -- runtime (35100) --
    store.set_u32(35105, 3000)  # PV1 power
    store.set_u32(35109, 1500)  # PV2 power
    store.set(35121, 2300)  # grid voltage 230.0 V
    store.set(35123, 5000)  # grid frequency 50.00 Hz
    store.set_s16(35138, 4400)  # AC power
    store.set_s16(35140, -500)  # grid power (exporting 500 W)
    store.set_s16(35172, 2500)  # load
    store.set(35176, 325)  # inverter temperature 32.5 C
    store.set(35180, 4800)  # battery voltage 480.0 V
    store.set_s16(35181, 31)  # battery current 3.1 A
    store.set_s32(35182, 1500)  # battery power +1500 W (charging)
    store.set(35184, 3)  # battery mode: charge
    store.set(35187, 1)  # status: normal
    store.set_u32(35191, 12345)  # total PV energy 1234.5 kWh
    store.set_u32(35193, 234)  # today PV energy 23.4 kWh
    store.set(35199, 56)  # today export 5.6 kWh
    store.set(35202, 12)  # today import 1.2 kWh
    store.set(35208, 78)  # today battery charge 7.8 kWh
    store.set(35211, 34)  # today battery discharge 3.4 kWh
    # -- battery info (37000) --
    store.set(37003, 250)  # battery temperature 25.0 C
    store.set(37004, 25)  # charge limit 25 A
    store.set(37005, 25)  # discharge limit 25 A
    store.set(37007, 73)  # SOC 73 %
    store.set(37008, 98)  # SOH 98 %
    store.set(37020, 260)  # max cell temperature 26.0 C
    store.set(37021, 240)  # min cell temperature 24.0 C
    store.set(37022, 3320)  # max cell voltage 3320 mV
    store.set(37023, 3290)  # min cell voltage 3290 mV
    # -- work mode (47000) --
    store.set(47000, 3)  # eco
    # -- export (47509) --
    store.set(47509, 1)  # export enabled
    store.set(47510, 10000)  # export limit
    # -- EMS (47511) --
    store.set(47511, 1)  # auto
    store.set(47512, 0)  # no setpoint
    # -- Time-of-Use (47515): 02:00-06:00 charge 80% every day --
    store.set_window_v1(0, (2, 0), (6, 0), -80, enabled=True, days=0x7F)
    return store


@pytest.fixture
def store() -> GoodweStore:
    """A fresh, realistic GoodWe register image."""
    return build_store()


@pytest.fixture
def transport(store: GoodweStore) -> FakeTransport:
    """A fake transport backed by the realistic register image."""
    return FakeTransport(store.as_dict())


@pytest.fixture
def driver(transport: FakeTransport):
    """A GoodWe driver on the fake transport, already polled once."""
    from proxy.drivers.goodwe import GoodweDriver

    drv = GoodweDriver(transport=transport)
    drv.read_state()
    return drv


class FakeCore(CoreLink):
    """An in-memory core: a register image plus the writes it has seen.

    Used to exercise the facade without a driver, a poller or a network.
    """

    def __init__(
        self,
        image: dict[int, int] | None = None,
        *,
        device: str = "goodwe",
        fail: bool = False,
    ) -> None:
        self.device = device
        self.image: dict[int, int] = dict(image or {})
        self.writes: list[tuple[int, int]] = []
        self.fail = fail
        self.closed = False

    def read(self, offset: int, count: int) -> list[int]:
        if self.fail:
            raise CoreError("simulated core failure")
        return [self.image.get(offset + i, 0) for i in range(count)]

    def write(self, offset: int, value: int) -> None:
        if self.fail:
            raise CoreError("simulated core failure")
        value &= 0xFFFF
        self.image[offset] = value
        self.writes.append((offset, value))

    def snapshot(self) -> dict:
        return {str(offset): value for offset, value in self.image.items()}

    def status(self) -> dict:
        return {"device": self.device, "writes": len(self.writes)}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_core() -> FakeCore:
    """A fake core holding a couple of known registers."""
    return FakeCore({47000: 3, 47515: 0x0200, 47516: 0x0600})
