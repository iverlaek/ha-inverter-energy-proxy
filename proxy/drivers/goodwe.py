"""The GoodWe inverter driver.

Wraps a GoodWe UDP dongle transport with the ET-family register map, turning
register reads into canonical readings and canonical writes back into register
writes. It also keeps the cached register image the dongle facade serves from.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from ..canonical import (
    INVERTER_FIRMWARE,
    INVERTER_MODEL,
    INVERTER_RATED_POWER,
    INVERTER_SERIAL,
    INVERTER_TOU_WINDOWS,
    Snapshot,
)
from ..schedule import Schedule
from ..transport.goodwe_udp import (
    DEFAULT_COMM_ADDR,
    DEFAULT_PORT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    GoodweUdpClient,
)
from .base import (
    DeviceInfo,
    InverterDriver,
    RegisterBlock,
    WriteRequest,
)
from .goodwe_map import (
    DEFAULT_BLOCKS,
    Block,
    TOU_WRITE_RANGES,
    decode_field,
    decode_windows_v1,
    decode_windows_v2,
    encode_field,
    windows_for_block,
)

_LOGGER = logging.getLogger(__name__)


class GoodweDriver(InverterDriver):
    """Brand-specific access to a GoodWe ET/EH/BT/BH-family inverter."""

    name = "goodwe"
    brand = "GoodWe"

    def __init__(
        self,
        host: str = "",
        port: int = DEFAULT_PORT,
        comm_addr: int = DEFAULT_COMM_ADDR,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        *,
        transport: Any | None = None,
        blocks: Iterable[Block] | None = None,
    ) -> None:
        self.transport = transport or GoodweUdpClient(
            host, port, comm_addr, timeout, retries
        )
        self.host = host
        self._blocks = tuple(blocks) if blocks is not None else DEFAULT_BLOCKS
        self._image: dict[int, int] = {}
        self._last_regs: dict[str, list[int]] = {}
        self._snapshot = Snapshot()
        self._info = DeviceInfo(brand=self.brand)
        self._field_index: dict[int, tuple[Block, Any]] = {}
        self._key_index: dict[str, tuple[Block, Any]] = {}
        self._block_by_offset: dict[int, Block] = {}
        for block in self._blocks:
            self._block_by_offset[block.offset] = block
            for field_ in block.fields:
                self._key_index[field_.key] = (block, field_)
                for extra in range(field_.size):
                    self._field_index[block.offset + field_.index + extra] = (
                        block,
                        field_,
                    )
            for derived in block.derived:
                self._key_index[derived.key] = (block, derived)

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Probe the dongle once so startup fails fast on a wrong address."""
        self.transport.read_registers(self._blocks[0].offset, 1)

    def close(self) -> None:
        self.transport.close()

    # -- canonical model ---------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        return frozenset(self._key_index)

    def info(self) -> DeviceInfo:
        return self._info

    def blocks(self) -> tuple[RegisterBlock, ...]:
        return tuple(
            RegisterBlock(b.name, b.offset, b.count, b.optional) for b in self._blocks
        )

    def image(self) -> dict[int, int]:
        return self._image

    def read_state(self) -> Snapshot:
        """Poll every block once and return the combined canonical readings."""
        snapshot = Snapshot()
        now = time.time()
        for block in self._blocks:
            regs = self._read_block(self.transport, block)
            if regs is None:
                continue
            self._last_regs[block.name] = regs
            for index, raw in enumerate(regs):
                self._image[block.offset + index] = raw & 0xFFFF
            for field_ in block.fields:
                try:
                    value = decode_field(field_, regs)
                except (ValueError, IndexError):
                    _LOGGER.debug("cannot decode %s", field_.key)
                    continue
                if value is not None:
                    snapshot.set(field_.key, value, source=self.name, now=now)
            for derived in block.derived:
                try:
                    value = derived.func(regs)
                except (ValueError, IndexError):
                    _LOGGER.debug("cannot derive %s", derived.key)
                    continue
                snapshot.set(derived.key, value, source=self.name, now=now)
        self._update_info()
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> Snapshot:
        """The canonical readings from the last poll (empty before the first)."""
        return self._snapshot

    def write(self, key: str, value: Any) -> None:
        """Write a canonical parameter to the inverter."""
        if key == INVERTER_TOU_WINDOWS:
            schedule = value if isinstance(value, Schedule) else Schedule.from_dict(value)
            block = self._tou_block()
            self._write_block_registers(block, windows_for_block(block, schedule))
            return
        entry = self._key_index.get(key)
        if entry is None:
            raise KeyError(f"{self.name} cannot write {key!r}")
        block, field_ = entry
        regs = encode_field(field_, value)
        self._write_block_registers(block, regs, base_index=field_.index)

    def decode_write(self, offset: int, value: int) -> list[WriteRequest]:
        """Interpret a raw register write from a consumer.

        The candidate image is not committed here: the returned canonical write
        is applied through :meth:`write`, which then compares against the real
        image and forwards only the registers that actually changed.
        """
        value &= 0xFFFF
        candidate = dict(self._image)
        candidate[offset] = value

        for start, count in TOU_WRITE_RANGES:
            if start <= offset < start + count:
                block = self._block_by_offset.get(start)
                if block is None:
                    break
                regs = [candidate.get(block.offset + i, 0) for i in range(block.count)]
                schedule = (
                    decode_windows_v2(regs)
                    if block.name == "tou_v2"
                    else decode_windows_v1(regs)
                )
                return [WriteRequest.canonical(INVERTER_TOU_WINDOWS, schedule)]

        entry = self._field_index.get(offset)
        if entry is None:
            return [WriteRequest.raw(offset, value)]
        block, field_ = entry
        regs = [candidate.get(block.offset + i, 0) for i in range(block.count)]
        decoded = decode_field(field_, regs)
        return [WriteRequest.canonical(field_.key, decoded)]

    def write_raw(self, offset: int, value: int) -> None:
        value &= 0xFFFF
        self.transport.write_register(offset, value)
        self._image[offset] = value

    # -- internals ---------------------------------------------------------

    def _tou_block(self) -> Block:
        for block in self._blocks:
            if block.name.startswith("tou"):
                return block
        raise KeyError("no Time-of-Use block configured")

    def _write_block_registers(
        self, block: Block, regs: list[int], base_index: int = 0
    ) -> None:
        for index, raw in enumerate(regs):
            offset = block.offset + base_index + index
            raw &= 0xFFFF
            if self._image.get(offset) == raw:
                continue
            self.transport.write_register(offset, raw)
            self._image[offset] = raw

    def _update_info(self) -> None:
        regs = self._last_regs.get("device_info")
        block = next((b for b in self._blocks if b.name == "device_info"), None)
        if not regs or block is None:
            return
        values: dict[str, Any] = {}
        for field_ in block.fields:
            try:
                values[field_.key] = decode_field(field_, regs)
            except (ValueError, IndexError):
                continue
        for derived in block.derived:
            try:
                values[derived.key] = derived.func(regs)
            except (ValueError, IndexError):
                continue
        self._info = DeviceInfo(
            brand=self.brand,
            model=str(values.get(INVERTER_MODEL, "")),
            serial=str(values.get(INVERTER_SERIAL, "")),
            firmware=str(values.get(INVERTER_FIRMWARE, "")),
            rated_power=values.get(INVERTER_RATED_POWER),
        )
