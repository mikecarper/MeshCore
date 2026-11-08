<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/radio_agc/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Old AGC reset and new receive refresh

The new periodic method briefly sleeps and wakes the radio, then resumes
reception. The old method also runs radio calibration. Both keep the existing
`agc` command name for compatibility. Neither is a crystal frequency-trim
adjustment or an MCU reboot.

## What changes on Heltec V4 / SX126x

| | Old full receiver reset | New receive refresh |
| --- | --- | --- |
| Sequence | Warm sleep, RC standby, `Calibrate(0x7F)`, image calibration for the operating frequency, restore RX settings, resume RX. | Stage same-channel duty-cycle RX, allow a natural sleep/wake cycle, stop duty cycling and verify continuous RX reentry. |
| Calibration | Recalibrates the radio's internal blocks, including ADC, PLL and image rejection. Restores configured RF-switch/gain settings and applicable register patch. | Does not request full or image calibration or change the configured frequency or gain. |
| Noise-floor estimate | The independent legacy reset requests fresh samples, retaining the previously valid estimate while collecting. | Keeps the estimate and collection state; applies the normal RSSI settling delay after RX reentry. |
| Execution | Synchronous command sequence, including a 5 ms delay and bounded BUSY polling. The legacy helper continues after command errors. | Asynchronous: the main loop advances the operation, checks results and preserves packet ownership. Failed or unconfirmed cycles receive no success credit. |
| Current control | Saved `agc.old auto`: included after a successful primary refresh at intervals of 240 seconds or longer, where supported. `off`: disabled. A positive value: independent timer instead. | Primary saved `agc` / `agc.reset.interval` timer on supported builds. |

The sleep/wake cycle affects the radio's receiver circuitry and oscillator;
it is more than restarting only the crystal. **Receive refresh** describes
what the new code verifies. Calling it an **AGC reset** does not establish
that the internal automatic gain-control state was reset or that sensitivity
improved. See the [Semtech SX1261/2 datasheet, sections 9.3 and 13.1.7](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf)
for the underlying sleep and duty-cycle modes.

The old method remains available for comparison and existing recovery paths.
Its "full reset" name refers to the receiver maintenance sequence above, not
asserting the radio's hardware reset pin. Hard radio recovery is separate.

## Automatic selection

With the saved default `agc.old auto`, the primary interval selects the method
on receive-refresh builds:

- **0:** primary maintenance off.
- **Below 240 seconds:** new primary receive refresh, without the scheduled
  old-reset half.
- **240 seconds (4 minutes) or longer:** new receive refresh, followed by the old
  full receiver reset when that implementation is available in the build.

For example, `set agc 236` uses the new method; `set agc 240` uses both on V4.
The cutoff uses the configured interval, not time spent waiting for an idle
radio. A late or deferred short-interval refresh does not enable that scheduled
old half.

The cutoff controls the optional old-reset policy, not calibration needed for
recovery. Restoring an LR2021 PRAM scanner after a primary sleep/wake may require
a cold radio reset and full base/frontend calibration even below 240 seconds.
That calibration belongs to primary restoration; it does not satisfy the
automatic old half at 240 seconds or longer. W12 hardware testing verified
three short primary refreshes and both halves of a real 240-second cycle,
including a separate old-helper increment after primary cold restoration.

The combined operation first requires a witnessed refresh and checked RX
reentry. Its old-reset half then waits for an idle primary visit, without
repeating the pulse or interrupting a packet. Changing or disabling the interval
discards an outstanding completion. The next interval starts after both halves
complete. Most legacy helpers report admission rather than checked success of
every calibration command; the LR2021 periodic path checks command and restore
results. Failed or unconfirmed pulses do not trigger the
automatic old reset.

LR2021 also supports the old-reset policy. Its checked native sequence uses RC
standby, `Calibrate(0x6F)` and forced frontend calibration at the actual programmed
frequency and all enabled profile frequencies. Ordinary single-profile and
native RXPS operation include retained sleep/wake. A known-resident PRAM/LDO
scanner session omits that sleep/wake step while still performing full
calibration in checked RC standby. The scanner is warm-paused and its restart
must be verified. A resident image must not be rewritten while active; an
actual hard reset invalidates its loaded marker. Descriptor words alone do
not verify a working scanner. The wrapper restores modem, protocol, power,
gain and receive mode,
including multi-SF or RXPS. Noise-floor refresh stays queued for the next host
sampling window while PRAM owns reception. A busy receiver or pending packet defers
the operation; failed calibration/restoration gets no success credit and waits
the normal reset interval before another attempt. Device faults remain visible
through the existing LR2021 radio-apply diagnostics.

The command mask, frontend frequency encoding and retained-sleep behavior follow
[Semtech's LR20xx driver](https://github.com/Lora-net/usp/tree/main/smtc_rac_lib/radio_drivers/lr20xx_driver).

This LR2021 implementation has host command/fault/ownership tests and build
checks. The [W12 hardware test](test-results/lr2021_legacy_agc_2026-11-08.md)
passed single-profile legacy calibration (12/12 RF), native RXPS (12/12 incoming,
1/1 outgoing), single-profile multi-SF (8/8), and nine quiet dual-profile/multi-SF
calibrations followed by 18/18 RF probes with maintenance disabled. The final
candidate also completed three independent old calibrations, three short
primary cold-restoration cycles and a real four-minute primary-plus-old cycle,
with verified scanner return and no reported failure or unplanned recovery. Its four
old-helper calls took 26.301-26.357 ms, excluding scanner pause/rehook. A
concurrent-traffic scanner handoff limitation remains unresolved; these tests
do not establish sensitivity, RF downtime or lossless traffic handling.
Builds that omit legacy support with `MESH_ENABLE_OLD_AGC=0`, including the
default compact STM32 builds, retain the new method alone. Drivers without
receive-refresh support keep their existing method.

## Settings

Read the current settings before changing them:

```text
get agc
get agc.old
get agc.status
```

Use the new primary method every 16 seconds, with the independent old timer off:

```text
set agc.old 0
set agc 16
```

For an old-method-only comparison at the same 16-second interval:

```text
set agc 0
set agc.old 16
```

Both settings survive reboot. Restore the recorded values after a comparison.
`agc.old` accepts these saved policies:

- `set agc.old auto`: default; add the old reset after a successful primary
  refresh when `agc` is at least 240 seconds. No independent old timer.
- `set agc.old off` or `set agc.old 0`: disable the old timer **and** the automatic
  old half, including when `agc` is 240 seconds or longer. Recovery watchdogs
  remain independent of these periodic-maintenance controls.
- `set agc.old 1` through `set agc.old 86400`: run an independent old timer at
  that interval in seconds, replacing the automatic old half. The primary timer
  continues using the new method on its own schedule.

For only the old reset every **4 seconds**, including after reboot:

```text
set agc 0
set agc.old 4
```

For only the new method every four minutes:

```text
set agc.old off
set agc 240
```

New configurations default to a 16-second primary interval. Existing saved
primary values survive upgrades. Primary intervals use four-second steps;
device `powersaving` can extend the actual interval adaptively. The independent
old timer has no adaptive backoff. Compact builds can omit old-reset support
and return `Error: unsupported`.

The old policy is saved separately from existing application preferences. A
missing record uses `auto`; an unreadable or corrupt record disables old resets
for that boot. A failed save reports an error and attempts to restore the previous
runtime setting; it does not claim the setting survived reboot.

Active duty-cycle `radio.rxps` suspends the primary timer because the radio is
already sleeping and waking. Continuous-RX fallback still permits maintenance.
This suspension includes the automatic combined operation.
The independent old timer is not automatically suspended. With radio2 enabled,
the new method waits for an idle primary visit and temporarily extends that
visit; packets on the secondary profile can be missed during that time.

For full ranges, diagnostics and scheduling details, see
[AGC CLI settings](cli_commands.md#view-or-change-the-agc-reset-interval).
`get agc.status` and the pulse timing counters describe the receive-refresh
half; they do not measure the subsequent full calibration.

## Timing and V4 test evidence

The V4's default compensated sleep request is **3116 microseconds** with a
1600-microsecond TCXO startup allowance. This encodes 33 sleep-timer ticks
after transition compensation. It is not the duration of the whole operation
or a measured RF outage.

In the [November 5 V4 test](test-results/heltec_v4_rxps_half_ms_margin_2026-11-05.md),
sampled BUSY-high spans were 3.041-3.082 ms. Whole operations took about
126-128 ms, including about 120 ms of initial RX listening. Listening time is
not dead time. Timing depends on radio settings and packet activity.
Those measurements cover the new refresh alone, not the combined operation.
The four-minute selection rule is covered by host regression tests for the
boundary, ownership deferrals, configuration changes and unsupported backends;
it has not been timed on hardware as a combined sequence.

The tests verified natural wakes, RTC cleanup, RX reentry and subsequent packet
reception. They did **not** measure internal AGC state, calibrated sensitivity,
or battery savings. The uncontrolled antenna comparison did not establish the
cause of the reported V4 sensitivity/noise-floor difference or prove this
refresh fixes it. Displayed noise-floor differences alone did not predict
which receiver decoded more packets.

## Other radio families

Supported SX126x, LR1110, LR1121 and LR2021 drivers use their chip's duty-cycle
mechanism. SX1276 instead uses a checked, host-timed sleep/wake sequence.
The old reset's exact calibration sequence is driver-specific; the table above
describes SX126x. Other drivers retain their existing maintenance behavior.
Building with `MESH_WARM_RX_REFRESH=0` selects the previous primary method;
it is not a runtime CLI setting.

Implementation: [SX126x legacy reset](https://github.com/mikecarper/MeshCore/blob/keymindCascade/src/helpers/radiolib/SX126xReset.h),
[SX126x receive refresh](https://github.com/mikecarper/MeshCore/blob/keymindCascade/src/helpers/radiolib/SX126xPulseWrapper.h),
[shared scheduling and recovery](https://github.com/mikecarper/MeshCore/blob/keymindCascade/src/helpers/radiolib/RadioLibWrappers.cpp).
