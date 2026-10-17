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
On nRF52, settled long visits can idle the MCU with power saving enabled;
the RF receiver stays on. See [radio profiles](radio_profiles.md#mcu-power-saving-while-scanning).

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

## Board regulator initialization

RAK4631 Companion uses the same nRF52840 REG1 DC/DC initialization as the other
RAK4631 roles. The former Companion-only force-LDO flag has been removed. This
does not change REG0, the SX1262 regulator, storage layout, or boot-voltage
protection. The shared initializer uses the SoftDevice API when BLE's stack is
already active and the hardware register otherwise; Companion starts the board
before it starts Bluetooth.

[RAK documents DC/DC support](https://docs.rakwireless.com/product-categories/wisduo/rak4630-module/quickstart/)
for the module. The related upstream [battery-startup report #1878](https://github.com/meshcore-dev/MeshCore/issues/1878)
concerned Repeater firmware, with BLE Companion reported working on an affected
board. It did not establish filesystem corruption or a Companion-specific
DC/DC failure. [PR #1914](https://github.com/meshcore-dev/MeshCore/pull/1914)
also changes boot-voltage/wakeup handling and is not proof that every failure
in that thread has the same cause.

Host startup tests cover both SoftDevice states and regulator selection before
the boot-voltage check. Firmware compilation is not hardware qualification:
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

The startup tests execute each real `begin()` with SoftDevice off/on and power
management compiled out/in. They require DC/DC before peripheral initialization
and before any enabled boot-voltage check, while retaining one common board
initialization. The previous bypass fails these tests.

ThinkNode M3/M4/M6 and generic ProMicro remain unchanged pending exact circuit
or physical-board confirmation; their DC/DC inheritance alone is not evidence.
Nano G2 Ultra, ThinkNode M1/M8 and Keepteen LT1 retain their plain `NRF52Board`
initializers for the same reason. The MeshTracker X1's LR2021 regulator likewise
remains unchanged. Do not turn on a switching regulator without its required
inductor/capacitor network. The MCU and LoRa regulator choices are independent.

## Meshnology W12 transmit preparation

The W12 adopts the GC1109 TX preparation from
[upstream PR #3579](https://github.com/meshcore-dev/MeshCore/pull/3579), but keeps
the PA shut down in ordinary standby rather than permanently selecting TX mode.
Before sub-GHz packet or Morse CW transmission, it temporarily enables the LF
PA in standby, waits 1 ms, starts TX, then restores the normal standby mask.
The TX mask stays unchanged during that restoration. Failure paths restore all
affected DIOs and request radio recovery if the PA state cannot be confirmed.
The 2.4 GHz path and other LR2021 boards do not use this LF prearm policy.

LF/HF FEM rail changes now settle for 1 ms instead of 100 ms. Packet-wake deep
sleep retains only the selected FEM rail; terminal shutdown retains both rails
off. This is separate from the W12's existing circuit-verified DC/DC setting.

Native fault-injection tests cover the mask transitions and power retention;
RF envelope and battery-current measurements on a physical W12 are still needed
to validate the TX improvement and quantify power use.
