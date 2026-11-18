"""Tests for the brand-independent Time-of-Use schedule."""

from __future__ import annotations

import datetime as dt

import pytest

from proxy.schedule import (
    ALL_DAYS,
    MODE_CHARGE,
    MODE_DISCHARGE,
    MODE_OFF,
    WEEKDAYS,
    WEEKEND,
    Schedule,
    ScheduleWindow,
    day_bit,
    days_to_mask,
    mask_to_days,
)


def test_day_bit_follows_inverter_order():
    # Sunday = bit 0 .. Saturday = bit 6.
    assert day_bit(dt.date(2026, 11, 15)) == 1 << 0  # a Sunday
    assert day_bit(dt.date(2026, 11, 16)) == 1 << 1  # a Monday
    assert day_bit(0) == 1 << 1  # Python weekday 0 is Monday
    assert day_bit(6) == 1 << 0  # Python weekday 6 is Sunday


def test_days_to_mask_understands_words():
    assert days_to_mask("all") == ALL_DAYS
    assert days_to_mask("every day") == ALL_DAYS
    assert days_to_mask("weekdays") == WEEKDAYS
    assert days_to_mask("weekend") == WEEKEND
    assert days_to_mask("") == 0
    assert days_to_mask("none") == 0
    assert days_to_mask("mon,tue") == (1 << 1) | (1 << 2)


def test_mask_to_days_round_trips():
    assert mask_to_days(ALL_DAYS) == "sun,mon,tue,wed,thu,fri,sat"
    assert mask_to_days(0) == "none"
    assert mask_to_days(WEEKDAYS) == "mon,tue,wed,thu,fri"


def test_window_signed_power_sign_convention():
    charge = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80)
    discharge = ScheduleWindow(dt.time(2), dt.time(6), MODE_DISCHARGE, 80)
    off = ScheduleWindow(dt.time(2), dt.time(6), MODE_OFF, 80)
    assert charge.signed_power == -80
    assert discharge.signed_power == 80
    assert off.signed_power == 0
    assert charge.enabled and discharge.enabled and not off.enabled


def test_window_coerces_inputs():
    window = ScheduleWindow("02:00", "06:00", "nonsense", 250, 0x1FF)
    assert window.start == dt.time(2, 0)
    assert window.stop == dt.time(6, 0)
    assert window.mode == MODE_OFF  # unknown mode falls back to off
    assert window.power == 100  # clamped to 0..100
    assert window.days == 0xFF  # masked to a byte


def test_window_covers_normal_range():
    window = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, ALL_DAYS)
    assert window.covers(dt.datetime(2026, 11, 18, 4, 0))  # Wednesday 04:00
    assert not window.covers(dt.datetime(2026, 11, 18, 1, 59))
    assert not window.covers(dt.datetime(2026, 11, 18, 6, 0))  # stop is exclusive


def test_window_covers_wrap_past_midnight():
    window = ScheduleWindow(dt.time(22), dt.time(6), MODE_CHARGE, 80, ALL_DAYS)
    assert window.covers(dt.datetime(2026, 11, 18, 23, 0))
    assert window.covers(dt.datetime(2026, 11, 18, 5, 0))
    assert not window.covers(dt.datetime(2026, 11, 18, 12, 0))


def test_window_respects_day_mask():
    monday_only = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, day_bit(0))
    assert monday_only.covers(dt.datetime(2026, 11, 16, 4, 0))  # Monday
    assert not monday_only.covers(dt.datetime(2026, 11, 17, 4, 0))  # Tuesday


def test_window_round_trips_through_dict():
    window = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, WEEKDAYS)
    restored = ScheduleWindow.from_dict(window.to_dict())
    assert restored == window


def test_schedule_active_returns_first_match():
    schedule = Schedule(
        [
            ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80, ALL_DAYS),
            ScheduleWindow(dt.time(14), dt.time(18), MODE_DISCHARGE, 50, ALL_DAYS),
        ]
    )
    assert schedule.active(dt.datetime(2026, 11, 18, 4)).mode == MODE_CHARGE
    assert schedule.active(dt.datetime(2026, 11, 18, 15)).mode == MODE_DISCHARGE
    assert schedule.active(dt.datetime(2026, 11, 18, 12)) is None


def test_schedule_from_dict_accepts_list_and_wrapper():
    items = [
        {"start": "02:00", "stop": "06:00", "mode": "charge", "power": 80},
        {"start": "14:00", "stop": "18:00", "mode": "discharge", "power": 50},
    ]
    from_list = Schedule.from_dict(items)
    from_wrapper = Schedule.from_dict({"windows": items})
    assert len(from_list) == 2
    assert len(from_wrapper) == 2
    assert from_list.as_list() == from_wrapper.as_list()


def test_schedule_from_dict_passes_through_schedule():
    schedule = Schedule([ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80)])
    assert Schedule.from_dict(schedule) is schedule
    assert len(Schedule.from_dict(None)) == 0


def test_window_with_mode_keeps_power():
    window = ScheduleWindow(dt.time(2), dt.time(6), MODE_CHARGE, 80)
    off = window.with_mode(MODE_OFF)
    assert off.mode == MODE_OFF and off.power == 80
    other = window.with_mode(MODE_DISCHARGE, 40)
    assert other.mode == MODE_DISCHARGE and other.power == 40


def test_invalid_time_strings_raise():
    with pytest.raises((ValueError, TypeError)):
        ScheduleWindow("nonsense", "06:00")
