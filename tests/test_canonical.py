"""Tests for the canonical parameter registry and snapshot."""

from __future__ import annotations

import datetime as dt

from proxy import canonical
from proxy.canonical import (
    ACCESS_R,
    ACCESS_RW,
    BATTERY_INTENT,
    BATTERY_SOC,
    DEVICE_BATTERY,
    DEVICE_INVERTER,
    INVERTER_WORK_MODE,
    REGISTRY,
    DeviceState,
    Reading,
    Snapshot,
    is_writable,
    keys,
    parameter,
    parameters,
    serialize,
)
from proxy.schedule import ScheduleWindow


def test_registry_has_both_devices():
    assert keys(DEVICE_INVERTER)
    assert keys(DEVICE_BATTERY)
    assert set(keys()) == set(keys(DEVICE_INVERTER)) | set(keys(DEVICE_BATTERY))


def test_parameter_lookup():
    param = parameter(BATTERY_SOC)
    assert param is not None
    assert param.device == DEVICE_BATTERY
    assert param.unit == "%"
    assert param.access == ACCESS_R
    assert not param.writable
    assert parameter("nope.nope") is None


def test_writable_parameters():
    assert is_writable(INVERTER_WORK_MODE)
    assert is_writable(BATTERY_INTENT)
    assert not is_writable(BATTERY_SOC)
    assert not is_writable("does.not.exist")


def test_every_registry_entry_is_consistent():
    for key, param in REGISTRY.items():
        assert key == param.key
        assert param.device in (DEVICE_INVERTER, DEVICE_BATTERY)
        assert param.access in (ACCESS_R, ACCESS_RW)


def test_parameters_filters_by_device():
    inverters = parameters(DEVICE_INVERTER)
    assert all(p.device == DEVICE_INVERTER for p in inverters)
    assert len(parameters()) == len(inverters) + len(parameters(DEVICE_BATTERY))


def test_serialize_handles_nested_values():
    assert serialize(5) == 5
    assert serialize(None) is None
    assert serialize([1, 2]) == [1, 2]
    assert serialize({"a": 1}) == {"a": 1}
    window = ScheduleWindow(dt.time(2), dt.time(6), "charge", 80)
    assert serialize(window) == window.to_dict()


def test_reading_age_and_dict():
    reading = Reading(key=BATTERY_SOC, value=73, updated=100.0, source="test")
    assert reading.age(now=130.0) == 30
    data = reading.as_dict(now=130.0)
    assert data["key"] == BATTERY_SOC
    assert data["value"] == 73
    assert data["unit"] == "%"
    assert data["source"] == "test"
    assert data["age"] == 30


def test_reading_age_never_negative():
    reading = Reading(key=BATTERY_SOC, value=73, updated=200.0)
    assert reading.age(now=100.0) == 0


def test_snapshot_set_get_update():
    snapshot = Snapshot()
    snapshot.set(BATTERY_SOC, 73, source="test", now=100.0)
    assert snapshot.get(BATTERY_SOC) == 73
    assert snapshot.get("missing") is None
    assert snapshot.get("missing", -1) == -1
    assert snapshot.has(BATTERY_SOC)
    assert BATTERY_SOC in snapshot
    assert len(snapshot) == 1

    snapshot.update({BATTERY_SOC: 80, INVERTER_WORK_MODE: "eco"}, now=110.0)
    assert snapshot.get(BATTERY_SOC) == 80
    assert snapshot.get(INVERTER_WORK_MODE) == "eco"


def test_snapshot_values_and_serialized():
    snapshot = Snapshot()
    snapshot.set(BATTERY_SOC, 73, now=100.0)
    assert snapshot.values() == {BATTERY_SOC: 73}
    serialized = snapshot.serialized(now=110.0)
    assert serialized[BATTERY_SOC]["age"] == 10


def test_snapshot_merge_copy_and_stale():
    first = Snapshot()
    first.set(BATTERY_SOC, 73, now=100.0)
    second = Snapshot()
    second.set(INVERTER_WORK_MODE, "eco", now=105.0)
    first.merge(second)
    assert set(first.values()) == {BATTERY_SOC, INVERTER_WORK_MODE}

    clone = first.copy()
    clone.set(BATTERY_SOC, 99)
    assert first.get(BATTERY_SOC) == 73

    assert set(first.stale(ttl=10, now=120.0)) == {BATTERY_SOC, INVERTER_WORK_MODE}
    assert first.stale(ttl=30, now=120.0) == []
    assert set(first.stale(ttl=1, now=200.0)) == {BATTERY_SOC, INVERTER_WORK_MODE}


def test_snapshot_iteration():
    snapshot = Snapshot()
    snapshot.set(BATTERY_SOC, 73)
    assert list(snapshot) == [BATTERY_SOC]
    assert list(snapshot.keys()) == [BATTERY_SOC]


def test_device_state_serializes():
    readings = Snapshot()
    readings.set(BATTERY_SOC, 73, now=100.0)
    state = DeviceState(
        device=DEVICE_INVERTER,
        brand="GoodWe",
        model="GW10K-ET",
        serial="123",
        online=True,
        readings=readings,
    )
    data = state.as_dict(now=110.0)
    assert data["brand"] == "GoodWe"
    assert data["online"] is True
    assert data["readings"][BATTERY_SOC]["value"] == 73


def test_module_exports_expected_constants():
    assert canonical.POWER_SIGN_CHARGE_POSITIVE is True
    assert "eco" in canonical.WORK_MODE_VALUES
    assert "import_ac" in canonical.EMS_MODE_VALUES
