# Separated radios, normal XIAO RX gain, and unchanged-modulation timing

Measured 2026-09-14 on `keymindCascade`, base commit
`0e5955f889788a4317863e731b38ffe09e3d5441` plus local changes.

The unchanged-modulation optimization reduces XIAO frequency-only retunes
from **548.880 to 451.897 us (17.7%)** in a same-image control. The separated
four-channel SF6 test still misses packets. Separation and disabling XIAO
boosted gain were changed together; their RF effects are not isolated.

## Settings and controls

- XIAO ESP32-S3/WIO SX1262 COM31 receiver; SenseCAP Indicator ESP32-S3 COM28
  reference transmitter. The user moved the radios apart; distance and
  attenuation were not measured. RAK4631 and Indicator RP2040 untouched.
- XIAO HIL build has `SX126X_RX_BOOSTED_GAIN=0`; actual RX gain register
  readback was **0x94** in initial info, every stationary arm, and every scan
  status. Indicator remains 0x96. Normal XIAO production default is unchanged.
- 32-symbol preamble, CR4/5, 16-byte synthetic packets, -9 dBm, buffered 8 MHz
  SPI. Four scan centers 909.500/909.750/910.000/910.250 MHz.
- No guard shortening, forced retunes through packets, RF retransmissions,
  new public N-channel protocol, or additional oscillator-delay bypass.

## Same-image, receive-only timing A/B

Each board ran four three-second windows in off/on/on/off order, rebooting
between windows. SF6/125 kHz, four channels, 2,612 us nominal dwell, no extra
IRQ tracing, no TX. A HIL-only hook invalidates just the acknowledged
modulation tuple before each baseline hop. Optimized windows use production
cache behavior. The binary and all other settings remain identical per board.

Time is entry to the production retune through BUSY-low observation. It does
not include the listening dwell or full application scheduling, and is not an
independent RF settling measurement. Unlike the older mixed-bandwidth tests,
this frequency-only workload can skip 0x8B.

| Board / policy | Hops | Mean | Min–max | Actual 0x86 / 0x8B writes |
| --- | ---: | ---: | ---: | ---: |
| XIAO, always write | 1,898 | 548.880 us | 535–663 us | 1,898 / 1,898 |
| XIAO, reuse unchanged tuple | 1,957 | 451.897 us | 441–580 us | 1,957 / 0 |
| Indicator, always write | 564 | 8,263.438 us | 8,228–8,428 us | 564 / 564 |
| Indicator, reuse unchanged tuple | 602 | 7,601.193 us | 7,583–7,762 us | 602 / 0 |

Pooled means are sample-weighted from reported per-window means. Savings are
96.983 us (17.7%) for XIAO and 662.246 us (8.0%) for Indicator. All 5,021 hops
passed RX-mode/software-cache checks with zero retune errors or deferrals.
Counters observe outgoing SPI opcodes, not merely which software branch ran.
Initial setup before counter snapshots is excluded. The Indicator remains
above the common 6 ms switching allowance; no common timing budget changed.

## Stationary RF controls

Full initialization before every case, fixed RX at 910 MHz, ordinary RX with
fast reuse disabled, shuffled offsets, 450 ms capture windows. Method matches
the [earlier investigation](preamble_detection_investigation.md).

| TX offset | SF6 preamble windows | SF8 preamble windows |
| --- | ---: | ---: |
| Silent | 0/4 | 0/8 |
| On frequency | 4/4 | 8/8 |
| -250 kHz | 0/4 | 1/8 |
| +250 kHz | 0/4 | 1/8 |
| -500 kHz | 0/4 | 2/8 |
| +500 kHz | 0/4 | 0/8 |
| -1,000 kHz | 0/4 | 0/8 |
| +1,000 kHz | 0/4 | 0/8 |

All 12 on-frequency probes delivered valid payloads. No off-frequency header
or valid payload, silent preamble, device/read error, foreign valid payload,
or trace overflow occurred. SF6 off-channel detections changed from 1/24 to
0/24; SF8 from 32/48 to 4/48. These are small bounded samples, not sensitivity
or packet-error-rate estimates. Signal strength (RSSI) sampled on-frequency
was -41 to -40 dBm at SF6 and -42 dBm at SF8: the signal remains strong, but
these are not calibrated attenuation measurements. Improvement cannot be
attributed solely to separation or solely to normal gain.

## Four-channel SF6/125, 5.1-symbol visits

Same image, baseline then optimized, reboot both boards between runs. Each
requested 100 packets/channel with varied unsynchronized arrival timing and
stopped at its first miss. Extra nominal 64 us IRQ polling was enabled here,
unlike the receive-only timing windows. No higher channel count was tested.

| Policy | Received before first miss | Failed channel | Timed hops | Mean retune |
| --- | ---: | --- | ---: | ---: |
| Always write 0x8B | 24, then probe 25 missed | 1 / 909.750 MHz | 4,746 | 551.253 us |
| Reuse unchanged tuple | 51, then probe 52 missed | 1 / 909.750 MHz | 7,748 | 455.157 us |

Optimized RF run: 7,697 hops omitted 0x8B, averaging 453.081 us; 51 hops
reapplied it after received packets, averaging 768.529 us. These are different
RX lifecycle paths, not a pure 315 us command-cost comparison. Both runs had
one frequency write per hop and no RX-mode/cache/retune errors. Successful
packets read -42 to -40 dBm with SNR 10.0–11.5 dB. Neither met 100/100 on every
channel. More packets before the first miss does not establish a reliable
improvement in packet-error rate.

The baseline miss captured a target-channel preamble at 176,194 us after
arming, 1,830 us after a hop onto that channel. A guard held it there; no
header or packet followed. An IRQ clear was observed at 195,025 us and the
next hop completed at 195,240 us. This is **not** the earlier SF8 wrong-channel
hold: the preamble was observed on the intended channel. Its RF source and
reason for failing to progress remain unproven.

The optimized miss captured 3,256 hops over ten seconds with **no observed
nonzero IRQ and no guard hold**, reproducing the pre-acquisition symptom of
the earlier SF6 miss. Target-channel revisit intervals were 12.249–12.324 ms
(mean 12.286 ms), shorter than the 16.384 ms programmed preamble, yet no packet
was acquired during those short individual visits. This does not establish
that additional post-retune settling is needed. Trace buffers did not overflow.
TX returned success for both failed probes; the MCU clocks are not synchronized
and there was no independent RF capture.

## Provenance

RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`, Espressif32
6.11.0 / Arduino ESP32 2.0.17. Both targets built and uploads were verified.
These captures precede the subsequent SF5/250 bandwidth-selector addition.

Tested firmware SHA-256:

- XIAO: `8aa79da891409c9e600ce14c1d83a8d73c3198225a2aaf799fc884247e882fab`
- Indicator: `868946d0beca882d8f215c1556c0c19bf41908baac6620dc5632c3fca85b5feb`

Raw files in `tools/hil`, preserved byte-for-byte:

| File | SHA-256 |
| --- | --- |
| [XIAO timing](../tools/hil/profile_switch_same_modulation_xiao_results.json) | `45e7a640323a3c9028b6c92a5128e51307db1bd94a4846e8744d3c3ec098adfd` |
| [Indicator timing](../tools/hil/profile_switch_same_modulation_indicator_results.json) | `f0a85826f500b3a9173bf22c6207fbfba3b6cb17f3d70b849a9fe7621ae3dfc5` |
| [Stationary SF6](../tools/hil/preamble_stationary_sf6_separated_normal_results.json) | `1d981e5af41eabbc43f92641813bf8f7c5e7015439f5ba53ce0467fe6477d3f3` |
| [Stationary SF8](../tools/hil/preamble_stationary_sf8_separated_normal_results.json) | `36e63da7435d0a16f61db7db7730893de392d028feca7a96960fd17ae1fa624b` |
| [SF6 baseline scan](../tools/hil/sf6_125_5p1_separated_normal_baseline_results.json) | `cbfd200170891b334ef3958479694c6e318146993b94073eb9aee981f97fde97` |
| [SF6 optimized scan](../tools/hil/sf6_125_5p1_separated_normal_optimized_results.json) | `d88a621ef9aa358ce52bb3efb6aa9c4bcb77c681af20b2c66e4aebf3721e17ca` |

Fresh complete 8 MiB backups were saved and separately verified in the private
current-user backup directory before testing. Restores had already started
when the user requested leaving HIL firmware installed; those in-flight writes
and verification finished safely. Both boards were subsequently loaded with
the newer SF5/250 HIL build for the user's next sweep. Do not restore them
again unless requested. No private backup contents were added to Git.
