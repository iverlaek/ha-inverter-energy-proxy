"""Tests for the GoodWe battery: turning a request into inverter writes."""

from __future__ import annotations

import pytest

from proxy.battery.goodwe import GoodweBattery
from proxy.canonical import (
    BATTERY_INTENT,
    BATTERY_SOC,
    BATTERY_SOH,
    INVERTER_MODEL,
    INVERTER_TOU_WINDOWS,
    INVERTER_WORK_MODE,
)
from proxy.intents import BatteryIntent, IntentMode
from proxy.schedule import MODE_CHARGE

from conftest import FakeTransport

EMS_MODE_REG = 47511
EMS_POWER_REG = 47512
WORK_MODE_REG = 47000


def _battery(driver, **kwargs) -> GoodweBattery:
    return GoodweBattery(driver, rated_power_w=10000, **kwargs)


# --- telemetry ------------------------------------------------------------


def test_read_state_exposes_battery_keys_only(driver):
    snapshot = _battery(driver).read_state()
    assert snapshot.get(BATTERY_SOC) == 73
    assert snapshot.get(BATTERY_SOH) == 98
    assert snapshot.get(INVERTER_MODEL) is None  # inverter keys stay with the driver


def test_read_state_reports_the_active_intent(driver):
    battery = _battery(driver)
    intent = BatteryIntent.charge(power_w=1000)
    battery.apply_intent(intent)
    assert battery.read_state().get(BATTERY_INTENT) is intent


# --- EMS realisation ------------------------------------------------------


def test_charge_uses_import_ac_with_negative_setpoint(driver, transport: FakeTransport):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.charge(power_w=4000))
    assert (EMS_MODE_REG, 4) in transport.writes  # import_ac
    assert (EMS_POWER_REG, (-4000) & 0xFFFF) in transport.writes


def test_discharge_uses_discharge_pv_with_positive_setpoint(driver, transport: FakeTransport):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.discharge(power_w=3000))
    assert (EMS_MODE_REG, 3) in transport.writes  # discharge_pv
    assert (EMS_POWER_REG, 3000) in transport.writes


def test_idle_uses_battery_standby_without_a_setpoint(driver, transport: FakeTransport):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.idle())
    assert (EMS_MODE_REG, 8) in transport.writes  # battery_standby
    assert not any(offset == EMS_POWER_REG for offset, _ in transport.writes)


def test_self_consumption_uses_auto(driver, transport: FakeTransport):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.self_consumption())
    # The fixture already sits in "auto", so nothing needs writing.
    assert transport.writes == []
    assert battery.active_intent.mode is IntentMode.SELF_CONSUMPTION


def test_percent_setpoint_uses_the_rated_power(driver, transport: FakeTransport):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.charge(power_percent=50))
    assert (EMS_POWER_REG, (-5000) & 0xFFFF) in transport.writes


def test_active_intent_and_clear(driver):
    battery = _battery(driver)
    intent = BatteryIntent.discharge(power_w=2000)
    battery.apply_intent(intent)
    assert battery.active_intent is intent
    battery.clear_intent()
    assert battery.active_intent.mode is IntentMode.SELF_CONSUMPTION


def test_tick_expires_an_overdue_intent(driver):
    battery = _battery(driver)
    battery.apply_intent(BatteryIntent.charge(power_w=1000, deadline=100.0))
    battery.tick(now=99.0)
    assert battery.active_intent.mode is IntentMode.CHARGE
    battery.tick(now=101.0)
    assert battery.active_intent.mode is IntentMode.SELF_CONSUMPTION


def test_observe_records_without_writing(driver, transport: FakeTransport):
    battery = _battery(driver)
    before = list(transport.writes)
    intent = BatteryIntent.charge(power_w=1000)
    battery.observe_intent(intent)
    assert battery.active_intent is intent
    assert transport.writes == before


# --- Eco-mode realisation -------------------------------------------------


def test_eco_charge_writes_an_all_day_window(driver, transport: FakeTransport):
    battery = _battery(driver, control="eco")
    battery.apply_intent(BatteryIntent.charge(power_percent=80))
    image = driver.image()
    assert image[47515] == 0x0000  # 00:00
    assert image[47516] == 0x173B  # 23:59
    assert image[47517] == (-80) & 0xFFFF
    assert image[47518] == 0xFF7F  # enabled, every day
    schedule = driver.read_state().get(INVERTER_TOU_WINDOWS)
    assert schedule.windows[0].mode == MODE_CHARGE
    assert schedule.windows[0].power == 80


def test_eco_idle_falls_back_to_general(driver, transport: FakeTransport):
    battery = _battery(driver, control="eco")
    battery.apply_intent(BatteryIntent.idle())
    assert driver.image()[WORK_MODE_REG] == 0  # general
    assert (WORK_MODE_REG, 0) in transport.writes


def test_eco_discharge_window(driver):
    battery = _battery(driver, control="eco")
    battery.apply_intent(BatteryIntent.discharge(power_percent=60))
    # Discharge is positive in the window encoding.
    assert driver.image()[47517] == 60


def test_unknown_control_strategy_raises(driver):
    with pytest.raises(ValueError):
        _battery(driver, control="nonsense")


def test_info_reports_brand(driver):
    info = _battery(driver).info()
    assert info["name"] == "goodwe"
    assert info["brand"] == "GoodWe"


def test_eco_charge_is_encoded_as_a_negative_window(driver):
    battery = _battery(driver, control="eco")
    battery.apply_intent(BatteryIntent.charge(power_percent=80))
    assert driver.image()[47517] == (-80) & 0xFFFF
