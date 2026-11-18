"""Tests for the battery charge/discharge request."""

from __future__ import annotations

import datetime as dt

from proxy.intents import (
    BatteryIntent,
    IntentMode,
    intent_for_schedule,
    intent_for_window,
)
from proxy.schedule import ALL_DAYS, MODE_CHARGE, MODE_DISCHARGE, MODE_OFF, Schedule, ScheduleWindow


def test_intent_mode_sign():
    assert IntentMode.CHARGE.sign == 1
    assert IntentMode.DISCHARGE.sign == -1
    assert IntentMode.IDLE.sign == 0
    assert IntentMode.SELF_CONSUMPTION.sign == 0


def test_convenience_constructors():
    assert BatteryIntent.idle().mode is IntentMode.IDLE
    assert BatteryIntent.charge(power_w=4000).mode is IntentMode.CHARGE
    assert BatteryIntent.discharge().mode is IntentMode.DISCHARGE
    assert BatteryIntent.self_consumption().mode is IntentMode.SELF_CONSUMPTION


def test_expiry_and_remaining():
    now = 1000.0
    open_ended = BatteryIntent.charge()
    assert not open_ended.expired(now)
    assert open_ended.remaining(now) is None

    expiring = BatteryIntent.charge(deadline=now + 30)
    assert not expiring.expired(now)
    assert expiring.remaining(now) == 30
    assert expiring.expired(now + 31)
    assert expiring.remaining(now + 40) == 0


def test_intent_round_trips_through_dict():
    intent = BatteryIntent.charge(
        power_w=4000,
        power_percent=50,
        target_soc=90,
        min_soc=20,
        max_soc=95,
        deadline=1234.5,
        source="test",
        note="hi",
    )
    restored = BatteryIntent.from_dict(intent.to_dict())
    assert restored.mode is IntentMode.CHARGE
    assert restored.power_w == 4000
    assert restored.power_percent == 50
    assert restored.target_soc == 90
    assert restored.min_soc == 20
    assert restored.max_soc == 95
    assert restored.deadline == 1234.5
    assert restored.source == "test"
    assert restored.note == "hi"


def test_to_dict_reports_remaining():
    intent = BatteryIntent.charge(deadline=1000.0)
    data = intent.to_dict()
    assert data["mode"] == "charge"
    assert data["remaining"] is not None
    assert data["deadline"] == 1000.0


def test_with_power_w_replaces_power():
    intent = BatteryIntent.charge(power_percent=50)
    assert intent.with_power_w(1234).power_w == 1234


def test_intent_for_window_maps_modes():
    charge = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80)
    discharge = ScheduleWindow(dt.time(14), dt.time(18), MODE_DISCHARGE, 50)
    assert intent_for_window(charge).mode is IntentMode.CHARGE
    assert intent_for_window(charge).power_percent == 80
    assert intent_for_window(discharge).mode is IntentMode.DISCHARGE
    assert intent_for_window(discharge).note == "14:00-18:00"


def test_intent_for_window_off_or_none_is_idle():
    off = ScheduleWindow(dt.time(2), dt.time(6), MODE_OFF, 0)
    assert intent_for_window(None).mode is IntentMode.IDLE
    assert intent_for_window(off).mode is IntentMode.IDLE


def test_intent_for_schedule_sets_deadline_to_window_stop():
    schedule = Schedule(
        [ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, ALL_DAYS)]
    )
    when = dt.datetime(2026, 11, 18, 4, 0)
    intent = intent_for_schedule(schedule, when=when)
    assert intent.mode is IntentMode.CHARGE
    assert intent.deadline == when.replace(hour=6, minute=0, second=0, microsecond=0).timestamp()


def test_intent_for_schedule_wrap_deadline_is_next_day():
    schedule = Schedule(
        [ScheduleWindow(dt.time(22), dt.time(6), MODE_CHARGE, 80, ALL_DAYS)]
    )
    when = dt.datetime(2026, 11, 18, 23, 0)
    intent = intent_for_schedule(schedule, when=when)
    assert intent.mode is IntentMode.CHARGE
    expected = when.replace(hour=6, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    assert intent.deadline == expected.timestamp()


def test_intent_for_schedule_without_active_window_is_idle():
    schedule = Schedule(
        [ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, ALL_DAYS)]
    )
    intent = intent_for_schedule(schedule, when=dt.datetime(2026, 11, 18, 12, 0))
    assert intent.mode is IntentMode.IDLE
    assert intent.deadline is None
