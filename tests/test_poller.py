"""Tests for the poller: the single place devices are touched and writes routed."""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from proxy.battery.goodwe import GoodweBattery
from proxy.canonical import (
    BATTERY_INTENT,
    BATTERY_SOC,
    INVERTER_EXPORT_LIMIT,
    INVERTER_TOU_WINDOWS,
    INVERTER_WORK_MODE,
)
from proxy.drivers.base import WRITE_CANONICAL, WRITE_RAW, WriteRequest
from proxy.intents import BatteryIntent, IntentMode
from proxy.poller import Poller, is_writable
from proxy.schedule import ALL_DAYS, MODE_CHARGE, Schedule, ScheduleWindow

from conftest import FakeTransport


def _poller(driver, batteries=(), **kwargs) -> Poller:
    return Poller([driver], list(batteries), **kwargs)


def _battery(driver) -> GoodweBattery:
    return GoodweBattery(driver, rated_power_w=10000)


# --- polling --------------------------------------------------------------


def test_poll_once_merges_readings(driver):
    poller = _poller(driver, interval=30)
    poller.poll_once(now=100.0)
    assert poller.snapshot.get(BATTERY_SOC) == 73
    status = poller.status["goodwe"]
    assert status.online is True
    assert status.polls == 1
    assert status.last_poll == 100.0
    assert status.next_attempt == 130.0
    assert poller.polls == 1


def test_disabled_driver_is_skipped(driver):
    driver.enabled = False
    poller = _poller(driver)
    poller.poll_once(now=100.0)
    assert len(poller.snapshot) == 0
    assert poller.status["goodwe"].online is False


def test_failure_records_an_exponential_backoff(driver, transport: FakeTransport):
    transport.fail = True
    poller = _poller(driver, min_backoff=5, max_backoff=300)
    poller.poll_once(now=100.0)
    status = poller.status["goodwe"]
    assert status.online is False
    assert status.failures == 1
    assert status.next_attempt == 105.0
    assert "simulated transport failure" in status.last_error

    poller.poll_once(now=101.0)  # still backing off
    assert poller.status["goodwe"].failures == 1

    poller.poll_once(now=106.0)
    assert poller.status["goodwe"].failures == 2
    assert poller.status["goodwe"].next_attempt == 116.0


def test_failure_warning_names_configured_ip(driver, transport, caplog):
    transport.fail = True
    driver.host = "192.168.20.13"
    with caplog.at_level(logging.WARNING, logger="proxy.poller"):
        _poller(driver, min_backoff=5).poll_once(now=100.0)
    assert "goodwe inverter/proxy not found on ip:192.168.20.13:8899" in caplog.text


def test_due_follows_the_interval(driver):
    poller = _poller(driver, interval=30)
    assert poller.due(now=1.0) is True
    poller.poll_once(now=100.0)
    assert poller.due(now=120.0) is False
    assert poller.due(now=130.0) is True


def test_stale_keys_use_the_ttl(driver):
    poller = _poller(driver, ttl=10)
    poller.snapshot.set(BATTERY_SOC, 73, now=100.0)
    assert poller.stale_keys(now=120.0) == [BATTERY_SOC]
    assert poller.stale_keys(now=105.0) == []


# --- write routing --------------------------------------------------------


def test_apply_write_request_canonical(driver, transport: FakeTransport):
    poller = _poller(driver)
    poller.apply_write_request(WriteRequest.canonical(INVERTER_WORK_MODE, "self_use"))
    assert (47000, 5) in transport.writes


def test_apply_write_request_raw(driver, transport: FakeTransport):
    poller = _poller(driver)
    poller.apply_write_request(WriteRequest.raw(47000, 5))
    assert (47000, 5) in transport.writes


def test_writes_are_paced_for_the_slow_device(driver, transport):
    poller = _poller(driver, write_interval=0.05)
    poller.apply_write_request(WriteRequest.raw(47000, 1))
    poller.apply_write_request(WriteRequest.raw(47000, 2))
    assert poller.write_count == 2
    assert poller.status_dict()["write_wait_seconds"] > 0
    assert (47000, 2) in transport.writes


def test_apply_write_request_raw_unknown_offset_raises(driver):
    poller = _poller(driver)
    with pytest.raises(KeyError):
        poller.apply_write_request(WriteRequest.raw(99999, 1))


def test_apply_write_request_unknown_kind_raises(driver):
    poller = _poller(driver)
    with pytest.raises(ValueError):
        poller.apply_write_request(WriteRequest(kind="weird"))


def test_apply_canonical_routes_a_plain_key(driver, transport: FakeTransport):
    poller = _poller(driver)
    poller.apply_canonical(INVERTER_EXPORT_LIMIT, 5000)
    assert (47510, 5000) in transport.writes


def test_apply_canonical_unknown_key_raises(driver):
    poller = _poller(driver)
    with pytest.raises(KeyError):
        poller.apply_canonical("inverter.nonexistent", 1)


def test_apply_canonical_tou_writes_and_observes(driver, transport: FakeTransport, monkeypatch):
    battery = _battery(driver)
    poller = _poller(driver, [battery])
    observed = {}

    def fake_intent(schedule, **kwargs):
        observed["schedule"] = schedule
        return BatteryIntent.charge(power_percent=50, source="schedule")

    monkeypatch.setattr("proxy.poller.intent_for_schedule", fake_intent)
    schedule = Schedule([ScheduleWindow(dt.time(0), dt.time(23, 59), MODE_CHARGE, 50, ALL_DAYS)])
    poller.apply_canonical(INVERTER_TOU_WINDOWS, schedule)

    assert observed["schedule"] is schedule
    assert battery.active_intent.mode is IntentMode.CHARGE
    assert (47515, 0x0000) in transport.writes
    # Observing a schedule must not issue EMS control writes of its own.
    assert not any(offset == 47511 for offset, _ in transport.writes)


def test_apply_intent_routes_to_the_battery(driver, transport: FakeTransport):
    battery = _battery(driver)
    poller = _poller(driver, [battery])
    poller.apply_intent(BatteryIntent.charge(power_w=1000))
    assert battery.active_intent.mode is IntentMode.CHARGE
    assert (47511, 4) in transport.writes


def test_apply_intent_without_a_battery_raises(driver):
    poller = _poller(driver)
    with pytest.raises(RuntimeError):
        poller.apply_intent(BatteryIntent.charge())


def test_apply_canonical_battery_intent_routes_to_the_battery(driver):
    battery = _battery(driver)
    poller = _poller(driver, [battery])
    poller.apply_canonical(BATTERY_INTENT, BatteryIntent.discharge(power_w=1000))
    assert battery.active_intent.mode is IntentMode.DISCHARGE


# --- introspection --------------------------------------------------------


def test_status_dict_shape(driver):
    battery = _battery(driver)
    poller = _poller(driver, [battery])
    poller.poll_once(now=100.0)
    data = poller.status_dict(now=110.0)
    assert data["polls"] == 1
    assert data["interval"] == 30.0
    assert data["devices"]["goodwe"]["online"] is True
    assert data["readings"] > 0
    assert data["battery"]["name"] == "goodwe"
    assert data["intent"] is None


def test_status_dict_reports_the_active_intent(driver):
    battery = _battery(driver)
    poller = _poller(driver, [battery])
    poller.apply_intent(BatteryIntent.charge(power_w=1000))
    assert poller.status_dict()["intent"]["mode"] == "charge"


def test_poller_reexports_is_writable():
    assert is_writable(INVERTER_WORK_MODE) is True
    assert is_writable(BATTERY_SOC) is False
