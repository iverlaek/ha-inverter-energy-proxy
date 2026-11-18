# GoodWe ET-family register reference

This documents the GoodWe register map the proxy uses. It is implemented as data
in `proxy/drivers/goodwe_map.py`. The offsets, scales and layouts agree with the
open-source [`goodwe`](https://github.com/marcelblijleven/goodwe) library's
`et.py`/`sensor.py`, and have been checked against a live **GW10K-ET** dongle.

## Transport

The Wi-Fi/LAN dongle speaks a Modbus/RTU variant over **UDP port 8899**:

```
request    [addr][cmd][off_hi][off_lo][val_hi][val_lo][crc_lo][crc_hi]   (8 bytes)
read ans    AA 55 [addr][03][byte_count][data ...][crc_lo][crc_hi]
write ans   AA 55 [addr][06][off_hi][off_lo][val_hi][val_lo][crc_lo][crc_hi]
exception   AA 55 [addr][cmd|80][code][crc_lo][crc_hi]
```

* `addr` defaults to `0xF7` (`comm_addr`).
* `cmd` is `0x03` (read holding registers) or `0x06` (write single register).
* The CRC is Modbus CRC-16 (poly `0xA001`) over the frame **without** the leading
  `AA 55` and without the CRC itself.
* At most 125 registers per read.

The dongle is flaky: it may answer with an oversized datagram holding several
concatenated Modbus frames, a fragment, or nothing at all. The client only
accepts a CRC-valid frame whose byte count matches the request, preferring the
last such frame, and retries a few times. See `proxy/transport/goodwe_udp.py`.

## Value scales

| kind | encoding |
| --- | --- |
| `u16` / `s16` | one register, unsigned / signed |
| `u32` / `s32` | two registers, big-endian, unsigned / signed |
| `voltage` | `u16 / 10` (V) |
| `current` | `u16 / 10` (A) |
| `current_s` | `s16 / 10` (A) |
| `freq` | `s16 / 100` (Hz) |
| `temp` | `s16 / 10` (°C); raw `-1` and `32767` mean "no reading" |
| `energy` | `u16 / 10` (kWh) |
| `energy4` | `u32 / 10` (kWh) |
| `cell_mv` | raw register (mV) |
| `bool` | `u16 != 0` |
| `byteh` / `bytel` | high / low byte of the register |
| `enum` | mapped through the field's enum table |

## Register blocks

The poller reads each of these spans once per cycle.

| block | offset | count | optional | notes |
| --- | ---: | ---: | --- | --- |
| `device_info` | 35000 | 33 | yes | rated power, serial, model, firmware |
| `runtime` | 35100 | 125 | no | grid, PV, load, battery, energies |
| `meter` | 36000 | 45 | yes | reserved / not decoded yet |
| `battery_info` | 37000 | 24 | yes | SOC, SOH, cell voltages/temperatures |
| `work_mode` | 47000 | 1 | no | configured operating mode |
| `export` | 47509 | 2 | no | export enable + limit |
| `ems` | 47511 | 2 | no | external EMS mode + setpoint |
| `tou_v1` | 47515 | 16 | no | Eco-mode windows (4 regs each) |
| `tou_v2` | 47547 | 24 | yes | Eco-mode windows (6 regs each) |

### `device_info` (35000)

| index | offset | key | kind |
| ---: | ---: | --- | --- |
| 1 | 35001 | `inverter.rated_power` | `u16` W |
| 3 | 35003 | `inverter.serial` | 16-byte ASCII |
| 11 | 35011 | `inverter.model` | 10-byte ASCII |
| 21 | 35021 | `inverter.firmware` | 12-byte ASCII |

### `runtime` (35100)

| index | offset | key | kind |
| ---: | ---: | --- | --- |
| 5,9,13,17 | 35105/09/13/17 | `inverter.pv_power` | sum of PV1–4 (`s32`, W) |
| 21 | 35121 | `inverter.grid_voltage` | `voltage` V |
| 23 | 35123 | `inverter.grid_frequency` | `freq` Hz |
| 38 | 35138 | `inverter.ac_power` | `s16` W |
| 40 | 35140 | `inverter.grid_power` | `s16` W (+ import, − export) |
| 72 | 35172 | `inverter.load_power` | `s16` W |
| 76 | 35176 | `inverter.temperature` | `temp` °C |
| 80 | 35180 | `battery.voltage` | `voltage` V |
| 81 | 35181 | `battery.current` | `current_s` A |
| 82 | 35182 | `battery.power` | `s32` W (+ charging) |
| 84 | 35184 | `battery.mode` | enum |
| 87 | 35187 | `inverter.status_mode` | enum |
| 91 | 35191 | `inverter.energy_pv_total` | `energy4` kWh |
| 93 | 35193 | `inverter.energy_pv_today` | `energy4` kWh |
| 99 | 35199 | `inverter.energy_export_today` | `energy` kWh |
| 102 | 35202 | `inverter.energy_import_today` | `energy` kWh |
| 108 | 35208 | `inverter.energy_battery_charge_today` | `energy` kWh |
| 111 | 35211 | `inverter.energy_battery_discharge_today` | `energy` kWh |

### `battery_info` (37000)

| index | offset | key | kind |
| ---: | ---: | --- | --- |
| 3 | 37003 | `battery.temperature` | `temp` °C |
| 4 | 37004 | `battery.charge_limit` | `u16` A |
| 5 | 37005 | `battery.discharge_limit` | `u16` A |
| 7 | 37007 | `battery.soc` | `u16` % |
| 8 | 37008 | `battery.soh` | `u16` % |
| 20 | 37020 | `battery.cell_temperature_max` | `temp` °C |
| 21 | 37021 | `battery.cell_temperature_min` | `temp` °C |
| 22 | 37022 | `battery.cell_voltage_max` | `cell_mv` mV |
| 23 | 37023 | `battery.cell_voltage_min` | `cell_mv` mV |

### Control registers

| offset | key | kind | notes |
| ---: | --- | --- | --- |
| 47000 | `inverter.work_mode` | enum | see below |
| 47509 | `inverter.export_enabled` | `bool` | allow grid export |
| 47510 | `inverter.export_limit` | `u16` W | export cap |
| 47511 | `inverter.ems_mode` | enum | see below |
| 47512 | `inverter.ems_power_limit` | `s16` W | + discharges, − charges |

## Enumerations

**Work mode (47000):** `0` general, `1` off_grid, `2` backup, `3` eco,
`4` peak_shaving, `5` self_use.

**Status (35187):** `0` wait, `1` normal, `2` off_grid, `3` fault, `4` flash,
`5` check.

**Battery mode (35184):** `0` none, `1` standby, `2` discharge, `3` charge,
`4` to_charge, `5` to_discharge.

**EMS mode (47511):** `1` auto, `2` charge_pv, `3` discharge_pv, `4` import_ac,
`5` export_ac, `6` conserve, `7` off_grid, `8` battery_standby, `9` buy_power,
`10` sell_power.

## Eco-mode / Time-of-Use windows

Two layouts exist, depending on the ARM firmware version. The proxy reads the
first (`tou_v1`) and treats the second (`tou_v2`) as optional.

**V1 — four registers per window (firmware < 19), base 47515:**

| word | meaning |
| --- | --- |
| 0 | start `HH<<8 | MM` |
| 1 | stop `HH<<8 | MM` |
| 2 | signed power percent (`s16`) |
| 3 | `on_off << 8 | days` (`on_off` `0xFF` = enabled) |

**V2 — six registers per window (firmware ≥ 19), base 47547:**

| word | meaning |
| --- | --- |
| 0 | start |
| 1 | stop |
| 2 | `on_off << 8 | days` |
| 3 | signed power percent |
| 4 | SoC target |
| 5 | month bits |

In both, the **power sign** is the direction: negative charges the battery,
positive discharges it, zero idles.

**Day mask** (bit 0 = Sunday … bit 6 = Saturday): all days `0x7F`, weekdays
`0x3E`, weekend `0x41`.

Writing any register inside `TOU_WRITE_RANGES` (`47515..47530` or
`47547..47570`) is decoded as a whole new schedule (`inverter.tou_windows`),
not as an isolated register write.

## Battery intents

A charge/discharge request is realised through the EMS registers by default:

| intent | EMS mode (47511) | setpoint (47512) |
| --- | --- | --- |
| `IDLE` | battery_standby (8) | — |
| `CHARGE` | import_ac (4) | `−power` W |
| `DISCHARGE` | discharge_pv (3) | `+power` W |
| `SELF_CONSUMPTION` | auto (1) | — |

With `control: eco` the request becomes a single `00:00–23:59` window on every
day: charging is a negative window power, discharging positive, and `idle` /
`self_consumption` fall back to work mode `general`.

## Sign conventions

* **Battery power** is positive when charging the battery.
* **Grid power** is positive when importing, negative when exporting.
* **EMS setpoint** is positive when discharging, negative when charging.
* **Eco window power** is negative when charging, positive when discharging.
