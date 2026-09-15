# Four fixed transmitters: longer dwell repeats

Same physical MercerMesh fixture and installed firmware as
[the completed 5.1-symbol run](four_fixed_tx_single_pass_validation.md):
RAK4631, Heltec T096, MeshTower V2 and T1000-E fixed transmitters; Heltec V4 RX.
SF10 / BW125 kHz / CR4/5, four channels spaced 1 MHz apart, 32-symbol preamble,
normal SX1262 RX gain, -9 dBm chip TX setting, zero added settling and one
frequency write per hop. No firmware was rebuilt or reflashed for these repeats.

## 6.1-symbol run: user-stopped, incomplete sample

Started 2026-09-14 22:44:47 UTC. Dwell was 49,972 us per channel.
All four stationary controls passed 100/100 again.

The user requested stopping this run and proceeding to 7.7 symbols. The
collector was interrupted only after verifying its PID, command line and owner.
At the last saved checkpoint, **342/348 scored scan packets were received**
(98.28%), with six timeouts:

| TX / channel | Scored attempts | Received | Timeouts |
| --- | ---: | ---: | ---: |
| RAK4631 / 0 | 87 | 87 | 0 |
| Heltec T096 / 1 | 87 | 86 | 1 |
| MeshTower V2 / 2 | 87 | 85 | 2 |
| T1000-E / 3 | 87 | 84 | 3 |

This is not a completed 400-packet run or a validated full-run timing capture.
An interrupted in-flight packet can exist beyond the last saved checkpoint;
it is not scored as another success or failure. No final switch timing was
collected before reboot. The raw capture remains incomplete, and the separate
user-stop record documents why. All five radios were then independently
verified idle, and ModemManager was restored. Gateway services stayed active;
the V4 memory-soak service stayed inactive.

- [Partial raw capture](../tools/hil/profile_four_tx_sf10_6p1_results.json)
- [User stop record](../tools/hil/profile_four_tx_sf10_6p1_user-stop.json)
- [Verified cleanup](../tools/hil/profile_four_tx_sf10_6p1_cleanup.json)
- [Run provenance](../tools/hil/profile_four_tx_sf10_6p1_launch.json)

Partial raw capture SHA-256:
`ad199124930da19fe56c78a4ea8280a6d94b95ef408589b19a9e165155dbf7fd`.

## 7.7-symbol run

Launched 2026-09-14 22:58:14 UTC; collector ran from 22:58:15 to 23:11:45 UTC
with 63,079 us visits. All four fixed-channel controls passed 100/100.
The full scan completed with **395/400 received (98.75%)**, five timeouts and
zero wrong-channel or invalid payloads. Thus **7.7 symbols did not achieve a
400/400 pass**.

| TX / channel | Scanning result | Timeouts | Mean received RSSI (dBm) | Mean received SNR (dB) |
| --- | ---: | ---: | ---: | ---: |
| RAK4631 / 0 | 100/100 | 0 | -21.05 | +8.14 |
| Heltec T096 / 1 | 99/100 | 1 | -4.35 | +8.98 |
| MeshTower V2 / 2 | 97/100 | 3 | -44.86 | +8.63 |
| T1000-E / 3 | 99/100 | 1 | -18.56 | +8.26 |

All 800 baseline/scan TX sequences were unique, all transmit calls succeeded,
and every TX packet reported zero frequency and modulation configuration writes.
There were zero packet-response transport replays. All four final TX lifetime
checks reported 200 packets and a prepared, non-failed transmitter.

The five timeouts occurred at scan attempts 158 (T1000-E), 184 (T096), and
200, 293, 314 (MeshTower). Each reported RX mode 1 and zero device errors.
Final RX failure, RX-mode-error, cache-error and receive-error counters were zero.

### Measured switch and sweep timing

| Hop population | Count | Minimum (us) | Mean (us) | Maximum (us) |
| --- | ---: | ---: | ---: | ---: |
| All single-pass switches | 5,046 | 438 | 524.146 | 804 |
| Cached modulation; no 0x8B | 4,651 | 438 | 502.147 | 686 |
| Post-reception modulation refresh | 395 | 760 | 783.180 | 804 |

Exactly 5,046 RF-frequency commands and 5,046 optimized RX resumes were counted,
with one retune pass and zero second passes/detours. The 395 modulation writes
match the 395 post-reception cache refreshes. This is the same cache behavior
as the earlier run, not double-setting the radio. The slightly higher overall
mean than the 5.1 run partly reflects a larger fraction of refresh hops.

Across 860 measured idle cycles, the four-channel sweep averaged **254.351 ms**,
minimum 254.208 ms and maximum **255.482 ms**. Relative to the 262.144 ms
programmed preamble, that leaves 7.793 ms at the mean and 6.662 ms at the maximum.
Nominal dwell was 63.079 ms; measured idle dwell averaged 63.084 ms. Packet-held
visits include payload/guard time and are not comparable to idle cycles.

The 7.7 run received three more packets than the earlier 5.1 run (395 versus
392 out of 400). This small, sequential comparison does not establish a reliable
improvement or a lossless dwell threshold. The 6.1 result is partial and must
not be treated as an equal-size completed comparison.

### Evidence and cleanup

Unlike the original 5.1 run's overly strict host assertion, this collector
completed normally with `complete: true` and exit code 0. Independent offline
validation also passed. The same collector SHA-256 was used for both repeats:
`cceb8a570f4aedeaf45d28b4b4590b23df3c1efd490c8e8e9cd74a23ba00024f`.
The copied deployment/manifest files are provenance for reused firmware, not
records of a new flash operation. Host options and acknowledged dwell match.

- [Completed raw capture](../tools/hil/profile_four_tx_sf10_7p7_results.json)
- [Validated summary](../tools/hil/profile_four_tx_sf10_7p7_validated.json)
- [Run provenance](../tools/hil/profile_four_tx_sf10_7p7_launch.json)
- [Cleanup readbacks and warnings](../tools/hil/profile_four_tx_sf10_7p7_cleanup.json)

Raw capture SHA-256:
`5e3f9a80af1384c3072303428b75c8550ac42d2ac95bd1234bf92cb0aea625e8`.

The cleanup wrapper retained three errors from its reboot phase: the T096
primary USB interface was temporarily missing/ambiguous, and attempts to open
the MeshTower/T1000-E ports returned permission denied. Its subsequent final
readbacks nevertheless succeeded for all five radios: each TX was unprepared,
non-failed, packet count zero and autonomous TX disabled; RX scanning was
inactive with zero channels. No further radio access or permission changes
were attempted after those results returned. The wrapper's `verified_idle`
flag remains **false** because it retains any earlier cleanup error, and the
outer operation returned nonzero for that reason. These warnings happened
after the complete RF sample; they are not counted as RF packet losses or
silently removed from the record.

ModemManager was restored active; gateway services remained active. The V4
memory-soak service remains inactive with test firmware installed. No firmware
restore, commit, push or pull was performed. Previous raw captures are unchanged.

## Dwell budget

One chirp at SF10/125 lasts 8.192 ms. For the experiment's nominal budget of
one complete four-channel idle sweep inside 32 programmed preamble symbols:

`maximum dwell symbols = 32 / 4 - switching_ms / 8.192`.

The 5.1-symbol run's mean switch (0.513980 ms) gives about 7.937 symbols;
its maximum switch (0.801 ms) gives about 7.902 symbols, before other loop
overhead. These are timing ceilings, not proven lossless dwell limits or a
complete acquisition model. Near the ceiling there is little timing margin.
At 7.7 symbols, four nominal dwell-plus-average-switch intervals total about
254.4 ms, versus a 262.144 ms programmed preamble.

The host keeps the same seed and arrival-randomization rule, spanning about
two nominal scan cycles; absolute arrival pauses therefore scale with dwell.
The fixed-channel controls, sample counts, switching policy and firmware stay
unchanged. As before, external PA/LNA paths and unrelated RF traffic mean this
is a finite bench comparison, not a calibrated sensitivity or interference test.
