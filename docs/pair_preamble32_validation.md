# SF7/62.5 + SF8/500, fast switching and preamble 32

User-requested hardware validation, 2026-09-14. This is a finite HIL experiment,
not a production reliability guarantee. No production timing defaults or
automatic-preamble overrides are changed by this test.

## Fixture and method

- Heltec V4 receiver, USB serial `44:1B:F6:69:CF:98`, normal chip RX gain (0x94).
- RAK4631 `9AB3B64C641BA927`: stationary transmitter at 909.5 MHz, SF7/62.5 kHz.
- T1000-E/LR1110 `34A9141999729D5D`: stationary transmitter at 910.5 MHz, SF8/500 kHz.
- CR4/5, explicit header, CRC, 32-symbol preamble, synthetic 16-byte sequence-tagged
  payloads. -9 dBm chip output. Only one transmitter sends at a time on host request.
- Each transmitter initializes its assigned settings once. RF-frequency and
  modulation-command counters must remain unchanged for every TX call.
- 100 fixed-channel controls per profile before scanning. Require 100/100 on
  both before making a scan-timing inference.
- Three scanner settings, 100 packets per profile per setting, randomized
  interleaving and 10–180 ms arrival delays. A miss does not cause a retry,
  retune, reset, or early end of the finite sample. The scan expectation expires
  after 1000 ms, far longer than the test frame and randomized host delay.

| Case | Loop reserve (not a delay) | Slow visit | Fast visit |
| --- | ---: | ---: | ---: |
| 4.1-chirp floor | 0 ms | 8.397 ms | 23.171 ms |
| Spare time used for 5.1 slow chirps | 0 ms | 10.445 ms | 21.123 ms |
| Previous reserve comparison | 4 ms | 8.397 ms | 19.171 ms |

The 5.1 case reallocates 2.048 ms from the fast visit to the slow visit; it does
not increase the nominal cycle. The 4 ms reserve is omitted from the first two
cases. The theoretical switch allowance is 0.6 ms per hop; actual switching is
measured, never forced to equal that allowance. Changed SF/BW requires one
modulation command per hop; frequency-only cache-hit timings do not apply.

The scheduler uses the same asymmetric two-profile visit budget and the actual
production guarded `tuneProfile()` / fast RX implementation, but runs in a small
RAM-only HIL application, without the full production networking/UI load. All
retunes are single-pass, warm-XOSC, buffered 8 MHz SPI, zero added settling;
double frequency writes, detours and rollback experiments are off. Packet/busy
ownership guards are retained. Explicit 32 on both sides bypasses the historical
automatic SF8/500 88-symbol rule without removing it from production.

The selected radios already had disposable HIL firmware. Updates are
application-only: matching nRF52 SoftDevice IDs/address ranges and exact USB
serial/topology are checked. V4 partition metadata is read back and compared
before its application-only write. No bootloader, SoftDevice, settings backup,
gateway firmware or unrelated radio is changed. Prior images/captures remain.

## Results

Finished all 800 unique probes, 2026-09-15 00:04:54–00:11:19 UTC (September 14
local time). Both stationary controls passed 100/100. All three scans completed
their full 100-packet sample per profile. No scan achieved 200/200.

| Case | SF7/62.5 received | SF8/500 received | Total | Switch mean / max | Idle cycle mean / max |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4.1 chirps, no loop reserve | 99/100 | 100/100 | 199/200 | 0.581 / 0.829 ms | 32.717 / 34.733 ms |
| 5.1 chirps, no loop reserve | 100/100 | 97/100 | 197/200 | 0.572 / 0.834 ms | 32.706 / 34.978 ms |
| 4.1 chirps, 4 ms loop reserve | 97/100 | 99/100 | 196/200 | 0.567 / 0.836 ms | 28.701 / 31.195 ms |

Switch timing includes every successful hop during each stage, including
post-reception hops. The RX-only five-second windows averaged 0.554 ms per hop.
The largest observed switch was 0.836 ms: 0.6 ms is a nominal allowance, **not a
measured worst-case bound**. Direction-specific timing is in the validated
capture. Idle-cycle samples exclude packet-held visits but still include USB
command/loop scheduling; their occasional roughly 2 ms overshoots are real.

Every scan hop issued exactly one RF-frequency and one modulation command,
with one optimized RX restart. There were zero added settling delays, double
passes, frequency repeats, RF retries, USB result replays, device errors,
retune failures, RX mode errors, cache errors or off-channel acceptances.
All eight missed packets were sequence-matched RX timeouts with the receiver
still in RX mode and no device error. Both transmitters acknowledged exactly
400 transmissions and zero frequency/modulation writes during every packet.

Successful scan packets had mean SNR approximately +12.0 to +12.1 dB on the
slow profile and +12.8 dB on the fast profile. Mean RSSI was approximately
-20.4 to -20.7 dBm and -18.5 to -18.9 dBm, respectively. These are strong-signal
bench results; the controls passed, but weak-signal performance and possible
strong-signal/front-end effects are not established by this test.

## What the extra chirp changes

An extra SF7/62.5 symbol is 2.048 ms, equivalent to **four SF8/500 symbols**.
Increasing the slow dwell from 4.1 to 5.1 preserves the full-sweep budget by
shortening the fast dwell, but increases the fast profile's uninterrupted
blind interval. With the nominal 0.6 ms hops, the 32-symbol fast preamble's
remaining time after the slow visit plus two hops falls from 6.787 to 4.739 ms
(13.26 to 9.26 fast symbols), before acquisition and scheduling overruns.
This is a timing-budget calculation, not an experimentally proven acquisition
threshold or a per-miss trace.

The observed 5.1 sample improved the slow result from 99 to 100, while the fast
result fell from 100 to 97. This is consistent with a dwell/acquisition tradeoff;
one finite sample per setting does not isolate the cause or establish a
statistically reliable ranking. Simply fitting a sweep inside a preamble does
not establish lossless acquisition. The 4 ms reserve is an allowance, not a
sleep; retaining it also did not produce a lossless result here.

Using genuine spare time above the 4.1 floor remains a reasonable policy to
investigate, but allocation must protect **both** profiles' acquisition/return
budgets and measured switching/scheduling overruns. Do not automatically spend
all of the reserve on longer slow visits, or treat the historic 88-symbol rule
as disproven by this nearly-lossless sample. No production defaults or warning
formula were changed by this hardware-test turn.

## Capture integrity, deployment and cleanup

- Raw capture: [profile_pair_sf8_32_results.json](../tools/hil/profile_pair_sf8_32_results.json),
  SHA-256 `3472883ade89494b64657486efd1645a04b0a3bebfd0a2e31bbf914341ad792f`.
- Independent offline checks: [profile_pair_sf8_32_validated.json](../tools/hil/profile_pair_sf8_32_validated.json).
  Checks all 800 unique sequences, 100/profile/phase, sender lifetime counts,
  actual RX frequency/SF/BW/payload identity, command counts, policies and cleanup.
- [Manifest](../tools/hil/profile_pair_sf8_32_manifest.json) records source and image hashes.
  Bundle SHA-256 `d6cb9ff36be109af8906eddbbe8a4657ca793d1351ec4bf50ebbd4909eed2bba`.
  Local bundle is `out/pair-sf8-32-20260914`; remote capture remains under
  `/home/mikec/hwtest/runs/pair-sf8-32-20260914`.
- Initial RAK USB reset returned EPROTO before any firmware write. The preserved
  [pre-write record](../tools/hil/profile_pair_sf8_32_deployment-prewrite-usb.json)
  has an empty writes list. Inspection confirmed the exact RAK serial/topology
  in its documented CDC-only bootloader PID 0x002A. Deployment was revised to
  accept that identity (and reset-time disconnect only after exact bootloader
  re-enumeration); no permission check or board-identity check was bypassed.
  The revision hash and all three successful application-only writes are saved
  in the deployment records. No bootloader or SoftDevice was replaced.
- All three radios were rebooted and live-verified idle, with test firmware left
  installed. ModemManager was restored active; gateway and host CLI services
  remained active. The preexisting memory soak remains inactive. No unrelated
  radio was flashed. Wrapper exit was zero.
- All three HIL firmware builds passed, run sequentially. The HIL host regression
  suite passed **63/63 tests**, including the new plan and packet-policy tests.
  Python compile checks and scoped `git diff --check` passed. No commit or push.

## Follow-up

The requested [4.6-chirp / 0.3 ms reserve follow-up](pair_4p6_300us_validation.md)
completed 197/200 scanning packets: slow 97/100, fast 100/100, after fresh
100/100 controls on both profiles. Its captures are separate from this run.
