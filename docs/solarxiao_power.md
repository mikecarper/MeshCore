<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/solarxiao_power/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# SolarXiao carrier profiles, MPPT and battery controls

SolarXiao Repeater and QSPI OTA Room Server firmware support the CN3791 Base,
older BQ24650 Advanced and LTC4162/INA260 MPPT carriers in Full and Reduced builds.
Use USB CLI or the
authenticated administrator CLI over LoRa. Ship mode and
saved-policy reset require local USB. Commands do not change RF settings.

## Automatic detection and manual overrides

There are three power profiles: `auto`, `basic` and `mppt`. Base and Advanced
share `basic` because their onboard power telemetry and software controls are
the same; their different analog charger designs require no different driver.
The default is `auto` when no carrier selection has been saved. Saved MPPT
selections remain manual overrides, and saved Base/Advanced selections load as
`basic`. Use `set solar.board auto` to enable detection on such an installation.
The firmware environment, hardware/OTA identity and flash layout are unchanged.

```
get solar.board
set solar.board auto
get solar
get solar capabilities
get solar readings
get solar history
```

Auto mode checks the INA260 manufacturer's ID (`0xfe = 0x5449`) and device ID
(`0xff & 0xfff0 = 0x2270`) at carrier address `0x40`. An address ACK alone is not
enough: another device such as INA219 can share that address. Detection only
reads identity registers; it does not configure/reset either chip. The first
probe occurs in the loop after initialization, then retries every five seconds
until a valid identity is found. A positive result identifies MPPT for these
carrier designs and is retained until reboot or a new board selection.
Temporary communication loss does not relabel a previously detected MPPT.

Without a verified INA260, auto reports `self-only` and keeps retrying. It does
not claim to distinguish Base from Advanced: neither has a digital identity,
and missing responses can also mean an MPPT power/bus fault. Charger access,
saved-override replay and native carrier telemetry stay blocked until a valid
INA260 identity is found. XIAO self readings remain available throughout.
The MPPT carrier's INA260 was previously read with USB power only; detection
does not require the LTC4162 charger to respond at SOLAR/VIN.

Manual settings are available when the physical carrier is known:

| Setting | Behavior |
| --- | --- |
| `set solar.board auto` | Verify INA260 before enabling MPPT; otherwise self-only, retrying. |
| `set solar.board basic` | Base/Advanced self battery voltage and MCU temperature; no native I2C access. |
| `set solar.board mppt` | Enable native access without requiring INA260 detection; existing charger write identity checks still apply. |

Legacy `base` and `advanced` command values are compatibility aliases for
`basic`; they are not separate modes. Readbacks and help use the three canonical
names. Existing Base/Advanced records load as `basic` without being rewritten;
new Basic saves use the original Advanced record value, preserving the same
self-only behavior across upgrades.

For Base or Advanced, `basic` reports the common hardware capabilities and live
XIAO battery ADC and processor temperature. It does not claim which analog
charger is fitted.
The existing self telemetry stays on channel 1, with voltage and temperature as
different LPP types. It adds no invented charger flags, current, watts, battery
NTC temperature or panel measurements. `get solar battery` aliases the self
readings; the MCU temperature is not a battery or charger temperature.
If a node already reports battery voltage and MCU temperature on channel 1,
this profile adds no further physical measurements. It adds carrier detection,
manual overrides and capability reporting; the voltage/temperature history commands
also already exist in builds that include telemetry history.

The [2025 Base Standard schematic](https://github.com/wehooper4/Meshtastic-Hardware/blob/main/XaioSeries/SolarXaio/Base/Standard/Schematic_SolarXaio_Base_2025-10-23.pdf)
uses a CN3791 analog charger with no digital interface or INA monitor. Battery
voltage reaches the XIAO BAT input. Charge status drives an LED, and the other
charger signals are not connected to XIAO measurement/control inputs. Base
therefore needs no extra telemetry driver: only self battery voltage and MCU
temperature are available, just like Advanced. Base Lite publishes a README
but no schematic, so its exact connections are not independently qualified here.

The [2025 Advanced schematic](https://github.com/wehooper4/Meshtastic-Hardware/blob/main/XaioSeries/SolarXaio/Advanced/Standard/Schematic_SolarXaio_ADV_2025-10-23.pdf)
has no INA monitor or digital charger interface. Charger status pins drive LEDs,
NTC goes only to the charger, and current/voltage/MPP setpoints use hardware
resistors, switches and a solder jumper. An OTA update cannot measure those
signals or change those setpoints without additional hardware connections.

Manual Basic mode suppresses native LTC4162/INA260 polling, register reads/writes,
replay and native measurements on channels 2..13. `get mppt`, `set mppt ...` and
`mppt ...` explain that Basic Base/Advanced layouts have no digital charger interface.
Pending auto mode only probes INA260 identity; it does not access the charger.
The generic sensor scanner still reserves native addresses 0x40 and 0x68, so it
cannot bypass that policy. Optional external sensor channels still start at 14.
Historical samples retained from a previous carrier selection describe the old
samples, not current sensor availability.

Voltage history reserves native channel IDs 2..5 at boot, even while Auto is
pending or Basic is selected. This is schema allocation, not chip access or a
measurement. It lets later detection, charger input power or a manual mode
change supply valid samples without restarting or clearing unrelated history.
Inactive readings remain missing, and empty channels do not appear in populated
history results. Native and optional I2C voltage history use the existing
`get telemetry.volt.i2c <channel> [1-4]` reader: four days of 30-minute RAM samples.

Where telemetry history is included, `get solar history` points to the existing
`get telemetry.volt [1-7]` and `get telemetry.temp [1-7]` commands: 30-minute
samples, seven days in RAM. History starts filling after boot and resets at
reboot. Voltage trends can help assess a remote installation, but are not a
measurement of solar power, charge current or charge state. Radio/status and
history-transfer commands remain the existing role commands.

The selection is stored separately in an eight-byte, versioned checked record
at `/solar_board`, using atomic publication and full public readback. A corrupt,
unreadable or unconfirmed selection blocks native access and replay. Self ADC
and MCU readings remain available. An explicit successful `set solar.board`
recovers selection. With an unavailable filesystem, the selection cannot be
saved and native access remains blocked.

When auto verifies MPPT or a manual MPPT selection re-enables native access,
the firmware reloads any retained `/solar_power` charger overrides. Changing the
carrier does not erase those overrides, change RF/PA settings, modify battery
protection, or write charger defaults. The INA260 identity verifies the monitor;
LTC4162 chemistry and cell-count checks still independently guard charger writes.

Radio target and role selection remain separate from the power profile:

| Fitted module | Repeater | Room Server |
| --- | --- | --- |
| E22 M30S | `solarxiao_30S_repeater` | `solarxiao_30S_room_server_lora_ota` |
| E22 M33S | `solarxiao_33S_repeater` | `solarxiao_33S_room_server_lora_ota` |

All three power profiles work in these role builds. Room uses its existing
onboard-QSPI OTA target, identity and application layout, with the matching
board/storage-aware OTAFIX bootloader. The older plain `*_room_server` targets
remain separate compatibility/recovery recipes; this native interface is
qualified on the QSPI OTA Room targets. No legacy identity is remapped.

The
published Base Standard schematic specifies M30S; the pictured 2W Advanced
uses M33S. Charger detection cannot identify the PA variant or select its TX
power limits.

A remotely deployed node must first receive compatible firmware before these
commands can be used. Deployment still requires its installed target and
bootloader compatibility to be checked when it is reachable.

See the [Room Server and late-detection history validation](test-results/solarxiao_room_2026-10-21.md)
and [three-profile validation](test-results/solarxiao_basic_2026-10-21.md).
This adds no measurements beyond existing self voltage/MCU temperature and has
not been installed on the remote Advanced board.

## MPPT carrier commands

Start with:

```
get mppt
get mppt battery
get mppt power
get mppt alerts
get mppt saved
get mppt fields 0
```

`fields` returns three names per page with `rw`, `ro` or `locked`. Increment the
page to enumerate the register interface. `get mppt <field>` returns units and
the raw register. `get mppt raw 0x43` reads a documented register; arbitrary
register writes are not exposed. `nan` in a summary means unavailable, not zero.

## Identity and readings

The carrier bus uses SDA P0.09 and SCL P0.10. INA260 at `0x40` must match its TI
manufacturer and device IDs. Its signed current, voltage and power readings do
not require charger input power. The driver does not reset/configure INA260.
LTC4162 at `0x68` uses little-endian SMBus words. Access normally requires power
at SOLAR/VIN; USB alone can leave the charger unavailable.

`get mppt info` reports the chemistry/profile code, live cell count, last observed
nonzero cell count, MPPT, JEITA, charger state and telemetry validity. The actual
bench board identified as FST (chemistry code 6); this distinguishes it from
FAD, FFS, Li-ion and SLA variants. The ID does not uniquely identify the M suffix
or package/temperature grade. Cell count comes from hardware straps; zero means
unknown while the charger is inactive. Software cannot change chemistry/cells.

Readings cover battery/input/output voltage, signed battery/input current,
charger die and modeled NTC temperature, battery series resistance, charge/CV/
absorb timers, JEITA region, actual DAC settings, system/status bits and all three
alert banks. A retained ADC bank is not presented as live while telemetry is off.
Battery ADC voltage/resistance need a currently valid cell count. Questionable
resistance and an open/disconnected NTC return unavailable in the summaries.

## Settings on the 4S FST carrier

Use `set mppt <field> <value>`. Booleans accept `on` or `off` only.

| Fields | Behavior |
| --- | --- |
| `mppt.enabled` | Enable automatic solar maximum power tracking. |
| `charge.enabled` | Suspend/resume charging; does not disconnect the battery or system output. |
| `telemetry.force`, `telemetry.fast` | Force the charger ADC on, or use fast telemetry. These affect power consumption. |
| `input.limit_ma` | Input current limit; rounds down to the hardware step, never above the request; maximum request 3200 mA. |
| `input.minimum_mv` | Manual input-voltage floor; MPPT must be off. Rounds up in 140.625 mV steps, maximum request 35000 mV. |
| `jeita.enabled` | Select the fixed multi-region JEITA profile or basic temperature-qualified manual targets. |
| `charge.current_ma`, `charge.cell_mv` | Manual targets require JEITA off. Current rounds down in resistor-dependent steps, up to 3200 mA. This 4S board interface limits voltage requests to 3412.5..3600 mV/cell in 12.5 mV steps. |
| `thermal.start_c`, `thermal.end_c` | Die thermal regulation thresholds, -40..125 C; start must remain below end. |
| `termination.enabled`, `termination.current_ma` | C/x termination switch and nonnegative current threshold. |
| `termination.cv_seconds`, `termination.total_minutes` | 16-bit safety timers; zero disables the corresponding timer. |
| `alert.*` | Voltage/current/die/NTC/resistance thresholds and enabled alert masks; use field names/units from `fields`. |

FST locks the six JEITA thresholds, regional current/voltage values, recharge
threshold and absorb voltage/time. These remain readable. Manual target
registers are continuously overwritten by JEITA when JEITA is on; read actual
`dac.*` fields to see the active regulation targets. Basic temperature-qualified
charging still uses the fixed outer temperature limits when JEITA is off.

All configuration writes require a fresh FST identity and a live cell count of
4 or unknown. Other variants remain readable but cannot receive this board's
saved settings. Every setting uses a masked register update and verifies
readback before success. Unrelated bits are preserved; one-shot BSR is not
replayed by configuration writes. A failed write/save attempts to restore the
previous field and reports whether rollback was verified.

## Saving and actions

Successful settings save an explicit override in a versioned, CRC-protected
`/solar_power` record, using atomic file replacement plus public-file readback.
The board reapplies only saved overrides after restart or charger power loss,
with a five-second retry interval. It does not write default settings on first
boot. JEITA-owned targets are not repeatedly written while JEITA is enabled.
A failed/corrupt settings file blocks configuration saves and automatic replay.

- `mppt reload`: reload saved policy and retry applying it.
- `mppt forget`: remove saved register overrides; keep calibration and live chip settings.
- `mppt reset`: local USB only; reset saved policy/calibration to reference values,
  including recovery from a corrupt record. Live charger registers are unchanged.
- `mppt bsr run`: request one battery-resistance measurement; not saved.
- `mppt ship arm` / `mppt ship disarm`: local USB only; not saved. Arming takes
  effect when VIN drops below approximately 1 V and can remove system power.
- `mppt alerts clear limits|states|status <mask>`: clear selected latched alerts
  using write-zero-to-clear semantics; unselected/new alerts are preserved.

These actions acknowledge the request, not completion of charging, resistance
measurement or entry into ship mode. Read the corresponding status afterward.

See the [bench acceptance report](test-results/solarxiao_power_2026-10-21.md)
for exact firmware identity, measured readouts and test limits.

## Emergency battery schedule

The [emergency schedule](cli_commands.md#emergency-battery-schedule) keeps the
radio and normal work awake for the first 15 minutes of each UTC hour, with
timed sleep for the remaining 45 minutes. It starts automatically at 5% and
recovers at 7%. Forwarding thresholds now default to 25% for floods and 15%
for all relayed traffic; those forwarding gates remain opt-in.

```text
get power.emergency
set power.emergency on
set power.awake 30
set power.emergency auto
```

The percentage follows the configured self battery voltage endpoints used by
`battery.forward`. On MPPT carriers this is the XIAO ADC, not an estimate of
the separate carrier battery's charge from INA260/LTC4162. Check the actual
voltage source before relying on automatic entry. Manual mode and timed wake
work independently of the charger. Hard low-voltage protection still applies.

## Conversion defaults and carrier revisions

The supplied [Standard schematic](https://github.com/wehooper4/Meshtastic-Hardware/blob/main/XaioSeries/SolarXaio/MPPT/Standard/Schematic_SolarXaio_MPPT_2025-10-23.pdf)
shows R5/RSNSI and R14/RSNSB at 10 milliohms, a 10 kohm NTC bias and a 10 kohm
NTC. It shows an L42M single-cell charger, so it does not independently confirm
component values on this 12 V FSTM revision. Defaults use those resistor values
and the LTC4162 reference thermistor beta of 3490 K. Readings identify the model
as `reference` until the corresponding calibration values are explicitly set.

Calibration settings, saved without changing hardware target registers:

```
get mppt cal.rsnsb_mohm
get mppt cal.rsnsi_mohm
get mppt cal.ntc_bias_ohm
get mppt cal.ntc_r25_ohm
get mppt cal.ntc_beta
```

Set these with the same `set mppt` syntax after confirming actual components.
Allowed sense resistance is 0.1..1000 milliohms, NTC/bias resistance 100..1000000
ohms, beta 1000..10000 K. The simple beta temperature model assumes an unpadded
NTC divider. It does not model series/parallel thermistor dilution networks.
Changing calibration changes units/conversions, not already saved register codes.
INA260 uses its integrated shunt and does not use these sense-resistor values.

## Telemetry channels

The following carrier channels apply to MPPT mode. Advanced mode reports the
existing self voltage/MCU temperature on channel 1 and omits native channels.

Carrier telemetry requires the environmental telemetry permission. Channel 1
remains the XIAO internal ADC; it is separate from the 4S carrier battery and its
existing startup/low-voltage policy is unchanged.

| Channel | Values |
| --- | --- |
| 2 | INA260 battery voltage, signed current, power |
| 3 | LTC4162 input voltage, signed current, computed nonnegative input power |
| 4 | Charger system output voltage |
| 5 | Charger battery ADC voltage and signed current |
| 6, 7 | Die temperature, modeled NTC temperature |
| 8, 9, 10 | Generic sensor integers: charger state, charge status, system bits |
| 11, 12, 13 | Generic sensor integers: limit, state and status alert masks |
| 14 onward | Optional environmental sensors, in discovery order |

Unavailable values are omitted. Voltage history reserves channels 2..5 and
marks unavailable samples invalid. The generic sensor scanner cannot claim
carrier addresses `0x40`/`0x68`; this prevents an INA219 driver from configuring
the INA260 and keeps carrier channels stable across profiles and power states.
No state-of-charge percentage or coulomb counting is inferred from voltage.

References: [SolarXaio hardware](https://github.com/wehooper4/Meshtastic-Hardware/tree/main/XaioSeries/SolarXaio),
[LTC4162-F Rev A](https://www.analog.com/media/en/technical-documentation/data-sheets/ltc4162-f.pdf),
[INA260](https://www.ti.com/lit/ds/symlink/ina260.pdf).
