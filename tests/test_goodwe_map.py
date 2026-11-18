"""Tests for the GoodWe ET register map: decoding, encoding and windows.

The map is data, so these tests pin down the data's *meaning*: the sign and
scale of each field kind, the string/time helpers, and the two Eco-mode window
layouts the firmware has used over time.
"""

from __future__ import annotations

import datetime as dt

import pytest

from proxy.canonical import INVERTER_MODEL, INVERTER_PV_POWER, INVERTER_TOU_WINDOWS, REGISTRY
from proxy.drivers import goodwe_map as gm
from proxy.schedule import (
    ALL_DAYS,
    MODE_CHARGE,
    MODE_DISCHARGE,
    MODE_OFF,
    Schedule,
    ScheduleWindow,
)

# --- low-level helpers ----------------------------------------------------


def test_u16_and_s16():
    regs = [0x0001, 0x7FFF, 0x8000, 0xFFFF]
    assert gm.u16(regs, 0) == 1
    assert gm.u16(regs, 3) == 0xFFFF
    assert gm.s16(regs, 1) == 32767
    assert gm.s16(regs, 2) == -32768
    assert gm.s16(regs, 3) == -1


def test_u32_and_s32():
    regs = [0x0001, 0x0002, 0xFFFF, 0xFFFE, 0x8000, 0x0000]
    assert gm.u32(regs, 0) == 0x00010002
    assert gm.s32(regs, 0) == 0x00010002
    assert gm.s32(regs, 2) == -2
    assert gm.s32(regs, 4) == -2147483648


def test_regs_to_bytes_is_big_endian():
    assert gm.regs_to_bytes([0x0102, 0x0304]) == b"\x01\x02\x03\x04"


def test_decode_string_stops_at_nul_and_strips():
    regs = [0x4142, 0x4300, 0x4444]  # "ABC\0DD"
    assert gm.decode_string(regs, 0, 6) == "ABC"


def test_decode_string_replaces_non_ascii():
    regs = [0xFF41]  # 0xFF is not ASCII
    assert gm.decode_string(regs, 0, 2) == "\ufffdA"


def test_time_codec_round_trips():
    assert gm.decode_time(0x0200) == dt.time(2, 0)
    assert gm.decode_time(0x173B) == dt.time(23, 59)
    assert gm.encode_time(dt.time(2, 0)) == 0x0200
    assert gm.encode_time(dt.time(23, 59)) == 0x173B
    # Out-of-range values are wrapped rather than rejected.
    assert gm.decode_time(0x1964) == dt.time(1, 40)


# --- field decoding -------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "regs", "expected"),
    [
        ("u16", [0xFFFF], 65535),
        ("s16", [0xFF9C], -100),
        ("u32", [0x0001, 0x0000], 0x00010000),
        ("s32", [0xFFFF, 0x0000], -65536),
        ("voltage", [2300], 230.0),
        ("current", [31], 3.1),
        ("current_s", [0xFFE2], -3.0),
        ("freq", [5000], 50.0),
        ("temp", [325], 32.5),
        ("energy", [234], 23.4),
        ("energy4", [0x0000, 0x3039], 1234.5),
        ("cell_mv", [3320], 3320),
        ("bool", [1], True),
        ("bool", [0], False),
        ("byteh", [0xFF9C], 0xFF),
        ("bytel", [0xFF9C], 0x9C),
    ],
)
def test_decode_field_kinds(kind, regs, expected):
    field_ = gm.Field(index=0, key="x", kind=kind)
    assert gm.decode_field(field_, regs) == expected


def test_decode_field_temp_sentinels_are_none():
    field_ = gm.Field(index=0, key="x", kind="temp")
    assert gm.decode_field(field_, [0xFFFF]) is None  # -1
    assert gm.decode_field(field_, [0x7FFF]) is None  # 32767


def test_decode_field_enum_known_and_unknown():
    field_ = gm.Field(index=0, key="x", kind="enum", enum={1: "auto", 3: "eco"})
    assert gm.decode_field(field_, [3]) == "eco"
    assert gm.decode_field(field_, [9]) == 9  # unmapped stays raw


def test_decode_field_unknown_kind_raises():
    with pytest.raises(ValueError):
        gm.decode_field(gm.Field(index=0, key="x", kind="nope"), [0])


# --- field encoding -------------------------------------------------------


def test_encode_field_scalars_and_signs():
    assert gm.encode_field(gm.Field(0, "x", "u16"), 65535) == [0xFFFF]
    assert gm.encode_field(gm.Field(0, "x", "s16"), -100) == [0xFF9C]
    assert gm.encode_field(gm.Field(0, "x", "voltage"), 230.0) == [2300]
    assert gm.encode_field(gm.Field(0, "x", "current_s"), -3.0) == [0xFFE2]
    assert gm.encode_field(gm.Field(0, "x", "freq"), 50.0) == [5000]
    assert gm.encode_field(gm.Field(0, "x", "temp"), 32.5) == [325]
    assert gm.encode_field(gm.Field(0, "x", "energy"), 23.4) == [234]
    assert gm.encode_field(gm.Field(0, "x", "bool"), True) == [1]
    assert gm.encode_field(gm.Field(0, "x", "bool"), False) == [0]


def test_encode_field_wide_values_split_into_two_registers():
    assert gm.encode_field(gm.Field(0, "x", "u32"), 0x00010002) == [0x0001, 0x0002]
    assert gm.encode_field(gm.Field(0, "x", "energy4"), 1234.5) == [0x0000, 0x3039]


def test_encode_field_enum_by_name_and_invalid():
    field_ = gm.Field(0, "x", "enum", enum={1: "auto", 3: "eco"})
    assert gm.encode_field(field_, "eco") == [3]
    assert gm.encode_field(field_, 5) == [5]
    with pytest.raises(ValueError):
        gm.encode_field(field_, "nope")


def test_encode_field_unknown_kind_raises():
    with pytest.raises(ValueError):
        gm.encode_field(gm.Field(0, "x", "nope"), 1)


# --- Time-of-Use windows --------------------------------------------------


def test_window_v1_round_trips():
    schedule = Schedule(
        [
            ScheduleWindow(dt.time(2, 0), dt.time(6, 0), MODE_CHARGE, 80, ALL_DAYS),
            ScheduleWindow(dt.time(14, 0), dt.time(18, 0), MODE_DISCHARGE, 50, 0x3E),
        ]
    )
    regs = gm.encode_windows_v1(schedule)
    assert len(regs) == 16  # four windows, four registers each
    decoded = gm.decode_windows_v1(regs)
    assert decoded.windows[0] == schedule.windows[0]
    assert decoded.windows[1] == schedule.windows[1]


def test_window_v1_disabled_forces_off():
    # on_off byte 0x00 must override a non-zero power.
    regs = [0x0200, 0x0600, (-80) & 0xFFFF, 0x007F]
    decoded = gm.decode_windows_v1(regs)
    assert decoded.windows[0].mode == MODE_OFF


def test_window_v1_padding_is_off():
    regs = gm.encode_windows_v1(Schedule())
    assert len(regs) == 16
    assert all(reg == 0 for reg in regs)


def test_window_v2_round_trips():
    schedule = Schedule(
        [ScheduleWindow(dt.time(1, 30), dt.time(5, 30), MODE_CHARGE, 90, 0x7F)]
    )
    regs = gm.encode_windows_v2(schedule)
    assert len(regs) == 24  # four windows, six registers each
    decoded = gm.decode_windows_v2(regs)
    assert decoded.windows[0] == schedule.windows[0]


def test_windows_for_block_picks_the_layout():
    assert gm.windows_for_block(gm.TOU_V1, Schedule()) == gm.encode_windows_v1(Schedule())
    assert gm.windows_for_block(gm.TOU_V2, Schedule()) == gm.encode_windows_v2(Schedule())


# --- derived values and map invariants ------------------------------------


def test_pv_power_sums_the_panels():
    regs = [0] * 24
    regs[5], regs[6] = (3000 >> 16) & 0xFFFF, 3000 & 0xFFFF
    regs[9], regs[10] = (1500 >> 16) & 0xFFFF, 1500 & 0xFFFF
    regs[13], regs[14] = (2000 >> 16) & 0xFFFF, 2000 & 0xFFFF
    regs[17], regs[18] = (500 >> 16) & 0xFFFF, 500 & 0xFFFF
    assert gm._pv_power(regs) == 7000


def test_pv_power_ignores_negative_and_short_blocks():
    assert gm._pv_power([]) == 0
    regs = [0] * 20
    regs[5], regs[6] = 0xFFFF, 0xFFF6  # -10 W
    assert gm._pv_power(regs) == 0


def test_blocks_have_sane_spans():
    assert gm.DEVICE_INFO.offset == 35000
    assert gm.RUNTIME.offset == 35100
    assert gm.RUNTIME.count == 125  # the dongle's per-read maximum
    assert gm.BATTERY_INFO.offset == 37000
    assert gm.WORK_MODE.offset == 47000
    assert gm.EXPORT.offset == 47509
    assert gm.EMS.offset == 47511
    assert gm.TOU_V1.offset == 47515
    assert gm.TOU_V2.offset == 47547


def test_default_and_optional_blocks_are_named():
    names = {block.name for block in gm.DEFAULT_BLOCKS}
    assert {"device_info", "runtime", "battery_info", "work_mode", "tou_v1"} <= names
    assert {block.name for block in gm.OPTIONAL_BLOCKS} == {"tou_v2"}


def test_tou_write_ranges_match_the_tou_blocks():
    for start, count in gm.TOU_WRITE_RANGES:
        block = next(b for b in (*gm.DEFAULT_BLOCKS, *gm.OPTIONAL_BLOCKS) if b.offset == start)
        assert block.count == count


def test_every_mapped_key_is_registered():
    for block in (*gm.DEFAULT_BLOCKS, *gm.OPTIONAL_BLOCKS):
        for field_ in block.fields:
            assert field_.key in REGISTRY, field_.key
        for derived in block.derived:
            assert derived.key in REGISTRY, derived.key


def test_known_keys_are_mapped_somewhere():
    mapped = {
        entry.key
        for block in (*gm.DEFAULT_BLOCKS, *gm.OPTIONAL_BLOCKS)
        for entry in (*block.fields, *block.derived)
    }
    assert INVERTER_MODEL in mapped
    assert INVERTER_PV_POWER in mapped
    assert INVERTER_TOU_WINDOWS in mapped
