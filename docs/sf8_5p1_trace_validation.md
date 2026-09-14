# SF8 / 125 kHz: four-channel comparison at 5.1 chirps

Measured 2026-09-14. **12 packets received, then packet 13 missed.** The first
three balanced rounds passed (3/3 on every channel); the next probe on channel
3 failed. The run stopped at that miss. The target of 100 packets per channel
was not reached. No RF retry or other channel count was tested.

## Setup and comparison

Same XIAO ESP32-S3 + WIO SX1262 scanner (COM31) and SenseCAP Indicator ESP32-S3
reference transmitter (COM28). Four centers: 909.500, 909.750, 910.000 and
910.250 MHz. CR4/5, 32-symbol programmed preamble, 16-byte synthetic packets,
-9 dBm, warm production fast RX and buffered 8 MHz SPI. Same production packet
guards and read-only 64 us nominal IRQ polling; events buffered until each
result. Randomized balanced channel order, seed 606125, varied arrival timing.

| Setting | Earlier SF6/125 | This SF8/125 |
| --- | --- | --- |
| One symbol | 512 us | 2,048 us |
| Requested 5.1-symbol dwell, rounded up | 2,612 us | 10,445 us |
| 32 programmed preamble symbols | 16.384 ms | 65.536 ms |
| Whole-run average measured retune | 539.427 us | 524.124 us |
| Outcome before first miss | 7 received, packet 8 missed | 12 received, packet 13 missed |
| Miss trace | No observed preamble or hold | Off-channel preamble and prolonged hold |

Changing SF increases real listening and preamble durations together. This is
not a matched same-image A/B or an isolated post-retune settling experiment.
The small retune-time difference must not be treated as a new optimization;
no extra settling delay was inserted. The earlier reporting-only held-visit
timestamp correction is included in this trace-version-2 run.

## Failed probe: an off-channel hold

Failed sequence `388715032` targeted **channel 3 / 910.250 MHz**. The scanner
instead observed PreambleDetected on **channel 2 / 910.000 MHz**, then its
normal packet guard deferred the intended jump to channel 3. It never observed
a valid header, header error, CRC event, or RxDone for that preamble.

Times are software observations relative to expectation arming:

| Time | Event |
| --- | --- |
| 207.649 ms | Last hop onto target channel 3 before the long gap |
| 240.542 ms | Hop onto channel 2 |
| 250.030 ms | PreambleDetected (0x0004) observed on channel 2 |
| 251.013 ms | Guard defers channel 2 → 3 at the 5.1-symbol deadline |
| 342.287 ms | Guard issues a clear of the still-latched preamble bit |
| 342.794 ms | Hop onto channel 3 finally completes |
| 342.851 ms | IRQ read confirms zero bits |
| 9,999.270 ms | Expectation timeout, RX mode, zero device errors |

Channel-2 occupancy was about **102 ms**. The target channel's revisit gap
grew to **135.145 ms**, compared with its ordinary ~43.875 ms cycle and the
65.536 ms programmed preamble. The delayed retune itself took only **550 us**.

The preamble-to-header budget from `calcMaxPacketMillis()` is
ceil((32 + 8 + 4.25) * 2.048) = **91 ms**. The strict millisecond timeout in
`CustomSX1262::isReceiving()` clears a preamble that does not advance to a
header. The observed **92.257 ms** from the first IRQ observation to that
clear is consistent with this existing protection mechanism.

This identifies a prolonged off-channel hold that removed the next listening
opportunity. It does **not** show that the ~0.52 ms jump needs more settling
time. The RF source of that preamble is not proven: adjacent-channel pickup/
false detection or unrelated RF are possibilities. There was no independent
RF capture or synchronized MCU clock. The earlier SF6 miss had no observed
preamble or hold; this result does not establish the same cause for that miss.

Simply shortening the guard risks cutting off valid long-preamble packets.
Successful traces show that reception legitimately keeps a channel occupied
well past 10.445 ms. No production guard change was made in this diagnostic.

Follow-up: the [stationary preamble investigation](preamble_detection_investigation.md)
reproduced transmitter-associated off-frequency indications with full RX
initialization and no hopping, on both boards. It strengthens the off-channel
detection explanation without independently identifying the RF source of this
historical missed probe. The SF6 miss remains a separate unresolved case.

## Measurements and checks

- All 12 successful payloads were valid; RSSI -42 to -41 dBm, SNR 12.0 to
  14.2 dB. First observed preamble after logged hop completion ranged
  **7.433–9.485 ms**; hop-to-RxDone ranged **105.056–146.836 ms**.
- Entire run: 1,167 fast hops, mean retune 524.124 us, maximum 774 us. Idle
  dwell mean 10,452.444 us; held visits separately accounted for.
- Failed expectation: 910 trace events, zero overflow, 903 completed hops.
  The 902 unheld dwells were 10,445–10,480 us, mean 10,450.424 us.
- Excluding the one held cycle, target-channel revisit intervals were
  **43,847–43,928 us**, mean **43,874.580 us** (224 intervals).
- Failed-expectation retunes: 511–617 us, mean 520.443 us. The held occupancy
  accounts for the anomalous target-channel revisit gap.
- Zero retune failures, RX-mode errors, modulation-cache errors, CRC/read
  errors, USB stale replies or cached transport recovery. Reference TX rc=0.
- 133,217 extra IRQ polls during the failed capture; total cost 3,241,509 us,
  maximum 55 us. Polls add load; timestamps are observations, not exact IRQ
  edges. No per-event serial output occurs during capture.

This first-miss sample neither estimates a stable packet-error rate nor
establishes a channel count with 100% reception.

## Reproduction and provenance

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output tools/hil/sf8_125_5p1_trace_results.json --samples 100 --preamble 32 --max-channels 4 --dwell-symbols 5.1 --trace --sf 8
```

- [Raw result and all 13 traces](../tools/hil/sf8_125_5p1_trace_results.json)
- [Earlier SF6/125 at 5.1 chirps](sf6_5p1_trace_validation.md)
- [Harness instructions](../tools/hil/profile_switch_README.md#sf8-comparison-at-the-same-chirp-count)

Raw result SHA-256:
`0d7e97173937f42708febf8276cdadc36578740735058d5da39b1b576e9ca49e`.

Tested firmware SHA-256:

- XIAO: `a68ab9834f62d0a5fb3f10c7a0b312c17d7d62795885cac4ea0dce483d6bf1ea`
- Indicator: `bca1514bc6e09af6b1a9daed5bf4d3dbeeab988bcb856acbc73f59334395280d`

Base commit `c329cf1fc1e7f2e8c8733ab0d000094730458fe3` plus local changes;
RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`, Espressif32
6.11.0 / Arduino ESP32 2.0.17. Both HIL targets built successfully. All 27
selected host/native tests passed, including SF scaling, propagation to both
RX staging slots and TX, cache checks, trace handling and first-miss stop.

## Device handoff

Fresh full 8 MiB backups were saved and digest-verified before flashing:

- XIAO: `e9533742103c9d7af11a3dc0c40496ca412b072e11880c2be785c92ef80e4310`
- Indicator: `d4f39a0be1339f142ed329b68606347adeb514d26d6cc8bad43fb3d343ac1e2a`

Both were restored after the test, separately full-flash digest-verified, and
hardware-reset. Backups remain in the private current-user backup directory.
No identity or channel secrets were used in the synthetic RF probes. The
RAK4631 and Indicator RP2040 were not modified; the XIAO soak logger was not
restarted. The temporary HIL build overlay was removed. Source, tests and
results were initially handed off locally before publication.
