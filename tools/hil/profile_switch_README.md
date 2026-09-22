# Production SX1262 profile-switch timing

This experiment links the actual `RadioLibWrapper::tuneProfile()` and
`CustomSX1262Wrapper` implementations. It does not start MeshCore networking,
load an identity, write preferences, or initialize a filesystem. Timing runs
are RX-only; explicit host-requested packet probes transmit at -9 to 0 dBm.
It is a timing harness, not a normal node firmware.

The `profile_switch_xiao` wiring matches `variants/xiao_s3_wio/platformio.ini`.
`profile_switch_indicator` uses the SenseCAP Indicator's ESP32 LoRa processor
and its production GPIO-expander HAL, not the Indicator's RP2040 USB processor.
Use only matching hardware. Back up its flash
before uploading; upload can replace the bootloader, partition map, and app.
Restore the original image after testing unless the user asks to leave HIL
firmware installed; in that case reboot to idle HIL and retain the backup.
Do not put raw flash backups in Git:
they may contain identity, channel, Wi-Fi, and other secrets.

The current XIAO HIL build explicitly disables boosted RX gain, unlike its
normal production variant. `info.rx_gain_reg` and scan status must read 148
(0x94); Indicator remains boosted, 150 (0x96). Stationary and channel-scan
collectors default to `--expect-rx-gain normal`; specify `boosted` when using
the Indicator as receiver. This is SX1262 RX gain, not a change to an external
RF-switch/LNA pin policy.

Enable the environment using an ignored `platformio.local.ini`:

```ini
[platformio]
extra_configs =
  variants/*/platformio.ini
  tools/hil/profile_switch.ini
```

Do not overwrite another local overlay. Merge its extra configs if one exists.
Run only one PlatformIO command at a time in this checkout:

```text
pio run -e profile_switch_xiao
pio run -e profile_switch_xiao -t upload --upload-port COM31
python tools/hil/profile_switch.py --port COM31 --output results.json --count 1000 --rounds 2 --experiment batching
```

Select the actual USB port, not necessarily COM31. The collector refuses an
existing output file or firmware without the expected benchmark identity.
The current firmware identity is `production-profile-switch-v8`. `batching`
keeps XOSC warm in both cases and compares the original separate SF/BW/CR
setters against the production batched method, in the same executable.
Use `--experiment warm` to repeat the earlier RC/XOSC comparison with batching
disabled in both cases. V1 raw results remain historical measurements of the
unbatched path; the V2 baseline reproduces its configuration sequence.
V3/V4 broad-screening results are superseded: their HIL override bypassed
MeshCore's extra preamble-detection IRQ enable. V5 preserves it in the normal
and experimental RX paths. Do not combine timings from different binaries
into a matched A/B comparison; executable layout also affects small timings.

## Measurement boundary

- Primary: 909.5 MHz, SF7 / 62.5 kHz, CR 4/5.
- Secondary: 910.5 MHz, SF7, SF8, or SF9 / 500 kHz, CR 4/5.
- Same executable and hardware in both cases. Each trial enters production
  dual-profile mode. In the warm experiment, the RC baseline changes only
  `standbyXOSC` to false. In the batching experiment, both cases keep XOSC warm;
  a test subclass selects the pre-batching `applyParams` implementation or the
  current production implementation. Those two experiments do not alter packet guards or RX.
  All protection checks, frequency/modulation/preamble setters, and RX startup
  run through production code. The separate V5 sweeps below deliberately
  substitute an experimental RX sequence while preserving the wrapper's guards.
- Hops are requested directly through a small subclass exposing `tuneProfile`.
  The normal receive-visit length is used between hops, but application/UI/network
  scheduling and cooperative scan-loop latency are outside the timed region.
- `api`: entry to return of `tuneProfile`.
- `busy_tail`: time from return until BUSY is observed low (20 ms timeout).
- `total`: entry to BUSY-low observation. This includes timer/polling overhead.
- RX mode is checked through GetStatus after timing. This is not an RF packet
  acquisition or packet-delivery measurement and does not measure analog settling
  beyond what BUSY and RX status report.
- V2 also checks the RadioLib SF/BW/CR cache against the active profile after
  every successful hop; a mismatch fails the run.
- First eight successful retunes of every trial are excluded from steady-state
  statistics. Packet/busy deferrals are counted separately, not timed as hops.
- Every trial disables the second profile and checks that the production hook
  restores RC standby. The second round reverses cold/warm order.

These frequencies do not exercise image recalibration for a large frequency
jump. Other boards, especially ones with I/O expanders, require separate tests.
The production 6 ms switch budget and 4 ms loop budget remain unchanged.
The Indicator still exceeds 6 ms; do not shorten common preambles based on
the XIAO result. Timing the wrapper excludes the full application's scheduling.

## Production implementation and recovery (V8)

```text
python tools/hil/profile_switch_sweep.py --port COM31 --output production.json --group production --count 256 --rounds 2
python tools/hil/profile_switch_recovery.py --port COM31 --output recovery.json
python tools/hil/profile_switch_packets.py --receiver COM31 --sender COM28 --output packets.json --power -9 --rounds 2
```

The default `production` group compares batching/2 MHz, fast RX/2 MHz,
buffered SPI/8 MHz and their combination. Bit 512 selects the actual production
fast state machine; low mask bits remain zero. A hardware success counter must
advance on fast trials and packet readiness, and remain unchanged on controls.
Owned fast RX, `SetFs`, and skipping unchanged, acknowledged RX packet tuples
are defaults for `CustomSX1262` boards with direct radio I/O. The Seeed
Indicator deliberately retains its earlier fast path without the two new
shortcuts: its radio control and BUSY/IRQ pins run through an I2C expander.
XIAO S3 WIO, Station G3 KISS, Heltec V4 KISS, and the Indicator retain their
existing buffered 8 MHz SPI choices; this change does not raise SPI speed on
other boards. The new operations were checked over the air on a T096 SX1262
fixture, not separately qualified for every board now using them.

Fast RX reuses IRQ mapping and buffer bases only after successful continuous
LoRa RX, within an owned warm retune. It retains stale-IRQ clearing, RF-switch
control, SetRx and BUSY waits. Packet-parameter/IQ programming remains on the
Indicator and on any changed or untrusted packet tuple. TX, CAD, ordinary
standby, sleep, reset, duty cycling and failed commands invalidate reuse. A
failed RX resume rolls back the old tuple/profile through full setup.

The recovery collector exercises 20 USB reopen cycles, deliberately lost
timing/TX replies, stale TX acknowledgements, and 18 radio lifecycle cases: TX, CAD, retained
sleep, reset/init, RC-policy sleep and explicitly reconfigured-TCXO sleep,
three repetitions each. It sends five 16-byte -9 dBm probes per board, then
reboots the HIL image. The direct-chip lifecycle leaves wrapper state invalid
and requires that reboot before further packet/timing commands.

Sleep uses standby entry and restores the configured TCXO voltage/delay on
wake. This fixed a sleep-only XOSC flag seen with both warm and RC policies;
unrelated device errors are not cleared by that recovery. Cold starts keep
the configured 1,600 us TCXO delay. This is not an oscillator-delay bypass.

## Redundant setup and SPI sweeps (HIL only)

```text
python tools/hil/profile_switch_sweep.py --port COM31 --output rx.json --group rx --count 128 --rounds 2
python tools/hil/profile_switch_sweep.py --port COM31 --output bulk.json --group bulk --count 128 --rounds 2
python tools/hil/profile_switch_packets.py --receiver COM31 --sender COM28 --output packets.json --power 0
```

`rx` tests 15 cases across SF7/8/9 and both directions: unchanged batching,
manual full RX setup control, individual omission of repeat standby, IRQ setup,
buffer setup, packet setup, modem query, deferred preamble programming, awake
NOP omission, combinations, and 2/4/8 MHz SPI. `bulk` compares whole-buffer
ESP32 `transferBytes` against RadioLib's per-byte loop, alone and combined.
Masks are documented in `profile_switch_experiments.h`; bit 256 enables bulk SPI.

No BUSY wait, stale-IRQ clear, RX command, or wrapper packet-ownership check is
removed. Omissions apply only inside an explicitly identified RX-to-RX hop
after standby and previous successful RX setup. These legacy masks are not a
complete cache-invalidation policy after TX, CAD, sleep, reset or recovery;
normal firmware uses the separately tested production state machine above.

Packet checks use known sequence-tagged 16/64/255-byte payloads at both profile
settings, after ensuring the opposite profile and performing a real guarded
retune to the requested profile. They check length, bytes, and receive
profile; report RSSI/SNR and RX errors; retain failures rather than retrying them
away. `--smoke` tests just baseline and fastest candidate with 16-byte payloads.
Use only authorized test frequencies, connected antennas and appropriate power.
This is short-range functionality testing, not sensitivity, PER, continuous
scan-overlap, TX/CAD lifecycle, application scheduling or power qualification.

The host reader accumulates partial serial reads until newline. A 250 ms port
timeout is not the end of a JSON record (safe profile exit can take longer).
Native Espressif USB sessions (VID 303A) assert DTR and keep RTS low. CH340
bridge sessions keep both low; DTR can drive BOOT/GPIO0 on that hardware.
Native HIL TX uses a 4096-byte buffer and 250 ms write timeout. Packet results
and complete timing reports are cached before emission and flushed. After a
missing reply, the host sends `result sent|received|listening|result <sequence>`
to replay the result, not the radio
operation. Sequence-correlated reads retain/skip late replies; missing TX
acknowledgements never cause a retransmission. Payload validity is reported
separately from complete command acknowledgements. These are HIL USB fixes,
not changes to the public MeshCore Companion protocol. V8 timing commands
append a request sequence; collectors choose randomized session starts to
avoid accepting cached results from a previous host session.

## SF6/125 kHz channel-count limit

The current collector additionally accepts `--sf 5` through `--sf 10` and
`--bw-khz 125|250`, requiring `channel_bw_select: 1` from both boards.
Defaults remain SF6/125. The SF and bandwidth are checked on RX setup, every
retune, and each TX result; legacy firmware command forms still default to
125 kHz. `--modulation-cache on|off` selects the same-image command-cache A/B,
requiring `modulation_cache_ab: 1`. The default is on.

The `channel_sweep: 1` capability in `info` identifies the extended V8 harness.
It adds a lab-only round-robin scheduler over the same production `tuneProfile`
implementation. The two profile slots are reused as staging slots; this does
not add N-channel configuration or routing to normal MeshCore firmware.

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output channels.json --samples 100 --preamble 32
```

This starts at four channels and increments by one only after every channel
receives all 100 packets. It stops on the first RF miss/corrupt payload, without
retransmission, lower-channel trials or testing any subsequent count. USB,
reference-TX or scanner faults stop as inconclusive fixture errors instead of
being labeled a channel-capacity limit. The bounded maximum is 64 channels;
if all pass, the result explicitly reports that no miss was found within the
tested range. All results, including the partially completed failing level,
are saved.

Settings: SF6 / 125 kHz / CR4/5, fixed 32-symbol preamble, 16-byte synthetic
packets, -9 dBm TX, buffered 8 MHz SPI and warm fast RX. Channel centers start
at 909.5 MHz in 250 kHz steps (64 channels would end at 925.25 MHz). Use only
authorized RF settings. Each level shuffles channel order within 100 balanced
rounds and varies arrival timing across roughly two scan cycles. Arming a
packet expectation never retunes the receiver or resets its visit clock.

Each idle receive visit targets ceil(4.8 * 512 us) = 2,458 us. Packet-ownership
guards can extend a visit; those held visits are excluded from idle-dwell
statistics. Retune timing and actual idle dwell are measured separately.
There is no millisecond loop sleep in this scanner. Preamble/packet capture
continues through the production preamble/header guards, not blind forced
retuning during a packet. The explicit N-channel schedule does not use the
normal two-profile automatic-preamble-fit calculation or 6/4 ms allowances.

The scanner stops tuning on its first timeout, and `scanstop` puts it in
standby. Reboot before reusing that board for another HIL experiment. A clean
100/100 per channel is a finite observed sample, not proof of a zero PER.

### Traced four-channel diagnostic

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output channels_5p1_trace.json --samples 100 --preamble 32 --max-channels 4 --dwell-symbols 5.1 --trace
```

This requests ceil(5.1 * 512) = 2,612 us visits, keeps four channels, and stops
at the first miss or after all 400 packets pass. `channel_trace: 2` identifies
the required extended harness. Without the new options, the original 4.8-symbol
defaults remain available. The legacy three-argument `scanstart` remains valid.

The HIL HAL observes the existing GetIrqStatus replies and ClearIrqStatus
commands. During each armed expectation the scanner additionally polls IRQs
at a nominal 64 us interval, without clearing them. These are software
observation timestamps, not the exact IRQ edge or RF arrival time. Poll count,
total cost and maximum cost are reported so instrumentation is explicit.
Production packet guards are unchanged. All events are buffered in 64 KiB RAM;
there is no per-event serial output while receiving. Capture freezes on the
packet result. `scantrace <packet-sequence> <offset>` retrieves immutable
24-event pages; overflow is reported and must not be mistaken for a complete
trace. Normal scanning may continue between probes and during trace retrieval.

Event rows are `[relative_us, kind, channel, raw_irq_bits, a, b]`:

- 1: expectation armed; `a` expected channel, `b` current visit age.
- 2: observed IRQ bits changed.
- 3: IRQ clear command observed; `a` clear mask (not proof it succeeded).
- 4: hop completed onto `channel`; `a` preceding receive dwell, `b` retune time.
- 5: first guarded deferral on a visit; `a` dwell, `b` desired next channel.
- 6: packet consumed; `a` validity, `b` byte count.
- 7: expectation timed out; `a` expected channel.

SX126x IRQ bits include 0x0002 RxDone, 0x0004 PreambleDetected, 0x0010
HeaderValid, 0x0020 HeaderErr, 0x0040 CrcErr and 0x0200 Timeout. `held_dwell`
now reports visits excluded from `idle_dwell`; guard-attempt counts are not
packet counts. Host monotonic command bounds and transmitter setup/transmit
durations help localize a probe, but do not synchronize the two MCU clocks.

The first 5.1-symbol run used trace version 1 (no `trace_format` page field).
Its `held_dwell` and hop-row dwell reset when `startRecv()` consumed a packet;
do not use that field to infer total held-channel occupancy. Use the recorded
hop/IRQ/packet timestamps instead. Version 2 tracks channel occupancy across
RX restarts; this is a reporting-only correction, not a scheduler change.

### SF8 comparison at the same chirp count

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output channels_sf8_5p1_trace.json --samples 100 --preamble 32 --max-channels 4 --dwell-symbols 5.1 --trace --sf 8
```

`--sf` accepts 6 (default) or 8; bandwidth remains 125 kHz. Both transmitter
and every receiver retune use that SF, and the host validates their reported
configuration. Require `channel_sf_select: 1` in `info`. The extended protocol
appends SF to `scanstart` and `scantx`; legacy forms keep their SF6 defaults.

SF8 symbols last 2,048 us, so 5.1-symbol visits round up to 10,445 us and a
32-symbol programmed preamble lasts 65.536 ms. At SF6 the corresponding values
are 2,612 us and 16.384 ms. This changes both real listening time and preamble
time, not only the modem SF, and does not itself isolate whether additional
post-retune settling or a longer acquisition window is needed. No extra
settling delay is inserted; measured retune overhead is reported separately.

## Stationary off-channel preamble controls

The `preamble_diagnostic: 1` capability adds a bounded control independent of
the channel scanner. Back up and flash both matching boards as above. Use a
fresh output filename and reboot both HIL boards before each separate run:

```text
python tools/hil/profile_preamble_diagnostic.py --receiver COM31 --sender COM28 --sf 8 --rounds 8 --output stationary_sf8.json
python tools/hil/profile_preamble_diagnostic.py --receiver COM31 --sender COM28 --sf 6 --rounds 4 --output stationary_sf6.json
python tools/hil/profile_preamble_diagnostic.py --receiver COM28 --sender COM31 --sf 8 --rounds 4 --expect-rx-gain boosted --output stationary_sf8_reverse.json
```

These are separate experiments, not a script to run without the intervening
reboots. RX remains at 910.000 MHz for each 450 ms window. Each balanced round
shuffles silent, on-frequency, +/-250, +/-500 and +/-1,000 kHz TX offsets.
Settings are 125 kHz, CR4/5, 32 preamble symbols, 16 synthetic bytes and -9 dBm.
The default eight rounds transmit 56 probes and include eight silent windows.
Use only authorized RF frequencies and suitable antennas/power.

Each case uses full radio initialization and ordinary RX, with fast profile
reuse and experimental omission masks disabled. Buffered 8 MHz SPI remains
enabled. There are no profile hops or software preamble holds. The HAL also
observes actual outgoing frequency words and the SetTx command, which the
collector checks against the requested settings. Results include IRQ traces,
instantaneous RSSI at the first preamble, and counts of synthetic-valid,
foreign-valid and read-error packets; unknown payload bytes are not exported.

Off-frequency non-reception is expected and does not stop this fixed control
matrix. An on-frequency positive-control failure, device/transport fault or
trace overflow stops it. RF probes are never retried. The `diagtxsetup` /
`diagtx` split prepares the transmitter before RX arming; each preparation
permits one transmission only. Cached USB result replay does not retransmit.
The collector ends with `diagstop` on both radios; **reboot is required before
another experiment**, because direct chip control invalidates wrapper state.
Restore and separately verify each private full-flash backup after testing.

See the [measured preamble investigation](../../docs/preamble_detection_investigation.md)
for results and limitations; these are not channel-capacity or sensitivity tests.

## Unchanged-modulation receive-only timing

```text
python tools/hil/profile_switch_same_modulation.py --port COM31 --expect-rx-gain normal --output same_mod_xiao.json
python tools/hil/profile_switch_same_modulation.py --port COM28 --expect-rx-gain boosted --output same_mod_indicator.json
```

This transmits nothing: four three-second SF6/125 four-channel windows in
off/on/on/off order, rebooting between windows. Only the baseline invalidates
the acknowledged modulation tuple before each owned retune. Outgoing SPI
0x86/0x8B counters verify that frequency writes continue while redundant
modulation writes disappear. RX-mode, software-cache and actual RX-gain
checks must pass. See the [measured A/B](../../docs/separated_radio_modulation_cache_validation.md).

## SF5/250 four-channel dwell sweep

```text
python tools/hil/profile_switch_dwell.py --receiver COM31 --sender COM28 --output sf5_250_dwell.json
```

User-defined bounded sweep: 4.1, 4.6, 5.1, 5.6, 6.1, 6.6, 7.1, 7.6, 8.1
symbols, always four channels and 32-symbol TX preamble, 100 packets/channel,
-9 dBm, XIAO normal RX gain, modulation reuse enabled. Each failed dwell stops
at its first timeout or invalid/channel-mismatched packet; then both boards
reboot and the next dwell starts. The whole sweep stops at the first 400/400
result or after 8.1. A fixture error aborts without advancing. Each dwell has
an independent, fresh raw file and the parent manifest checkpoints progress.
These first-miss samples are not comparable PER estimates.

At SF5/250 a symbol is 128 us; the nominal dwell grid is 525, 589, 653, 717,
781, 845, 909, 973, 1,037 us. Bandwidth changes, but centers remain 250 kHz
apart. No added IRQ polling or per-packet traces are used for this short-visit
sweep. Actual retune/idle-dwell timing, hardware/cache status, packet identity,
RSSI/SNR and receive errors remain recorded. Lack of traces means the timing
of any preamble/header/CRC activity cannot be reconstructed. This does not
isolate RF sensitivity from preamble duration, bandwidth, adjacent-channel
behavior, or arrival phase. The controller leaves the test firmware installed
and reboots both boards into idle HIL without autonomous transmissions.

## Post-switch settling-delay sweep (SF6/125)

```text
python tools/hil/profile_switch_settling.py --receiver COM31 --sender COM28 --output sf6_125_settling.json
```

Four channels, SF6/125, 5.1 symbols (2,612 us) per listening visit, 32-symbol
preamble (16,384 us), 100 packets/channel, -9 dBm, normal XIAO RX gain, cached
modulation and no extra IRQ polling. The user requested stopping at the first
400/400 pass. Every failed setting stops at its first invalid reception or
timeout, then reboots both HIL boards before the next setting. Fixture errors
abort the sweep without advancing. No failed probe is retransmitted.

The nominal extra per-hop budget is 16,384/4 - 2,612 - 453 = 1,031 us. Test
0, 100, ... 1,000 us; 1,100 us would exceed the requested four-channel timing
calculation. This uses a nominal measured switch cost, not a worst-case bound
on software jitter, held packets, or physical acquisition.

`scansettle <microseconds>` selects the HIL-only delay (currently 0..24,000,
expanded for the SF10 sweep) before
`scanstart`; changes while scanning are rejected. The host's `--settle-us`
option requires that acknowledgement and validates actual minimum delay
against the request. Reboot defaults to zero; production firmware has no
new settling delay. The receiver must have the new command; the unchanged
reference-TX firmware from the SF5/250 sweep remains compatible.

After a successful guarded retune and BUSY-low observation, the MCU waits the
requested interval without SPI or IRQ operations, then starts an independent
5.1-symbol visit clock. RX is already active and may acquire a signal during
that wait. Therefore this is additional RX occupancy/settling, not a disabled
receiver blanking period or a pre-SetRx delay. Packet guards remain intact;
ordinary RX restarts after processing packets reset the visit clock without
another settling delay. A zero delay preserves the old RX-start clock.

Status reports `switch` (retune through BUSY-low), `settling`, their combined
`switch_and_settle`, and post-settle `idle_dwell` separately. `idle_cycle`
measures consecutive channel-0 return times, excluding initial cycles and
cycles with guard/packet-held visits. It still includes loop and USB handling
that occurred during those cycles. Mean cycle timing is not a worst-case
zero-loss guarantee. Trace hop-row timing includes settling and bookkeeping;
use the separate status timing fields for switch/delay measurements.

## Descending SF10 to SF5 at 125 kHz

```text
python tools/hil/profile_switch_sf_limits.py --receiver COM31 --sender COM28 --output profile_switch_sf125_limits.json
```

Uses four channels, 5.1-symbol visits, a fixed 32-symbol preamble, normal XIAO
RX gain, -9 dBm, 16-byte probes and 100 packets/channel. Start at SF10, then
SF9, SF8, SF7, SF6 and SF5. Each SF uses zero added delay first, then 100 us
increments. A failing setting stops on its first RF miss; a passing setting
must complete all 400 packets. Continue past a first pass until the first
subsequent failed setting, or the nominal timing ceiling. Then lower SF.
This gives sampled pass/fail brackets, not a proof of monotonic behavior or
zero packet-error rate. Do not retry failures to turn them into passes.

Add `--stop-on-first-pass` to stop the **entire descending sweep** at the first
400/400 setting, without testing higher delays or lower SFs afterward. If an
SF has no passing setting, its full delay grid is still exhausted before the
next SF. To change a stopped run's policy without repeating measurements, use
a fresh output prefix and `--reuse-prefix <old-top-level-manifest.json>`.
Only complete matching raw captures are reused; their original bytes and
SHA-256 provenance are preserved. Stop old controllers and let any active
packet collector finish before starting the replacement; never run both on
the same ports. An incomplete existing capture aborts reuse, rather than
silently retrying or overwriting it.

The same per-SF mode is available through
`profile_switch_settling.py --sf 10 --find-upper-limit` plus its normal port
and fresh-output arguments. Without `--find-upper-limit`, the previous
first-400/400-pass stop rule remains the default; without `--sf`, SF6 remains
the default. The descending controller stops entirely on fixture failures.

| SF | Symbol | 5.1-symbol visit | Last 100 us delay step within nominal budget |
| --- | ---: | ---: | ---: |
| 10 | 8,192 us | 41,780 us | 23,300 us |
| 9 | 4,096 us | 20,890 us | 11,400 us |
| 8 | 2,048 us | 10,445 us | 5,400 us |
| 7 | 1,024 us | 5,223 us | 2,500 us |
| 6 | 512 us | 2,612 us | 1,000 us |
| 5 | 256 us | 1,306 us | 200 us |

Budget is `32 * symbol_us / 4 - ceil(5.1 * symbol_us) - 453` per hop.
453 us is the nominal measured retune, not a hard worst-case bound; actual
cycle jitter may exceed the preamble budget near the ceiling. During this
sweep the randomized arrival pause range includes the added settling delay,
so it still spans roughly two nominal cycles even at SF10's largest delays.
Earlier captures keep their original pause distributions and are not rewritten.

The longest SF10 wait is about 23 ms, still after BUSY-low with RX active;
the CPU does no SPI/IRQ work inside that wait. Firmware parser and host limits
are bounded at 24 ms, while the sweep controller enforces each tighter
per-SF grid. Both boards require the expanded SF-capable HIL image for this
experiment. Original firmware is not restored unless requested.

## Same-image double center-frequency write

`scanfreqrepeat 1` enables a HIL-only repeat of SetRfFrequency (0x86) inside
an owned RX-to-RX hop. The duplicate follows the first successful frequency
write immediately, before modulation/packet/RX commands, and skips only a
redundant image-calibration check. Both calls retain RadioLib BUSY/status
handling; either failure aborts profile application. `scanfreqrepeat 0` is
the boot default. Active scanning rejects policy changes. TX initialization
and other non-hop frequency changes are not repeated. Production targets do
not include this override.

```text
python tools/hil/profile_switch_frequency.py --port COM31 --sf 10 --output profile_switch_frequency_timing.json
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM7 --output profile_switch_frequency_packets.json --sf 10 --bw-khz 125 --dwell-symbols 5.1 --max-channels 4 --samples 100 --preamble 32 --frequency-repeat on
```

The RX-only collector uses six-second ABBA windows, checks actual 0x86 count
against one/two per hop, and requires zero modulation commands after setup.
The packet collector checks the same per-hop frequency-command ratio, records
both devices' `info` replies and keeps the original stop-on-first-miss rule.

`profile_switch_heltec_v4` adds a minimal standard V4 reference transmitter
using production pin mappings and FEM detection. The GC1109 branch uses TX
bypass (CSD high, CPS low, DIO2 controls CTX), following
[GC1109 table 4](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/datasheet/GC1109_EN_V0.9.2.pdf).
The KCT8103L branch uses the existing V4 production TX/RX controls, which are
an amplified path. The adapter restores RX after successful or failed direct
TX. The actual detected FEM/mode is included in `info`; -9 dBm remains the
radio-chip request, not a calibrated antenna-port output. Do not call a
V4/Indicator comparison power-matched without external RF measurements.

## XIAO nRF52840 reference TX and 1 ms settling steps

`profile_switch_xiao_nrf52_tx` builds an application-only USB transmitter for
the production XIAO nRF52/Wio SX1262 pin mapping. Its firmware has no scan or
Mesh/network role: it advertises `channel_tx:1`, accepts bounded four-channel,
16-byte/32-preamble `scantx` probes and replays cached results without another
TX. The host accepts TX-only capability on the sender but still requires full
scan/cache capability and the requested gain on the receiver. Native Seeed
and Adafruit USB sessions assert DTR; CH340 bridge handling is unchanged.

The DFU package is application-only and requires SoftDevice ID 0x0123. Verify
the connected board identity and bootloader/SoftDevice compatibility before
uploading; do not erase or replace bootloader/SoftDevice to run a timing test.
The HIL variant adapter reuses production pin/startup definitions without
compiling the variant's Mesh application objects. No shared SDK is modified.

For the requested SF10/125, double-write, 1 ms increment sweep, run
`profile_switch_settling.py` with its normal verified receiver/sender/fresh
output arguments and `--sf 10 --frequency-repeat on --step-us 1000`.
This uses 539 us nominal retune cost and tests added delay 0..23,000 us in
1,000 us steps. The exact nominal maximum is 23,217 us; actual full-cycle
statistics still determine whether jitter overruns the preamble. Keep
`--find-upper-limit` absent to stop at the first 400/400 result. No results
from the previous single-write or different-transmitter runs should be reused.

## Fixed-channel 100-packet positive control

Before a new transmitter's channel sweep, run
`profile_stationary_baseline.py --receiver COM31 --sender COM15 --sf 10
--channel 3 --output <fresh-result.json>` with verified current ports. This
requires receiver capability `stationary_baseline:1`. It performs one full,
normal RX initialization on 910.25 MHz and holds that frequency throughout
100 sequenced CHS1 packets. SF10/125, CR4/5, 32-symbol preamble, 16 bytes,
-9 dBm chip request and normal RX gain match the four-channel experiment.
Only normal RX rearming follows each packet; no scheduler/cache optimization
or frequency hop is exercised. Final status requires zero observed frequency
commands after setup, unchanged gain/mode and matching host/device counts.
Finish all 100 RF probes, including misses, without retries; abort on fixture
failure. Frequency/sequence/channel/body checks reject unrelated packets;
only synthetic results and signal statistics are returned. Firmware stops
after ten minutes if the host disappears. Both boards reboot idle afterward.
Require a 100/100 positive control before restarting a fresh four-channel run.

## Same-image rollback isolation and wider channel spacing

`profile_switch_rollback.py --receiver COM31 --sender COM15 --output
<fresh-manifest.json>` runs bounded SF10/125 A/B cases: cached vs forced
modulation writes, batched vs individual SF/BW/CR writes, fast vs ordinary RX
restart, bulk vs byte-wise SPI, warm XOSC vs RC standby, single vs double
frequency writes, and an explicit 6 ms TCXO timing rollback. These are HIL
switches over the current production retune path, not checkouts of historical
firmware. Ownership, packet guards, BUSY checks and command errors remain active.

The receiver command `scanrollback <mask>` selects bit 0 = full RX restart,
bit 1 = individual modulation setters, bit 2 = byte-wise SPI, bit 3 = ordinary
RC standby (also disables fast RX). Zero preserves the optimized defaults.
`scantcxo 1600|6000` selects timing only, retaining the configured voltage;
the requested change is applied and calibrated before a scan. Reboot restores
the compiled default. `--tcxo-us` and `--rollback` expose these on the packet
collector. Use `--modulation-cache off` to force the modulation control.

Each rollback case first measures six seconds with no added settling, then
selects the largest 100 us step satisfying
`4 * (41780 + observed_max_switch_us + delay_us + 500) <= 262144`.
Another six-second RX-only control must contain at least eight complete idle
cycles, all below the 262.144 ms programmed preamble, before transmitting.
The 500 us per-hop reserve is empirical, not a worst-case guarantee; preamble
acquisition, arrival phase, longer packet holds and external interference can
still defeat this near-ceiling schedule. Slower modes get less *added* delay,
keeping total scan occupancy comparable. Each case stops on its first strict
failure; a passing case must complete 100 packets on each channel. The whole
controller stops at the first 400/400 pass, after all selected cases, or on a
fixture error, and reboots both boards idle. `--cases` selects a bounded subset.

Add `--channel-step-khz 1000` to either rollback or packet collector to use
909.5, 910.5, 911.5 and 912.5 MHz instead of the default 250 kHz spacing.
Both endpoints must acknowledge `scanstep 1000` and advertise spacing support;
the transmitter reports each requested center, and RX trace records the actual
0x86 command word. Wider mode is limited to four channels. This verifies command
programming, not a calibrated measurement of the emitted RF spectrum.

`profile_stationary_offsets.py --receiver COM31 --sender COM15 --output
<fresh-result.json> [--channel-step-khz 1000]` runs three rounds of fixed RX/TX
channel pairs, interleaving on-channel positive controls. Each probe reboots
both boards and fully initializes the receiver once; status must show no
frequency commands afterward. It records accumulated preamble/header/error
IRQs, matching CRC-clean CHS1 payloads, RSSI/SNR and the commanded RF word.
Failed on-channel controls stop the experiment. This isolates off-channel
response from the switching scheduler; it does not identify a physical RF cause.

Trace format 2 also includes event kind 8 (`Frequency`), whose `a` field is the
observed SetRfFrequency word. The next `Hop` event identifies the completed new
logical channel. IRQ traces enable additional 64 us polling, so use untraced
controls for performance comparisons and treat tracing as diagnostic overhead.

## Separate payload-delivery diagnostic (not a strict capacity pass)

Strict scans still require the expected intact 16-byte packet **and** the
expected RX channel. A correct payload decoded while RX is tuned elsewhere
therefore stops that test. To distinguish this from actual missing packets,
the packet collector accepts explicit `--accept-offchannel` on capable HIL RX
firmware (`scanacceptoffchannel 1`). Default is off and resets on reboot.

The raw packet `valid` and host `strict_valid` retain the original criterion.
Only the separately labeled diagnostic uses `accepted` / trial `valid` to
continue on an exact expected sequence/channel/body with a clean CRC regardless
of RX channel. Status separately reports `strict_received` and
`offchannel_received`. A missing or corrupt expected packet still stops the
run; it is never retried. Settling sweeps reject these captures, including
reused evidence, so a payload-only 400/400 cannot become a strict channel pass.
The direct packet collector stops RX and closes both ports when finished;
reboot both endpoints afterward to clear temporary HIL policies. Test firmware
remains installed; there are no autonomous transmissions.

See `docs/sf10_rollback_failure_validation.md` for the rollback, spacing and
fixed-frequency evidence collected on 2026-09-14.

## Mixed SF/BW channels with identical symbol duration

```text
python tools/hil/profile_switch_mixed.py --receiver COM31 --sender COM15 --output mixed_equal_symbols.json
```

Both endpoints must contain the shared HIL `ProfileMixedChannels.h` plan,
acknowledged by `mixinfo`: channel 0 SF9/62.5 kHz, channel 1 SF10/125 kHz,
channel 2 SF11/250 kHz, channel 3 SF12/500 kHz. Every symbol is exactly 8,192 us.
Centers remain 909.5, 910.5, 911.5 and 912.5 MHz. Ten fixed-frequency positive
controls per channel precede scanning; all forty must pass. These are ten-
packet controls, not the earlier uniform-mode 100-packet baseline.

`basemixed <channel> <sequence>` selects one fixed full-init receiver profile;
`mixtx <channel> <sequence>` sends one matching 16-byte/32-preamble probe from
the nRF52 reference TX. Sequence result replay never retransmits. All commands
are bounded to four profiles and retain the -9 dBm request. Fractional 62.5 kHz
is preserved through configuration and result output, not rounded to 62/63.

`scanmixed 1` explicitly selects per-channel modulation in the existing HIL
scanner. `scanstart` still uses SF10/125 as its *timing anchor* (5.1 symbols =
41,780 us, 32 preamble symbols = 262,144 us), not as uniform RF modulation.
The dedicated collector records and verifies the actual mixed plan separately.
Every guarded production hop applies the next channel's frequency and its
SF/BW. Post-hop cache validation compares against that channel's profile;
packet records include SF/BW at read. Every hop must issue two frequency
commands and one modulation command; 0x8B cannot be skipped for this plan.
No receive guard is bypassed. Normal RX gain and warm/fast/bulk operation stay
enabled; production defaults and public two-profile protocol are unchanged.

RX-only timing chooses the largest 100 us settling step using the same
observed-max-switch plus 500 us per-hop reserve as the rollback experiment.
A second RX-only window verifies idle cycles fit before transmitting. Test
100 packets per channel, stop at first strict miss or 400/400 pass; if the
near-ceiling setting fails, run one zero-added-delay comparison. Stop at the
first complete pass or after both failures. No payload-only acceptance, no
RF retries and no automatic channel-count expansion. Both boards reboot idle
afterward with test firmware retained. A finite pass is not proof of zero PER.

## Two complete retunes per SF10/125 channel hop

```text
python tools/hil/profile_switch_retune_repeat.py --receiver COM31 --sender COM15 --output full_retune_twice.json
```

This HIL-only experiment applies the complete frequency/modulation/preamble/
RX sequence twice to the same destination channel. It is not just the earlier
immediate duplicate 0x86 command. `scanretunepasses 2` forces a second real
`tuneProfile` transaction by marking that profile for refresh, and forces the
modulation write in both passes. It waits boundedly for first-pass BUSY-low;
the normal packet/ownership/BUSY guards are evaluated again before pass two.
If a newly acquired packet blocks pass two, abort as a fixture failure rather
than overriding the guard or returning a misleading "BUSY/no channel change"
after pass one already moved the radio. One pass is the reboot default.

Bounded to uniform SF10/125, four channels, 32 preamble symbols and the fast
path. The collector uses 1 MHz spacing, 5.1-symbol visits, no added settling,
normal RX gain, -9 dBm and 100 packets/channel with strict first-miss stopping.
The existing double frequency write remains enabled **inside each pass**, so
a completed hop must show four 0x86 writes, two 0x8B writes and two optimized
RX starts. Status reports `first_pass`, `second_pass`, total `switch`,
`retune_passes` and `second_pass_blocked`; failures invalidate the result.

The controller measures six-second RX-only windows in 1/2/2/1-pass order,
then runs the two-pass packet test. If the strict failure is merely an intact
payload on another channel, it may run one separate payload-delivery diagnostic;
that cannot become a strict pass. No RF retries. Both boards reboot idle at
completion, retaining HIL firmware. Direct use of the generic packet collector
requires `--retune-passes 2 --modulation-cache off` plus the matching SF10/125,
four-channel/32-preamble arguments. Production firmware is unchanged.

### Tiny frequency and coding-rate detour on the first pass

```text
python tools/hil/profile_switch_retune_repeat.py --receiver COM31 --sender COM15 --first-pass-detour --output first_pass_detour.json
```

Optional HIL-only first pass: destination plus a requested 0.01 kHz (10 Hz),
CR4/6; second pass: original destination frequency and CR4/5. SF10/BW125 and
all the two-pass guards above remain unchanged. This does not use a float-MHz
addition: near 910 MHz that would round away. `ProfileFrequencyOffset.h`
instead adds ten integer steps to the actual 0x86 command word during the
temporary CR4/6 apply. The nominal programmed offset is 9.5367431640625 Hz,
the nearest synthesizer step to 10 Hz. Both duplicate frequency writes in
that pass are shifted, without altering the caller's buffer. The scoped
offset is zero on the corrected pass; rollback retains the offset only when
restoring the temporary CR4/6 tuple. This is command-level verification, not
an independent RF frequency measurement.

`detourinfo` describes the exact offset; `scanfirstdetour 1` enables it while
the scanner is stopped. It defaults off on reboot and requires two passes.
Every completed hop verifies both outgoing RF words and modulation words:
first frequency = nominal + 10 steps, final frequency = nominal;
modulation 0x0a040200 then 0x0a040100. Any mismatch fails the diagnostic.
Status `detour.n` must equal completed hops and `detour.bad` must be zero.

The controller uses same-image ABBA RX-only timing: two identical passes,
detour/correction, detour/correction, two identical passes. It then runs the
strict packet test with the detour enabled. Controls, failures and optional
payload diagnostics remain separately labeled. The capability reply uses
one LF-terminated write with trailing JSON whitespace to avoid an exact
64-byte packet boundary on native USB; no transmission is retried to repair
USB setup.

### Complete error-rate samples: 10 Hz versus 100 Hz detour

```text
python tools/hil/profile_switch_offset_compare.py --receiver COM31 --sender COM15 --output offset_comparison.json
```

This sequential same-image comparison runs exactly 100 packets per channel,
400 at each offset, with all other settings above unchanged. The generic
collector's explicit `--continue-on-miss --max-channels 4` selects fixed-sample
mode. It does not expand the channel count or call a failed sample perfect.
RF misses, wrong-channel payloads and timeouts do not stop the run; USB,
hardware, guard or command-verification failures still invalidate it.
Each packet is sent once, with the existing 10-second expectation timeout.

Receiver `scancontinue 1` keeps the scanner running after each scored miss
without restarting the receiver, retuning to the TX channel, resetting the
visit clock or clearing counters. Stop-first remains the reboot default.
`continueinfo` reports the capability and timeout. The policy can be changed
only while the scanner is stopped. A reboot separates the two offset runs.

`scandetouroffset 10|100` selects requested Hz; the generic host option is
`--first-pass-offset-hz 10|100` together with `--first-pass-detour`.
The exact programmed choices are +10 steps (9.5367431640625 Hz) and +105
steps (100.13580322265625 Hz). Both use the integer command path and restore
the original word on pass two. The same per-hop verification checks the
selected offset, with CR4/6 on pass one and CR4/5 on pass two.

Outputs retain every attempt and separate strict same-channel success from
intact payload delivery on another reported RX channel. `error_rates`
contains overall and per-channel denominators, strict errors, delivery
errors, timeouts and wrong-channel intact payload counts. Missing or invalid
payloads contribute to delivery error rate; wrong-channel intact packets
contribute only to strict error rate. Completion requires all 400 attempts
and exactly 100 per channel; host/device counters must agree. Results from
each sequential run describe this finite bench sample, not a proven causal
effect or sensitivity qualification. Both devices reboot idle at completion.

### Four physical, fixed-channel transmitters

`profile_four_tx.py` runs the explicitly identified MercerMesh fixture in
`profile_four_tx_fixture.py`: RAK4631, T096, MeshTower V2 and T1000-E transmitters,
with the Pi's Heltec V4 receiving. Duplicate CDC interfaces are not additional
radios. The RAK3401 gateway is excluded. The V4 soak logger must remain paused
while its firmware is the bench receiver; other gateway services are unchanged.

Build recipes are in `profile_fixed_tx.ini`. Each transmitter accepts one
`txprepare <channel>` per reboot, fixing SF10/BW125/CR4/5, 32-symbol preamble,
and 909.5 + channel MHz. `scantx` sends the synthetic 16-byte CHS1 payload
without calling initialization or frequency/modulation setters again. Actual
SPI frequency/modulation command deltas must remain zero for every packet.
The T1000-E uses its LR1110 driver and production RF-switch table; it is not
an SX1262 board. The two Heltec TX paths include external amplification, so
the -9 dBm chip setting is not a calibrated antenna-port power measurement.

The collector takes 100 fixed-RX controls per transmitter, then the full
400-packet scan, with randomized channel order/arrival spacing and no overlapping
transmissions. The RX uses 5.1-symbol visits (41,780 us), no added settling delay,
one complete retune and one RF-frequency command per hop, warm XOSC, bulk 8 MHz
SPI, fast RX restart and cached unchanged modulation. Normal packet-processing
RX staging invalidates the owned-hop cache, so the next hop refreshes 0x8B;
subsequent unchanged hops omit it. Double passes, duplicate frequency writes, offsets and CR detours are
explicitly disabled. Normal SX1262 RX gain (0x94) is verified; external FEM
behavior is reported separately.

RF misses do not truncate the sample or retransmit packets. Hardware errors,
USB failures, changed board identity, TX resets and command-count mismatches
invalidate the test. All four TXs remain on their assigned frequencies through
both controls and scanning. Results distinguish strict same-channel reception
from intact wrong-channel payloads. This is a new five-board fixture, not a
controlled same-receiver comparison against the earlier local XIAO runs.

The completed 2026-09-14 run, including the post-reception cache-refresh timing
and original host-assertion caveat, is documented in
[four-transmitter validation](../../docs/four_fixed_tx_single_pass_validation.md).

`profile_four_tx.py --dwell-symbols 6.1` selects 49,972 us visits at SF10/125;
the default remains 5.1 symbols (41,780 us). Use a fresh run directory with the
verified fixture dependencies and deployment provenance. This option does not
change firmware, TX settings, sample counts, preamble, settling or switch policy.
Random arrival delays retain the existing distribution over approximately two
nominal scan cycles, so their absolute time range scales with dwell.
The [longer-dwell report](../../docs/four_fixed_tx_dwell_validation.md) records
the user-stopped 6.1-symbol sample and completed 7.7-symbol repeat.

## T1000-E LR1110 RX-only timing

The checked FS-mode fast profile switch is enabled by default in
`CustomLR1110` for all MeshCore LR1110 targets: T1000-E, Wio WM1110,
ThinkNode M3/M7/M9, and Minewsemi ME25LS01. Their radio SPI paths now use
16 MHz buffered transfers. Each board keeps its existing TCXO startup delay;
the 0.6 ms combined-hop goal is **not** a 600 µs TCXO delay. Only the T1000-E
has on-device timing validation so far. Other boards require measured retune
timing, packet reception, and TX checks before claiming the 0.6 ms goal.

`profile_switch_t1000_lr1110` uses the production `CustomLR1110Wrapper` and
the same 200 µs TCXO/fast-retune settings as the T1000-E variant. It never
transmits, loads an identity, or writes saved settings. Its only frequencies
are 909.5 and 909.75 MHz; the `mod`, `freq`, `both`, and `preamble` modes change
SF7/SF8, frequency, both, or 32/48 preamble symbols respectively. A run first
times eight rapid startup-style hops (four in each direction), then the
requested longer sample with 10 ms between hops. Results report each sample's
mean/maximum and the production wrapper's 10%-guarded startup and final
budgets. A separate RSSI read checks that each resumed receiver responds after
seven milliseconds of settling; it is outside timing.

The [four-hop comparison](profile_switch_t1000_spotcheck_results.json) ran
three 1,000-hop combined frequency/SF trials. The rapid eight-hop samples
averaged 504.346 us and peaked at 507.156 us; the 3,000 later hops averaged
502.907 us and peaked at 503.172 us. The firmware's startup allowance was
547 us in all three trials and covered every later hop. The cycle-counter
measurement includes a post-retune BUSY check that the firmware's `micros()`
sample ends before, so its 10% allowance is not exactly 110% of the reported
cycle-counter maximum. This validates four samples per direction on this
T1000-E only, not on every LR1110 or GPIO-expander board. The exact original
Full Companion application was restored after the test.

Build with `pio run -e profile_switch_t1000_lr1110`. Flash its application-only
ZIP with serial DFU after verifying the board's unique USB serial number and
bootloader identity; do not use a whole-chip erase or UF2 copy as a substitute.
At 115200 baud the commands are `info`, `spihz 2000000|8000000|16000000`,
`bulk 0|1`, `run both 1000 1` (use a new sequence number per run), and
`result result 1` to replay the last result without another RF run. `fs 0|1`
compares the corrected XOSC standby with the default FS-mode retune.
`clear 0|1|2` compares always-clear, never-clear (unsafe diagnostic only), and the
default checked-clear path. `inject 1` puts a one-shot RX-timeout IRQ into the
next run to verify checked clearing. `tcxo`
accepts 50, 100, 150–200 in 10 µs steps, 400, 800, and 1600 µs for RAM-only
experiments; reboot restores the compiled 200 µs. Each JSON result reports
command counts, busy deferrals, drained test packets, failures, RX/cache/RSSI
errors, and both timing directions. The RX-only harness discards pending packets
before retrying a deferred hop; otherwise an unprocessed interrupt can stall
the test even though production firmware would consume it.

On the Pi-attached T1000-E, 16 MHz buffered SPI and default 200 µs TCXO,
FS-mode retune plus checked IRQ clearing averaged about 0.504–0.508 ms for
simultaneous frequency/SF changes, 0.384 ms for frequency only, 0.409 ms for SF
only, and 0.361 ms for a preamble change. Three boot/run cycles with the final
default HIL image each completed 1,008 combined hops; earlier runs completed
three more combined cycles plus 1,008 of each single-change mode. No completed
run had a radio, cache, RSSI, or BUSY-timeout error. An injected stale timeout
IRQ was detected and cleared before RX re-entry. A corrected XOSC standby path
with checked IRQ clearing took about 0.568–0.575 ms; always clearing took about
0.60 ms. The old RadioLib `STANDBY_XOSC` constant is erroneously zero (RC mode),
whose previous combined result was about 0.87 ms at a requested 180 µs. The
unbuffered 2 MHz RC-standby baseline was about 1.36 ms.

RadioLib converts the requested TCXO delay to 32.768 kHz ticks by truncating
`delay / 30.52`, so requests of 160, 170, and 180 µs all programmed five ticks
(about 153 µs), while 200 µs programs six (about 183 µs). The earlier 150 µs
trial failed after reset; the 160/170 µs runs developed deferrals, but those
differences do not establish a precise hardware threshold. With true XOSC or
FS-mode retuning, 180 and 200 µs produced indistinguishable warm-hop timing;
200 µs remains the selected startup setting. These are room-temperature,
single-unit, RX-only retune/command-health measurements, not over-the-air
packet reception, TX operation, or voltage/temperature-corner qualification.
