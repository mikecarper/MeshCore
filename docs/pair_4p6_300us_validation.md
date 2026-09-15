# SF7/62.5 + SF8/500: 4.6 slow chirps, 0.3 ms reserve

User-requested follow-up to the [original pair test](pair_preamble32_validation.md).
Completed 2026-09-15 00:21:27–00:23:52 UTC (September 14 local time).

## Result

| Profile | Requested dwell | Stationary control | Scanning received |
| --- | ---: | ---: | ---: |
| Slow: SF7 / 62.5 kHz | 9.421 ms, approximately 4.6 chirps | 100/100 | 97/100 |
| Fast: SF8 / 500 kHz | 21.847 ms, approximately 42.67 chirps | 100/100 | 100/100 |

Full scan: **197/200 (98.5%)**, not lossless. All three misses were slow-profile
timeouts with no device error and the chip still in RX. The collector completed
every scheduled probe, with no RF retry, retune/reset on a miss, or early stop.
The two controls plus the scan contain 400 unique probes; each fixed transmitter
acknowledged exactly 200 packets. There were no USB result replays.

This is one finite sample. It does not establish that 4.6 is inherently worse
than 4.1 or better than 5.1, nor identify the cause of the three losses.

## Timing and unchanged conditions

- Reserve: 300 us; nominal switching allowance: 600 us per hop. Neither is an
  added settling delay. Nominal cycle: 9.421 + 21.847 + 1.200 = 32.468 ms.
- Actual switching across 3,957 hops: **0.582 ms mean, 0.827 ms maximum**.
  Direction means: 0.586 ms toward slow, 0.578 ms toward fast.
- Five-second RX-only switching window: 0.559 ms mean, 0.779 ms maximum.
- Non-packet-held cycle: 32.428 ms mean, 34.242 ms maximum. These samples can
  still include USB/loop scheduling overruns; the reserve is not a hard bound.
- Observed non-held dwell means: slow 9.426 ms, fast 21.855 ms; maxima 10.480
  and 23.474 ms respectively. No per-loss timing trace was collected.
- Successful-packet mean SNR: slow +12.27 dB, fast +13.47 dB; mean RSSI:
  -20.87 and -18.99 dBm. These remain strong-signal lab conditions.

Same V4 RX, RAK4631 slow TX and T1000-E/LR1110 fast TX; 909.5/910.5 MHz,
CR4/5, explicit header, CRC, 32-symbol preambles, 16-byte synthetic payloads,
-9 dBm chip TX power, normal RX gain 0x94, randomized interleaving and 10–180 ms
arrival delays. Only one transmitter sends at a time. Each TX configures once;
every packet has zero frequency/modulation writes.

The single-pass production guarded switching path uses warm XOSC and buffered
8 MHz SPI. Each hop issued exactly one frequency and one modulation command,
with one optimized RX restart. No double writes, detours, rollback, added
settling, off-channel acceptance, cache errors, RX errors or retune failures.
As before, this is a small HIL application, not full production UI/network load.

## Verification and artifacts

Only the V4 application was updated. Exact USB identity/MAC, idle ownership and
existing partition metadata were checked before writing. The nRF52 transmitter
images, bootloaders, SoftDevices, gateway and unrelated radios were not flashed.
All three test radios were rebooted and verified idle afterward; HIL firmware
was left installed. ModemManager was restored active, gateway and host CLI
services are active, and the previously paused memory soak remains inactive.

- [Raw capture](../tools/hil/profile_pair_sf8_32_4p6_300us_results.json), SHA-256
  `1c2e0eae3f304fcaeb6addfeef4f2f09a3c2bec8d78c60d3f151c952d0f6d5f5`.
- [Independent offline validation](../tools/hil/profile_pair_sf8_32_4p6_300us_validated.json)
  checks complete samples, unique sequences, sender lifetime counts, actual
  RX frequency/SF/BW/payload, command counts, policies and idle cleanup.
- [Manifest](../tools/hil/profile_pair_sf8_32_4p6_300us_manifest.json) records the
  receiver image/source hashes and prior transmitter-manifest hash. Bundle
  SHA-256 `fb7a08536ed06e36cf7d5c29babbe2affb30183de05918c4db271b98b796e579`.
- Remote records remain under
  `/home/mikec/hwtest/runs/pair-sf8-32-4p6-300us-20260914`.
- Receiver build passed. HIL host regression suite: **64/64 passed**; new plan
  guards and the exact dwell/budget arithmetic are covered. Python compile and
  scoped whitespace checks passed. Collector/wrapper exit zero.

No production timing defaults changed, and no commit or push was made.
