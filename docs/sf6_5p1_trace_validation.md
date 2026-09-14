# SF6 / 125 kHz: four channels at 5.1 chirps

Measured 2026-09-14. **Seven packets received, then packet 8 missed.** The run
stopped at that first miss. No RF retry, other dwell, or higher/lower channel
count was tested. The target of 100 packets per channel was not reached.

Follow-up: [SF8/125 at the same 5.1-chirp dwell](sf8_5p1_trace_validation.md)
missed on packet 13 and captured an off-channel preamble hold. That differs
from the absence of observed preamble in this SF6 failure.

## Setup

- XIAO ESP32-S3 + WIO SX1262 scanner, COM31.
- SenseCAP Indicator ESP32-S3 reference transmitter, COM28.
- Four centers: 909.500, 909.750, 910.000, 910.250 MHz.
- SF6 / 125 kHz / CR4/5, 32-symbol programmed TX preamble.
- User-corrected dwell: **5.1 symbols**, ceil(5.1 * 512) = **2,612 us**.
- Same warm production fast retune, buffered 8 MHz SPI, packet guards.
- 16-byte synthetic payloads at -9 dBm; balanced randomized channel order,
  seed 606125; varied arrival timing, not synchronized to scan phase.
- IRQ changes, IRQ clears, hops and first guard deferrals buffered in RAM.
  Additional read-only IRQ polling at nominal 64 us intervals while armed.
  No per-event serial printing during capture. Trace read after each result.

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output tools/hil/sf6_125_5p1_trace_results.json --samples 100 --preamble 32 --max-channels 4 --dwell-symbols 5.1 --trace
```

## Results and what the trace establishes

Probes 1–7 succeeded on channels **3, 2, 1, 0, 2, 3, 1**. Probe 8, sequence
`944962150`, missed on channel **0 (909.500 MHz)**. Thus channel 0 had already
received a valid probe; this was not a channel that never worked at all.
Successful packets had RSSI -42 to -40 dBm and SNR 9.5 to 10.8 dB.

The failed probe has a complete, non-overflowed 3,160-event trace:

- Expectation armed on channel 3 without selecting the expected channel.
- 3,158 completed hops during the bounded 10-second confirmation interval.
- **No observed nonzero IRQ bits**: no preamble, header, RxDone or CRC event.
- **No guard-held visit** on any channel during that expectation.
- Receiver timeout at 10,000,369 us, with RX mode and zero device errors.
- Reference TX returned success. All USB sequence acknowledgements matched;
  no stale replies, missing acknowledgements or cached transport recovery.

The scanner was not stuck on the wrong channel by a false preamble. It also
did not acquire a header and then fail a CRC check. The evidence locates the
failure before **observed preamble acquisition**, not at payload decoding.
It does not prove why acquisition failed: a short-visit/arrival-phase issue
remains a candidate, as does an unobserved RF/reference issue. There was no
independent RF capture, and the two MCU clocks were not synchronized.

### Actual timing during the failed expectation

| Measurement | Minimum | Mean | Maximum |
| --- | --- | --- | --- |
| Receive dwell, 3,158 hops | 2,613 us | 2,628.706 us | 2,646 us |
| Retune, trace hop rows | 526 us | 538.700 us | 630 us |
| Channel 0 revisit interval, 789 cycles | 12,643 us | 12,666.829 us | 12,705 us |

The repeatable scan timing rules out a large software scheduling stall in the
failed capture. The 32 programmed symbols occupy 16.384 ms, longer than that
idle scan cycle; nevertheless, revisiting within the preamble did not ensure
successful acquisition during these short individual visits.

### Successful acquisition traces

Times below are relative to the logged hop completion onto the eventual
receive channel, not a precisely captured RF edge or hardware RX-start edge.
The trace observed the following progression for every successful probe:
PreambleDetected (0x0004), HeaderValid (0x0014), RxDone (0x0016), consumption.

| Probe | Channel | First observed preamble after hop | RxDone after hop |
| --- | --- | --- | --- |
| 1 | 3 | 1,838 us | 32,982 us |
| 2 | 2 | 1,836 us | 35,470 us |
| 3 | 1 | 1,844 us | 35,151 us |
| 4 | 0 | 2,332 us | 37,329 us |
| 5 | 2 | 1,847 us | 35,532 us |
| 6 | 3 | 1,831 us | 30,731 us |
| 7 | 1 | 1,329 us | 32,567 us |

After detecting preamble, the production guard kept those visits on-channel
until reception completed. The failed probe never reached that observed
state. This is not evidence that 5.1 symbols always suffice, nor does 7/8
estimate a stable packet-error rate or establish a channel-capacity limit.

## Instrumentation and reporting caveats

The failed capture made 115,783 diagnostic IRQ polls, costing a total
2,821,617 us (about 24.4 us each; maximum 61 us). These reads are not free:
they alter loop load, although actual dwell and revisit timing were measured.
This is **not a matched A/B** against the earlier untraced 4.8-symbol run, so
seven successes versus two cannot be attributed solely to the extra dwell.

The run used `channel_trace: 1`. During analysis, a reporting bug was found in
the newly added `held_dwell` aggregate: production `startRecv()` resets the
visit timestamp after reading a packet. That aggregate therefore omitted the
preceding held reception time. **Do not use its 2.709 ms mean as total held
occupancy.** The timestamped IRQ/hop trace above is the evidence for the actual
30.7–37.3 ms hop-to-RxDone intervals. The failed capture had no RX restart, so
its dwell rows are unaffected.

The source now keeps a separate channel-occupancy timestamp (`channel_trace:
2`, `trace_format: 2`). Both HIL targets compiled with this reporting-only
correction; it was **not flashed or RF-tested**, and the saved raw result was
not rewritten. Scheduler, packet guard and radio commands were not changed by
that correction. Regression coverage includes passive SPI observation,
bounded/frozen capture, occupancy accounting, page identity, requested dwell,
and stopping at the first miss.

## Provenance and device handoff

- [Unmodified raw result and all eight traces](../tools/hil/sf6_125_5p1_trace_results.json)
- [Harness and trace format](../tools/hil/profile_switch_README.md#traced-four-channel-diagnostic)
- [Earlier 4.8-symbol result](sf6_channel_scan_validation.md)

Raw result SHA-256:
`7caa16483b7b0000b388a1906395fe3d381bd0e40de73f48cd5c876d96deab3b`.

**Actually tested** firmware SHA-256 (trace version 1, before reporting fix):

- XIAO: `35c201f367b0aaa693be03542a4c115024d1a70a2be2fbdbef2c94da5a6053ae`
- Indicator: `8a25d84fc99f6aeaf8318bf4533767e16a9afc0de51394fce27d3a610c63b352`

Base commit `c329cf1fc1e7f2e8c8733ab0d000094730458fe3` plus local changes;
RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`, Espressif32
6.11.0 / Arduino ESP32 2.0.17. No normal production firmware change this turn.

Fresh full 8 MiB snapshots were saved and digest-verified before flashing:

- XIAO: `35aa2deeb8a8145bc54435c9f87b3db1c65f320f2568ee0b489305db1a6330c2`
- Indicator: `dba59cde1df3c69dd2e5995950315951f75cd43886b21be8b14e261ec4b57309`

Both snapshots were restored afterward, separately verified against the full
flash, and hardware-reset. Snapshots remain in the private current-user backup
directory. The RAK4631 and Indicator RP2040 were not modified; the XIAO soak
logger was not restarted. Changes were initially handed off locally before publication.
