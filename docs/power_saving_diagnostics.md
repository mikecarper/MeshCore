<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/power_saving_diagnostics/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Power-saving diagnostics

Use `set powersaving.diag on` to collect the real sleep-policy decisions in
RAM, and `get powersaving.diag` to inspect them. This is available in Companion,
Repeater, Room Server and Sensor, without a separate diagnostic build. It does
not override any sleep guard. The production USB/DTR policy is unchanged.

## Suggested test

1. Record `get powersaving`, radio/RXPS settings, logging and bridge settings.
2. Run `set powersaving.diag on`, then close the USB terminal for the test.
3. Let the node run long enough to cover noise-floor calibration, queue work
   and scheduled tasks; a one-second snapshot can be misleading.
4. Reconnect and run `set powersaving.diag off` promptly to freeze the window.
5. Read `get powersaving.diag`, `get powersaving.diag calls`,
   `get powersaving.diag time` and pages `3` through `8`.
6. If necessary, clear and repeat with only one feature changed.

On ESP32, pages `9` and `10` also show SDK call-entry counts in sixteen
16-second buckets starting at `start_ms`. These cover the first 256 seconds;
`outside` counts later entries. Flag 1 marks entries outside that horizon,
flag 2 marks saturation and flag 4 marks a call without a millisecond timestamp.
A bucket with calls can establish activity within an independently timed test
interval only when its whole time range fits inside that interval. Whole-window
calls alone can all precede packet traffic. Buckets count SDK entries, not
successful returns, physical residency or a particular packet's wake event.
Check the existing call/return/error counters too. Other platforms keep pages
`0` through `8`.

Reconnecting/reading creates real USB or serial work and can affect the final
decision and counters. Prefer accumulated time over that final `why` value;
check its age. Off freezes recorded values, not the node itself. Re-enabling
after off starts a fresh window. Collection defaults off at every boot.

## Interpreting the result

- `policy.off`: the saved policy does not permit MCU sleep.
- `usb.session`: an actual host session or board-specific host/grace guard,
  not merely USB power from a charger.
- `usb.logging`: an existing logging guard (not a blanket nRF52 veto).
- `usb.recovery`: USB recovery/watchdog work needs service.
- `bridge`, `bridge.cleanup`, `bridge.wake`: active UART/network bridge,
  shutdown work or an overdue standby wake. A suspended RS232 owner is not
  diagnosed as running just because its saved enabled flag is on.
- `calibration`: noise-floor work is due now. The CPU can idle between sample
  deadlines even while the radio remains in continuous RX for calibration.
- `radio.watchdog`: radio observation/recovery work.
- `queue`, `retry`, `scheduled`, `mesh`, `ota`, `radio2`: pending role work.
- `wireless`, `network`, `serial`, `button`, `tracker`, `morse`: a matching
  existing service guard is active.
- `board`, `no.wake`: an inner board veto, SDK error or lack of a wake source.

`radio2` means the scanner cannot safely idle right now: for example, a short
remaining visit, calibration, pending reception or an unvalidated platform.
On nRF52 and ESP32, settled long visits can idle the MCU with power saving
enabled; the RF receiver stays on. ESP32 requires working radio GPIO wake and
physically stopped WiFi/Bluetooth drivers. Short visits and active transports
retain polling. See [radio profiles](radio_profiles.md#mcu-power-saving-while-scanning).

The role reports the first actual short-circuit blocker. It does not query
otherwise skipped hardware just to populate an exhaustive list. Board guards
may add reasons. Per-reason durations therefore are observed guard time, not
the independent energy cost of each feature. The state page's blocked time
is the union; adding per-reason totals can double-count overlap.

`calls` and `time` instrument actual nRF52 event waits, ESP32 manual light sleep
and generic RP2040/STM32 WFI paths. ESP32 automatic power-management yields are
not direct wait calls. A return under 100 us is counted as short; it can be a
legitimate pending interrupt/event, not an SDK failure. SDK errors have their
own counter and detail. Time inside the wait includes interrupt/SDK overhead;
it is not measured sleep residency. USB debugging/SWD can alter power and
clock behavior, so compare like-for-like setups.

These are not current measurements. Radio continuous RX, RXPS parameters,
FEM/LNA gain, GPS, displays and board regulators can explain large current
differences even with good MCU sleep. Use a current meter and matched settings
to attribute those loads. Counters are RAM-only, saturating, and consume at
most 256 bytes of collector state. Each wait-call interval must be shorter
than the 32-bit `micros()` wrap (about 71 minutes); the normal wait paths here
are much shorter. Keep gaps between policy observations below the `millis()`
wrap (about 49 days).

## RX power-saving recovery

`get radio.rxps.status` separates the saved request from the receive mode.
When an arm command fails, the radio falls back to continuous RX if that can
be started, while keeping RXPS requested. Automatic attempts are spaced by
5 seconds, 15 seconds, 60 seconds, then 5 minutes for persistent failures.
An idle node retries too; packet traffic is not needed to restart RXPS.

For example, `desired=on,effective=continuous,err=-708,fail=3` followed by
`retry=60s,streak=3` means the third consecutive arm attempt failed and normal
RX is being used until the next safe attempt. The cumulative `fail` counter
does not reset on a later success, while the consecutive `streak` does.
`hold=radio2`, `hold=calib` and `hold=timing` describe intentional pauses;
they are not additional failed arm attempts. `effective=inactive` does not
promise reception: check the driver error and radio diagnostics.

Retry deadlines participate in MCU wake scheduling, without becoming a
pending packet or preventing an idle tracker from suspending its radio.
Recovery uses the existing BUSY, TX, pending-IRQ and packet-ownership checks.
A busy radio defers the attempt without increasing the failure count.
Reapplying the same enabled setting during an error fallback requests a fresh
attempt; changing settings or rebooting is no longer necessary to clear a
permanent retry cutoff.

This recovery policy does not change LoRa timings, TCXO delays, transmit
preambles or RXPS levels. LR1121 capture margins and current savings still need
physical qualification; see the [existing LR1121 validation scope](test-results/lr1121_rxps_2026-10-27.md).

## ESP32 Bluetooth off and CPU sleep

Switching Bluetooth off now completes a reversible host/controller shutdown,
including when Bluetooth was saved off at startup. Existing pairings, address,
name and PIN are retained when it is switched back on. A pending disconnect or
failed teardown keeps the CPU awake rather than treating stopped advertising
as a stopped controller. `get bluetooth` reports a pending change until that
transition finishes.

NimBLE-Arduino 2.5.1 builds include the upstream host-timer shutdown fix
([NimBLE-Arduino #1185](https://github.com/h2zero/NimBLE-Arduino/pull/1185)).
Stopping Bluetooth preserves an already queued timer event's callback until
final host cleanup. Without this fix, switching Bluetooth off can reset an
ESP32 through a null callback. The version/hash-checked repair is applied to a
build-local source copy; the shared library installation is unchanged.

USB host sessions, logging, active WiFi/ESP-NOW, OTA and serial work keep their
existing sleep guards. This change does not sleep an ESP32 through a live
Bluetooth or WiFi connection. Such connections require SDK-managed modem and
automatic sleep; lowering CPU frequency or yielding alone is not proof of
light sleep.

The [ESP32 Bluetooth and radio2 qualification report](test-results/esp32_ble_radio2_sleep_2026-10-21.md)
records the tested firmware, saved-pairing checks, SDK wait measurements and
packet-test failures. SDK wait time is not a current measurement.

## ESP32 processor speed and temporary test controls

The WebConfig **Processor speed** selector and `set cpu.freq auto|80|160|240`
save the processor clock choice. `get cpu.freq` reports the choice, actual MHz
and supported values. The board rejects unsupported clocks. Auto retains the
normal policy: Companion selects 80 MHz with device power saving on and its
configured nominal speed with it off. Other roles retain their nominal speed.
While radio2 or a temporary second profile is active, Auto instead holds the
highest supported CPU speed (240 or 160 MHz) to prioritize switching speed.
SenseCAP Indicator retains its existing policy. A fixed speed stays fixed when
power saving or radio2 changes.
The selection applies immediately; a failed apply or save reports an error and
attempts to restore the previous selection. Returning to Auto clears the saved
override. Existing preferences load as Auto.

A damaged CPU override falls back to Auto without discarding other valid
preferences. An identifiable truncated timezone record is rejected as an
incomplete preferences image, so it cannot silently replace a fractional
timezone with an integer value.

Local USB/Bluetooth console commands provide temporary controls for controlled
radio2 experiments. They are held in RAM and reset at reboot:

| Command | Values | Default |
| --- | --- | --- |
| `set powersaving.test sleep VALUE` | `on`, `off` | `on` |
| `set powersaving.test slice_ms VALUE` | 2-20 ms | 10 ms |
| `set powersaving.test guard_ms VALUE` | 0-20 ms | 4 ms |
| `set powersaving.test min_ms VALUE` | 1-10 ms | 2 ms |
| `set powersaving.test settle_ms VALUE` | `auto`, 1-50 ms | `auto` |
| `set powersaving.test uart_quiet VALUE` | 5-300 seconds, `off` | inactive |

`get powersaving.test` shows the current controls; `set powersaving.test reset`
restores their defaults. Sleep on permits existing power-saving decisions; the
saved device power-saving setting and every normal transport, interrupt and
radio deadline guard still apply. Settle changes only when profile sleep is
allowed, leaving radio calibration timing unchanged.

UART quiet is available only for an ordinary hardware UART console, such as
the V3's external USB bridge. After draining its acknowledgement, it stops the
UART driver and parks its Companion session, preventing new push notifications
to the stopped UART from holding the CPU awake. Received messages remain in
the offline inbox; other transports keep their own sessions. It automatically
restores the console at 115200 baud with a fresh, silent protocol session after
the chosen interval. USB logging and OTA must be off. Native USB boards reject
this command. A reboot also restores the console.

To separate clock speed from CPU sleep, hold the clock at 80 or 160 MHz and
compare sleep off/on at the same RF settings. Use matching transmitter and
receiver preambles, unique packets without retries, and diagnostic counters
that prove SDK sleep occurred. Keep every failed packet in the result. These
controls are experimental tools; a reported SDK wait is not a current reading
or proof of a loss-free radio link.

## Noise-floor sampling and sleep

Startup still collects a complete baseline promptly: 64 accepted samples,
at least 50 ms apart, after the radio frontend has settled. Collection keeps
the radio in continuous RX when needed, but the CPU may sleep between reads.
Pending packet interrupts retain their normal receive owner. Rejected readings
retry at 50 ms intervals, and the existing 30-second collection timeout bounds
an attempt on a busy channel.

After a successful baseline, ordinary single-profile refresh waits five
minutes. A rejected rise or failed attempt retains the last usable floor and
retries after one minute. Gain/tuning changes and radio recovery still request
immediate recalibration. Adaptive AGC maintenance with an unchanged RF path
retains a valid baseline and its existing refresh deadline; it does not cancel
a pending collection or postpone a failed-sample retry. Legacy fixed-cadence
AGC resets still request recalibration.
Dual-profile scanning keeps its existing timing and calibration policy.

ESP32 timed sleep uses a millisecond timer capped at the next sample deadline.
Repeater, Room and Sensor also preserve millisecond queue, retry, RS232 standby,
scheduled-radio and maintenance deadlines, so the last fraction of a second
does not become a busy-wait tail. A future sub-second deadline is not reported
as blocked work; a deadline that is already due still prevents sleep.
nRF52, RP2040 and STM32 retain their interrupt-idle wake paths. Other sleep
guards, including USB/logging, packet queues and bridges, still apply.
These changes have host scheduling tests and firmware build coverage; their
power benefit requires a new matched hardware/current measurement.

See the [CLI reference](cli_commands.md#diagnose-what-prevents-power-saving)
for all page names and commands.

The [nRF52 idle-sleep investigation](nrf52_sleep_event_validation.md) describes
an eligible-but-not-sleeping regression, its fix, controlled hardware evidence
and the limits of CPU-state sampling without a current meter.

## RAK13302 builds with an unused onboard radio

The RAK3401/external RAK13302 firmware now probes the private onboard-radio
bus once during startup. This supports both a RAK3401 core (no onboard SX1262)
and a RAK4631 core using the external RAK13302. It is adapted from
[agessaman's optional-radio sleep change](https://github.com/agessaman/MeshCore/tree/feat/rak13302-auto-onboard-sleep).
[RAK's internal connection table](https://docs.rakwireless.com/product-categories/wisblock/rak4631/datasheet/#internal-connections-between-nrf52840-mcu-and-sx1262-lora-transceiver)
identifies the private SPI, reset, BUSY and RF-switch pins used by the probe.

A matching six-byte chip signature triggers cold sleep with the RTC disabled.
The RF switch stays off, NSS/reset stay high, and the temporary SPI/data/BUSY
pads are released. Sleep confirmation reads BUSY only: another SPI transaction
would wake the chip. Each BUSY wait is bounded to 20 ms, including when no chip
is fitted. A timeout or mismatched signature does not block startup of the
external radio. Debug-enabled startup logging reports `asleep`, `absent`,
`busy-timeout` or `sleep-failed` when the USB debug output is available; a
missing chip normally produces `busy-timeout`, not a verified sleep result.

This happens before the boot-voltage check, so a low-voltage shutdown cannot
skip the unused-radio sleep attempt. It does not cycle the shared GPS/storage
rail, change external-radio recovery, or enable its FEM before initialization
succeeds. There is no runtime probing or automatic primary-radio selection.

Ordinary **RAK4631 firmware is unchanged**: its onboard SX1262 is the active
primary radio and must not be disabled. Use the external RAK13302 image for
that hardware arrangement, not a normal RAK4631 image. This startup change has
host regression tests; sleep-current improvement still needs matched current
measurements on the physical core/module combination.

## Board regulator initialization

### User-selectable regulators and crash recovery

`mcu.regulator` and `radio.regulator` independently select `auto`, `dcdc` or
`ldo` where the board supports those modes. `auto` preserves its existing board
default. The choice is saved and takes effect after `reboot`; the two getters
show requested/active mode, fallback, crash count and recovery availability.
An explicit setter clears the corresponding fallback and streak, even when the
same value is saved again. See the [CLI commands](cli_commands.md#select-mcu-and-lora-regulator-modes).

Processor selection applies to the existing, circuit-supported nRF52 REG1
DC-DC path. It does not select REG0, change an external PMIC, or expose an
ESP32/RP2040 CPU regulator control. Radio controls apply to the supported
SX1262, LR1110/LR1121 and Meshnology W12 LR2021 implementations. DC-DC is offered
only when that board's regulator circuit is supported. LDO-only boards reject
`dcdc`; unverified LR2021 switching circuits keep their existing mode.

Integrated STM32WL targets retain their existing supply configuration. Their
MCU and radio share the regulator, so independent selections need a coordinated
implementation and board circuit verification before they can be offered.

Automatic recovery uses reset-retained RAM on supported platforms. A domain
must have been armed in DC-DC mode before the crash, with a valid last battery
sample of at least **50%**, no more than **60 seconds** old at its last
heartbeat. The **third consecutive qualifying crash** selects LDO before that
domain starts again and saves the fallback. MCU and radio streaks are separate;
if both were using DC-DC during the same qualifying failures, both can fall
back. This is a recovery action, not proof that a regulator caused the crashes.

Low/unknown battery, stale samples, cold starts, intentional reboots, sleep
wakes and unclassified resets break a streak. Corrupt retained state cannot
add a crash. **Five healthy minutes** clear the running streak; a later crash
starts a new one. The saved LDO fallback survives normal resets and power
cycles. It stays in LDO until a user explicitly selects a mode again, with no
automatic return to DC-DC. Crash bookkeeping stays in retained RAM rather than
writing flash at every heartbeat.

During timed Morse TX/RX, periodic battery sampling and failed-save retries
wait until timing ends. Recovery heartbeats continue, and a deferred sample
keeps its original timestamp, so it can become too stale to qualify a crash.
Overdue sampling and retries resume on the next eligible service tick.

Battery eligibility follows the board's battery measurement and configured
percentage curve. USB supply voltage alone does not establish that a physical
battery is at 50%; validate battery-only starts and the board's absent-battery
reading when qualifying recovery. Boards without a trustworthy battery reading
should report it invalid. A getter reporting recovery unavailable still permits
supported manual mode selection.

ESP32 WebConfig exposes supported controls under **Radio → Advanced → Power
regulators**, showing active mode and fallback status. Unsupported domains and
DC-DC options are hidden. Saving reboots only after the authenticated save
completes successfully. **Clear fallback on restart** lets a user retry the
current saved mode without first choosing a different one.

For hardware qualification, compare both modes with battery-only cold starts,
normal reboots, sleep/wake, BLE/WiFi and radio TX/RX under load. Confirm that
three injected qualifying watchdog/fault resets select LDO, that fewer than
three and low/unknown-battery resets do not, and that an explicit setter restores
the requested choice. Also check current consumption and voltage during TX;
native recovery tests and successful firmware builds do not establish these
physical results.

The [regulator qualification record](test-results/regulator_recovery_2026-10-18.md)
binds the compiled packages, software checks and Mercerwood bench results to
their tested source revisions and lists the remaining physical checks.

### Existing board defaults

RAK4631 Companion shares the nRF52840 REG1 regulator path with the other
RAK4631 roles. `auto` preserves its DC-DC default; the former Companion-only
force-LDO flag has been removed. Saved mode selection is deferred until board
ADC and primary filesystem initialization are complete, before display, radio
and wireless startup. This retains boot-voltage protection and keeps REG0 and
storage layout unchanged. The shared initializer uses the SoftDevice API when
BLE's stack is already active and the hardware register otherwise.

[RAK documents DC/DC support](https://docs.rakwireless.com/product-categories/wisduo/rak4630-module/quickstart/)
for the module. The related upstream [battery-startup report #1878](https://github.com/meshcore-dev/MeshCore/issues/1878)
concerned Repeater firmware, with BLE Companion reported working on an affected
board. It did not establish filesystem corruption or a Companion-specific
DC/DC failure. [PR #1914](https://github.com/meshcore-dev/MeshCore/pull/1914)
also changes boot-voltage/wakeup handling and is not proof that every failure
in that thread has the same cause.

Host startup tests cover both SoftDevice states and deferred regulator selection
with boot-voltage protection preserved. Firmware compilation is not hardware qualification:
compare battery-only cold starts, low-voltage recovery, BLE operation and
settings writes/reboots on the actual board/baseboard revisions before a fleet
rollout. USB power can mask the reported startup symptoms. A current meter is
still needed to quantify the power difference.

The following boards also now call their declared `NRF52BoardDCDC` initializer
instead of bypassing it with `NRF52Board::begin()`. This applies to every role
that uses the board class, not just Companion, and does not require enabling
the runtime sleep policy. The nRF52840 hardware manages the enabled regulator
according to load. This is REG1 only, not a blanket change to REG0 or an
external LDO, PMIC output, or radio regulator.

| Board | Circuit evidence used for REG1 support |
| --- | --- |
| Heltec T1 | [T1 V1.0 schematic](https://resource.heltec.cn/download/Mesh_Node_T1/schematic/Mesh_Node_T1_V1.0.pdf), DCC-to-DEC4 inductors and capacitors. |
| Heltec MeshPocket | [MeshPocket schematic](https://resource.heltec.cn/download/MeshPocket/schematic/MeshPocket_nRF52840%2BSX1262.pdf), DCC-to-DEC4 L1/L2 and C5/C6. |
| Heltec MeshSolar and MeshTower V2 | [HT-N5262M module circuit](https://resource.heltec.cn/download/MeshTower-V2/schematic/HT-N5262M_Schematic_Diagram.pdf), L1/L2 and C1/C2. Heltec links this circuit from its [MeshTower V2 resources](https://heltec.org/project/meshtower/); [MeshSolar's datasheet](https://resource.heltec.cn/download/MeshSolar/datasheet/MeshSolar_V1.0.0.pdf) identifies the module. |
| LilyGO T-Echo | [T-Echo schematic](https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Echo/main/schematic/T-Echo_Schematic.pdf), REG1 DC/DC network and explicit DC/DC-enabled design note. |
| Ikoka Handheld, Nano and Stick nRF52 | [XIAO nRF52840 circuit](https://files.seeedstudio.com/wiki/XIAO-BLE/Seeed_Studio_XIAO_nRF52840_PDF.pdf), DCC L1 and DEC4 capacitors. The designers identify XIAO as the MCU for [Handheld](https://github.com/ndoo/ikoka-handheld-meshtastic-device), [Nano](https://ndoo.sg/projects:amateur_radio:meshtastic:diy_devices:ikoka_nano) and [Stick](https://github.com/ndoo/ikoka-stick-meshtastic-device). |
| Minew ME25LS01 | [Manufacturer datasheet](https://store.minewsemi.com/wp-content/uploads/2024/03/ME25LS01_LR1110_Datasheet_K_EN.pdf), electrical schematic page 9: MCU DCC through ML2/ML3 to DEC4_6 and MC15/MC16. |
| Nano G2 Ultra | [Manufacturer schematic published as the 19 June 2023 design](https://wiki.uniteng.com/meshtastic/nano_g2_ultra/nanog2ultra_19_jun_2023.pdf), NRF52840 sheet: DCC through L13 (10 uH) and L12 (15 nH) to DEC4, with C41/C42. These components are not marked DNM. |

The startup tests execute each real `begin()` with SoftDevice off/on and power
management compiled out/in. Normal startup defers the selected mode until the
saved settings and recovery state can be read, while retaining one common board
initialization. The board class still defines the circuit-supported default;
the settings layer applies it before display/radio startup.

Nano G2 Ultra was deliberately excluded from upstream
[PR #1246](https://github.com/meshcore-dev/MeshCore/pull/1246) because its LC
circuit had not yet been confirmed. That discussion asks for circuit
confirmation or testing; it does not identify a Nano-specific DC-DC failure.
The published schematic supplies that circuit evidence. Nano now approves the
REG1 DC-DC default through the shared board initializer, retaining saved LDO
selection and crash fallback in the main firmware roles. KISS applies the
board default directly. REG0, GPIO assignments, ADC conversion, peripheral
startup/shutdown and USB/DFU identity are unchanged. The schematic title block
does not identify a PCB revision; battery-only starts, current draw and radio
load on actual hardware remain physical acceptance checks.

The [Nano validation record](test-results/nano_g2_regulator_2026-10-18.json)
binds the issue/circuit review, native checks and six passing build selections
to source `c6ccbb5ab`: BLE/USB/Full Companion, Repeater, Room Server and KISS.
No Nano hardware was flashed during these checks.

ThinkNode M3/M4/M6 and generic ProMicro remain unchanged pending exact circuit
or physical-board confirmation; their DC/DC inheritance alone is not evidence.
ThinkNode M1/M8 and Keepteen LT1 retain their plain `NRF52Board`
initializers for the same reason. The MeshTracker X1's LR2021 regulator likewise
remains unchanged. Do not turn on a switching regulator without its required
inductor/capacitor network. The MCU and LoRa regulator choices are independent.

## Meshnology W12 transmit preparation

The W12 uses the RF-switch table from
[upstream PR #3579](https://github.com/meshcore-dev/MeshCore/pull/3579), head
`7457cff43c22697119e06ad7d192a37de0990403`. Low-band standby keeps GC1109 CTX,
CPS and CSD high; LF receive keeps CTX low and CPS/CSD high. This replaces the earlier
1 ms temporary prearm as the fix for the weak opening of a transmitted packet.
The PR author reported a roughly 36 ms power step before the table change and
no idle-current increase in their power-saving setup. Those measurements are
upstream evidence, not measurements of this fork.

The 1 ms pre-TX settle guard and TX failure recovery remain. With the warm
W12 table they do not rewrite the standby masks or remove its retained bias.
Other LR2021 tables can still opt into temporary prearming with full cleanup.
The upstream table needs one adaptation for this fork's exclusive FEM rails.
The [GC1109 datasheet](https://www.geochipinc.com/uploads/23940615_1756826539.pdf)
requires controls not to exceed VBAT. Before changing supplies, firmware
checks a cold standby with all control outputs low; after the selected supply
settles for 1 ms it restores warm standby only for LF. High-band standby and
2.4 GHz RX/TX keep the GC1109 controls low. A failed routing change is retried
on the next request, including one for the same band.

Packet-wake deep sleep retains only the selected FEM rail. Terminal shutdown
checks cold controls after stopping the radio, then retains both rails off.
If that final SPI check fails, the transceiver is held in reset before its FEM
supplies are removed. Boot asserts reset before dropping retained FEM rails,
including after an MCU-only reset, and releases it after selecting the initial
supply. Hard recovery pulses reset before checked routing so a prior SPI fault
cannot prevent recovery. The W12's existing circuit-verified DC/DC setting is independent of this routing.

Native fault-injection tests cover the actual W12 table, temporary-mask
cleanup on a cold-standby table, and power retention. RF envelope and battery
current measurements on this fork's physical W12 remain hardware checks.
