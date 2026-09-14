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
Restore the original image after testing. Do not put raw flash backups in Git:
they may contain identity, channel, Wi-Fi, and other secrets.

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
XIAO S3 WIO and Indicator LoRa opt in to fast RX and buffered 8 MHz SPI in their
normal variants. Other variants do not opt in to the fast path or faster SPI.

Fast RX reuses IRQ mapping and buffer bases only after successful continuous
LoRa RX, within an owned warm retune. It retains packet-parameter/IQ programming,
stale-IRQ clearing, RF-switch control, SetRx and BUSY waits. TX, CAD, ordinary
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
python tools/hil/profile_preamble_diagnostic.py --receiver COM28 --sender COM31 --sf 8 --rounds 4 --output stationary_sf8_reverse.json
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
