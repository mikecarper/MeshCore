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
