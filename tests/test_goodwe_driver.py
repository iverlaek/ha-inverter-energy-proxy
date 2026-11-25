"""Tests for the GoodWe driver: the brand map behind the canonical model."""

from __future__ import annotations

import datetime as dt

import pytest

from proxy.canonical import (
    BATTERY_MODE,
    BATTERY_POWER,
    BATTERY_SOC,
    BATTERY_SOH,
    BATTERY_VOLTAGE,
    INVERTER_AC_POWER,
    INVERTER_EMS_MODE,
    INVERTER_EMS_POWER_LIMIT,
    INVERTER_ENERGY_BATTERY_CHARGE_TODAY,
    INVERTER_ENERGY_EXPORT_TODAY,
    INVERTER_ENERGY_PV_TODAY,
    INVERTER_ENERGY_PV_TOTAL,
    INVERTER_EXPORT_ENABLED,
    INVERTER_EXPORT_LIMIT,
    INVERTER_GRID_FREQUENCY,
    INVERTER_GRID_POWER,
    INVERTER_GRID_VOLTAGE,
    INVERTER_LOAD_POWER,
    INVERTER_MODEL,
    INVERTER_PV_POWER,
    INVERTER_RATED_POWER,
    INVERTER_SERIAL,
    INVERTER_STATUS_MODE,
    INVERTER_TEMPERATURE,
    INVERTER_TOU_WINDOWS,
    INVERTER_WORK_MODE,
)
from proxy.drivers.base import WRITE_CANONICAL, WRITE_RAW
from proxy.drivers.goodwe import GoodweDriver
from proxy.schedule import ALL_DAYS, MODE_CHARGE, Schedule, ScheduleWindow
from proxy.transport.base import TransportError

from conftest import FakeTransport, build_store


def test_read_state_decodes_identity(driver: GoodweDriver):
    snapshot = driver.read_state()
    assert snapshot.get(INVERTER_MODEL) == "GW10K-ET"
    assert snapshot.get(INVERTER_SERIAL) == "GW10K12345678"
    assert snapshot.get(INVERTER_RATED_POWER) == 10000
    assert driver.info().model == "GW10K-ET"
    assert driver.info().serial == "GW10K12345678"
    assert driver.info().rated_power == 10000
    assert driver.info().brand == "GoodWe"


def test_read_state_decodes_runtime(driver: GoodweDriver):
    snapshot = driver.read_state()
    assert snapshot.get(INVERTER_PV_POWER) == 4500  # 3000 + 1500
    assert snapshot.get(INVERTER_GRID_VOLTAGE) == 230.0
    assert snapshot.get(INVERTER_GRID_FREQUENCY) == 50.0
    assert snapshot.get(INVERTER_AC_POWER) == 4400
    assert snapshot.get(INVERTER_GRID_POWER) == -500
    assert snapshot.get(INVERTER_LOAD_POWER) == 2500
    assert snapshot.get(INVERTER_TEMPERATURE) == 32.5
    assert snapshot.get(INVERTER_STATUS_MODE) == "normal"
    assert snapshot.get(INVERTER_ENERGY_PV_TOTAL) == 1234.5
    assert snapshot.get(INVERTER_ENERGY_PV_TODAY) == 23.4
    assert snapshot.get(INVERTER_ENERGY_EXPORT_TODAY) == 5.6
    assert snapshot.get(INVERTER_ENERGY_BATTERY_CHARGE_TODAY) == 7.8


def test_read_state_decodes_battery(driver: GoodweDriver):
    snapshot = driver.read_state()
    assert snapshot.get(BATTERY_SOC) == 73
    assert snapshot.get(BATTERY_SOH) == 98
    assert snapshot.get(BATTERY_VOLTAGE) == 480.0
    assert snapshot.get(BATTERY_POWER) == 1500  # charging
    assert snapshot.get(BATTERY_MODE) == "charge"


def test_read_state_decodes_control_registers(driver: GoodweDriver):
    snapshot = driver.read_state()
    assert snapshot.get(INVERTER_WORK_MODE) == "eco"
    assert snapshot.get(INVERTER_EXPORT_ENABLED) is True
    assert snapshot.get(INVERTER_EXPORT_LIMIT) == 10000
    assert snapshot.get(INVERTER_EMS_MODE) == "auto"
    assert snapshot.get(INVERTER_EMS_POWER_LIMIT) == 0


def test_read_state_decodes_the_tou_schedule(driver: GoodweDriver):
    schedule = driver.read_state().get(INVERTER_TOU_WINDOWS)
    assert isinstance(schedule, Schedule)
    window = schedule.windows[0]
    assert window.start == dt.time(2, 0)
    assert window.stop == dt.time(6, 0)
    assert window.mode == MODE_CHARGE
    assert window.power == 80
    assert window.days == ALL_DAYS


def test_snapshot_is_the_last_poll(driver: GoodweDriver):
    assert driver.snapshot().get(BATTERY_SOC) == 73


def test_image_caches_raw_registers(driver: GoodweDriver):
    assert driver.image()[35001] == 10000
    assert driver.image()[47000] == 3
    assert driver.image_block(35001, 2) == [10000, 0]
    assert driver.image_block(99999, 2) == [0, 0]  # unknown offsets zero-fill


def test_blocks_and_capabilities(driver: GoodweDriver):
    names = {block.name for block in driver.blocks()}
    assert {"device_info", "runtime", "battery_info", "tou_v1"} <= names
    caps = driver.capabilities()
    assert INVERTER_MODEL in caps
    assert BATTERY_SOC in caps
    assert INVERTER_TOU_WINDOWS in caps
    assert driver.supports(INVERTER_PV_POWER)
    assert not driver.supports("inverter.nonexistent")


def test_decode_write_known_field_is_canonical(driver: GoodweDriver):
    (request,) = driver.decode_write(47000, 5)
    assert request.kind == WRITE_CANONICAL
    assert request.key == INVERTER_WORK_MODE
    assert request.value == "self_use"


def test_decode_write_scaled_field(driver: GoodweDriver):
    (request,) = driver.decode_write(35121, 2400)
    assert request.key == INVERTER_GRID_VOLTAGE
    assert request.value == 240.0


def test_decode_write_bool_field(driver: GoodweDriver):
    (request,) = driver.decode_write(47509, 0)
    assert request.key == INVERTER_EXPORT_ENABLED
    assert request.value is False


def test_decode_write_in_tou_range_is_forwarded_raw(driver: GoodweDriver):
    # A window is written register by register, so the proxy must not decode a
    # partial window into a schedule: that would normalise the write away.
    (request,) = driver.decode_write(47517, 50)
    assert request.kind == WRITE_RAW
    assert request.offset == 47517
    assert request.raw_value == 50


def test_tou_window_turns_on_when_written_one_register_at_a_time(
    driver: GoodweDriver, transport: FakeTransport
):
    # Regression: the power and the on/off byte arrive as separate writes. The
    # window used to stay off forever because each half was decoded against the
    # other half's old value and re-encoded as "off, no power".
    (power,) = driver.decode_write(47525, 0xFFFF)
    assert power.kind == WRITE_RAW
    driver.write_raw(power.offset, power.raw_value)
    (switch,) = driver.decode_write(47526, 0xFF7F)
    assert switch.kind == WRITE_RAW
    driver.write_raw(switch.offset, switch.raw_value)

    assert (47525, 0xFFFF) in transport.writes
    assert (47526, 0xFF7F) in transport.writes
    assert driver.image_block(47523, 4) == [0x0000, 0x0000, 0xFFFF, 0xFF7F]


def test_decode_write_unknown_offset_is_raw(driver: GoodweDriver):
    (request,) = driver.decode_write(40000, 1234)
    assert request.kind == WRITE_RAW
    assert request.offset == 40000
    assert request.raw_value == 1234


def test_write_canonical_updates_transport_and_image(driver: GoodweDriver, transport: FakeTransport):
    driver.write(INVERTER_WORK_MODE, "self_use")
    assert (47000, 5) in transport.writes
    assert driver.image()[47000] == 5


def test_write_skips_registers_that_did_not_change(driver: GoodweDriver, transport: FakeTransport):
    before = list(transport.writes)
    driver.write(INVERTER_EXPORT_LIMIT, 10000)  # already 10000
    assert transport.writes == before


def test_write_tou_schedule_encodes_windows(driver: GoodweDriver, transport: FakeTransport):
    schedule = Schedule([ScheduleWindow(dt.time(1), dt.time(5), MODE_CHARGE, 90)])
    driver.write(INVERTER_TOU_WINDOWS, schedule)
    image = driver.image()
    assert image[47515] == 0x0100  # 01:00
    assert image[47516] == 0x0500  # 05:00
    assert image[47517] == (-90) & 0xFFFF  # charging is negative
    assert image[47518] == (0xFF << 8) | ALL_DAYS


def test_write_unknown_key_raises(driver: GoodweDriver):
    with pytest.raises(KeyError):
        driver.write("inverter.nonexistent", 1)


def test_write_raw_goes_straight_to_the_transport(driver: GoodweDriver, transport: FakeTransport):
    driver.write_raw(47529, 0xFF9C)
    assert (47529, 0xFF9C) in transport.writes
    assert driver.image()[47529] == 0xFF9C


def test_connect_probes_the_first_block(transport: FakeTransport):
    driver = GoodweDriver(transport=transport)
    driver.connect()
    assert transport.reads[0] == (35000, 1)


def test_close_closes_the_transport(transport: FakeTransport):
    driver = GoodweDriver(transport=transport)
    driver.close()
    assert transport.closed


def test_read_state_skips_a_missing_optional_block():
    transport = FakeTransport(build_store().as_dict(), fail_offsets={37000})
    driver = GoodweDriver(transport=transport)
    snapshot = driver.read_state()
    assert snapshot.get(INVERTER_MODEL) == "GW10K-ET"
    assert snapshot.get(BATTERY_SOC) is None  # the battery block was absent


def test_read_state_raises_when_a_required_block_fails():
    transport = FakeTransport(build_store().as_dict(), fail_offsets={35100})
    driver = GoodweDriver(transport=transport)
    with pytest.raises(TransportError):
        driver.read_state()
