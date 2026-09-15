# SF10/125 full samples: 10 Hz versus 100 Hz first-pass detour

2026-09-14. **Both 400-packet samples completed: 100 attempts on each of four
channels, with no RF retries or early stop on misses. Payload loss was
64/400 (16.0%) at 10 Hz and 81/400 (20.25%) at 100 Hz. Strict same-channel
reception was almost identical: 230/400 versus 231/400. The larger offset
showed no improvement in this comparison.**

These are two sequential finite bench samples, not proof that the offset
caused the difference in payload loss. The earlier stop-first tests could
not provide this error-rate estimate and are not combined with these counts.

## Settings and method

- RX: XIAO ESP32-S3 / Wio SX1262, COM31, 28:84:85:B4:09:80.
- TX: XIAO nRF52840 / Wio SX1262, COM15, B35E71C1C3726CE7.
- Uniform SF10, BW125 kHz, nominal CR4/5; 909.5, 910.5, 911.5, 912.5 MHz.
- 32-symbol preamble; 5.1-symbol visits (41,780 us); zero added settling.
- Normal RX gain 0x94, -9 dBm chip TX request, 16-byte CHS1 payloads.
- Warm XOSC, bulk SPI, fast RX; TCXO setting 1600 us. No added IRQ tracing.
- Two full retunes per hop. First: selected positive RF offset, CR4/6.
  Second: exact original RF command, CR4/5. Duplicate frequency writes
  remain inside each pass: four RF commands, two modulation writes and
  two optimized RX starts per completed hop.
- Requested 10 Hz = +10 integer RF-word steps, nominal 9.5367431640625 Hz.
  Requested 100 Hz = +105 steps, nominal 100.13580322265625 Hz. Neither
  depends on a float-MHz addition. This is programmed, not independently
  spectrum-measured, frequency.
- Same receiver image for both runs; only the requested offset changed.
  Order: 10 Hz, reboot, 100 Hz. TX firmware was unchanged.
- Seed 606125: shuffled channel order each round and varied arrival pauses.
  Each run has 400 unique probe sequences and exactly 100 TXs per channel.
- Existing 10-second expectation timeout retained. A miss does not reset
  the receiver, tune it toward TX, reset the visit clock or clear counters.
  Hardware/guard/command failures still abort rather than become RF losses.

The complete comparison ran from 21:09:11 to 21:42:39 UTC (14:09–14:42 PDT),
about 33 minutes. The 10 Hz sample took about 15.4 minutes; 100 Hz about 18.0.
The extra elapsed time is largely the additional ten-second timeouts.

## Overall results

"Payload loss" means no intact expected payload was delivered within the
expectation window, regardless of reported RX channel. "Strict error" also
counts intact payloads received while the scanner reports another channel.

| Requested offset | Attempts | Correct-channel delivery | Intact wrong-channel delivery | Timeouts / payload loss | Strict errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10 Hz | 400 | 230 (57.5%) | 106 (26.5%) | 64 (16.0%) | 170 (42.5%) |
| 100 Hz | 400 | 231 (57.75%) | 88 (22.0%) | 81 (20.25%) | 169 (42.25%) |

All scored delivery failures were timeouts. The receiver also counted six
packet-read errors at 10 Hz and eleven at 100 Hz. The error codes were not
captured, so these cannot be identified specifically as CRC failures or
mapped one-to-one to timeout probes. "Zero invalid payloads" in the summary
does not mean there were no packet-read/CRC errors before timeout.

## Per-channel results

Each cell uses exactly 100 attempts. Loss counts therefore also equal percent.

| Center (MHz) | 10 Hz payload loss | 100 Hz payload loss | 10 Hz strict errors | 100 Hz strict errors |
| --- | ---: | ---: | ---: | ---: |
| 909.5 | 10% | 11% | 26% | 25% |
| 910.5 | 23% | 23% | 51% | 45% |
| 911.5 | 19% | 26% | 48% | 55% |
| 912.5 | 12% | 21% | 45% | 44% |

The extra 17 payload losses at 100 Hz occurred on channels 0 (+1), 2 (+7)
and 3 (+9). Correct-channel deliveries differed by only one packet overall.
Neither offset eliminated wrong-channel acquisition/delivery or timeouts.
This comparison does not isolate the underlying RF failure mechanism.

## Switching and signal measurements

| Metric | 10 Hz | 100 Hz |
| --- | ---: | ---: |
| Completed hops | 16,992 | 20,676 |
| Mean complete two-pass switch | 1263.765 us | 1262.551 us |
| Maximum complete switch | 1533 us | 1532 us |
| Mean first pass | 660.712 us | 659.458 us |
| Mean second pass | 592.344 us | 592.406 us |
| RF commands | 67,968 | 82,704 |
| Modulation commands / RX starts | 33,984 / 33,984 | 41,352 / 41,352 |
| Maximum unheld four-channel cycle | 174,139 us | 173,722 us |
| Maximum held channel visit | 590,757 us | 640,492 us |
| Packet-read errors | 6 | 11 |

Unheld cycles fit within the 262,144 us programmed preamble; packet-related
held visits can exceed it. Aggregate timing without IRQ traces does not prove
which hold caused any particular timeout. The approximately 1.2 us difference
in mean switching time is negligible here and does not explain the losses.

All completed hops passed the internal first-offset/final-correction and CR
command checks: 16,992/16,992 and 20,676/20,676, `detour.bad=0`. Second-pass
blocks, retune failures, mode errors and cache errors were zero. Counts match
four RF writes, two modulation writes and two RX starts per completed hop.

Telemetry limitation: the diagnostic `detour.first_rf/first_mod` fields are
updated on a guard-deferred first attempt as well as an applied attempt, while
`final_rf/final_mod` retain the last completed second pass. Thus the final
snapshot's displayed pair can be from different attempts and appears equal
in these captures. Those fields must not be interpreted as the last verified
pair. Verification itself runs only after two applied passes and increments
`detour.n` only then; its per-hop comparisons, zero failures and matching
counts establish the commanded sequence. Original capture bytes are retained.

| Packet group | 10 Hz mean RSSI / SNR | 100 Hz mean RSSI / SNR |
| --- | --- | --- |
| Correct-channel delivery | -34.05 dBm / +8.56 dB | -34.15 dBm / +8.62 dB |
| Intact wrong-channel delivery | -105.18 dBm / -13.75 dB | -105.22 dBm / -13.62 dB |

The markedly weaker wrong-channel readings are observations, not proof of a
specific filter/image/overload mechanism.

## Implementation and verification

The HIL scanner gained `scancontinue 1`, default off, so its ordinary completion
and timeout accounting can leave scanning active. The host gained bounded
`--continue-on-miss --max-channels 4` with separate delivery/strict summaries.
`scandetouroffset 10|100` and `--first-pass-offset-hz 10|100` select the integer
offset while stopped. Production radio defaults and receive guards were not
changed. The controller is `tools/hil/profile_switch_offset_compare.py`.

23 native profile tests and 51 host tests passed. Coverage includes continuing
through all 400 attempts without restart/retry, balanced channels, separated
error definitions, default first-miss behavior, aborting fixture failures,
both integer offsets and exact restoration. Build/upload succeeded with hash
verification: RAM 94,948 bytes, reported flash 370,213 bytes.

There was one cached USB RX-result replay in each sample, with no RF retransmit.
All TX commands returned success. Host/device strict receive and miss counters
matched; both captures are complete under `fixed_sample_complete`. The master
manifest is complete under `both_400_packet_samples_complete`, with no fixture
or cleanup error.

Both boards rebooted idle afterward. RX confirmed scanner inactive, zero
channels, one-pass default, detour/continue disabled and normal gain 0x94.
TX confirmed no autonomous TX. HIL remains installed; RAK COM29 was not opened.
The temporary PlatformIO overlay was removed. No background collector remains.
This work made no commits or pushes. Existing unrelated changes were preserved.

## Provenance

- Manifest: `tools/hil/profile_switch_sf10_10_vs_100hz_full_results.json`
  SHA-256 `40ef9d70991b585abe6b99b3dc4878829412d55e0e8b5a984576b00db0326981`
- 10 Hz capture: `tools/hil/profile_switch_sf10_10_vs_100hz_full_results_10hz.json`
  SHA-256 `889a7afeb980ec8459fccb8da5a7c496feebd433da8715f84181f02c1da613aa`
- 100 Hz capture: `tools/hil/profile_switch_sf10_10_vs_100hz_full_results_100hz.json`
  SHA-256 `0b54a057c07f9da0b03f585aeffee960ffcfa2ecb5383914554697ca250aa071`
- RX BIN SHA-256:
  `bddf779208cbfd24478d39d3075f3f7537b7c9275453492748a349f3ec15b630`
- Unchanged TX ZIP SHA-256 from the preceding validated upload:
  `915d507d0feec1712fad2bd4be62c2fa8d769d11400711de0da1cf98a50a4b36`

Source: live keymindCascade working tree at HEAD
`2b5a6f209835f0f67ed73fee78d36447a4566ce6`, plus existing dirty changes and
these HIL additions. The RX image hash identifies the exact tested build.
Earlier captures were not overwritten.
