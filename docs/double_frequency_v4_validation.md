# Double center-frequency write: V4 transmitter / XIAO receiver

2026-09-14. **Completed bounded comparison: neither single nor duplicate
frequency writes reached 400/400. The duplicate adds about 86 us per hop.**
No production default was changed. Both radios are idle with HIL installed;
the duplicate-write toggle is off. The earlier descending-SF sweep and its
follow-up remain paused; no new exhaustive settling sweep was launched.

## Hardware and control

- RX: XIAO ESP32-S3/WIO SX1262, COM31, MAC 28:84:85:b4:09:80. Normal gain
  register 0x94, unchanged-modulation cache enabled, buffered 8 MHz SPI.
- TX: newly connected Heltec V4, COM7, MAC 44:1b:f6:6a:e8:44. Hardware probe
  found ESP32-S3, 2 MB embedded PSRAM and 16 MB flash. HIL detected KCT8103L
  FEM and reported the amplified TX path; chip RX gain 0x96.
- User explicitly authorized erasing the V4 without backup. Its full flash
  was erased and a verified HIL upload completed. XIAO's previous HIL image
  was replaced with a verified same-image A/B-capable build. No private
  backup contents were read; RAK and other boards were not touched.
- SF10/125 kHz, CR4/5, four centers 909.500/909.750/910.000/910.250 MHz,
  5.1-symbol visits (41,780 us), 32-symbol TX preamble, zero added settling,
  synthetic 16-byte packets, -9 dBm **chip request**. No extra IRQ polling.

The V4 FEM uses the repository's production detection and TX/RX control.
HIL restores RX after success or error on each direct transmit path. The
GC1109 branch (not detected here) uses bypass according to
[GC1109 table 4](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/datasheet/GC1109_EN_V0.9.2.pdf).
The detected KCT8103L uses its amplified path; antenna-port TX power was not
measured. This is not a power-matched Indicator/V4 comparison.

## Exact experiment

`scanfreqrepeat 1` makes the HIL `ExperimentalSX1262` repeat the same
SetRfFrequency (0x86) immediately after the first successful frequency call,
before modulation, packet parameters and RX restart. The second call skips
the image-calibration check, but keeps normal BUSY/status handling. Only an
identified RX-to-RX hop may repeat; ordinary setup/TX does not. Either write's
failure propagates through the existing failed profile-apply path. The toggle
defaults off at reboot and cannot change during an active scan.

Both modes use the same XIAO binary. Actual SPI command counts, not only
software policy labels, are checked against one or two 0x86 writes per hop.
The modulation cache remains on in both modes.

## RX-only timing

Six-second windows in single/double/double/single order, with reboot/clean
setup between windows. No reference packets transmitted during this phase.

| Frequency writes | Hops | Mean switch | Actual 0x86 | Actual 0x8B |
| --- | ---: | ---: | ---: | ---: |
| Single, first | 142 | 451.965 us | 142 | 0 |
| Double, first | 141 | 538.213 us | 282 | 0 |
| Double, second | 141 | 538.170 us | 282 | 0 |
| Single, second | 142 | 451.930 us | 142 | 0 |

Weighted means: **451.948 us single, 538.192 us double**: +86.244 us / +19.1%.
All 566 hops had zero retune, RX-mode and cache errors. Maximum observed
switch costs were 594 us single and 674 us double; these are samples, not
hard latency guarantees. Unheld full-cycle means were about 168.942 ms versus
169.285 ms.

## Packet checks

Single first, then double, each up to 100 packets/channel and stopping on its
first miss; a 400/400 result would have stopped all RF testing. Neither passed.

| Frequency writes | Valid before first miss | Failed packet | RF-run mean switch | Hops / 0x86 writes |
| --- | ---: | ---: | ---: | ---: |
| Single | 1 | 2, channel 2 | 452.719 us | 235 / 235 |
| Double | 4 | 5, channel 2 | 542.063 us | 255 / 510 |

Both failed packets were receiver timeouts, with TX success acknowledged,
zero device-error flags and RX mode still active. Both settings reported zero
retune, RX-mode, cache and RX errors. Ordinary processing after received
packets caused one/four modulation rewrites respectively; pure RX-only timing
above is the cleaner comparison of the duplicate cost.

All five good V4 packets reported **-19 dBm RSSI**. SNR was +9.0 dB in the
single-write sample and +8.2..+9.2 dB (mean +8.675 dB) for four double-write
samples. The previous Indicator run had 547 good packets at average -40.547
dBm RSSI, +8.311 dB SNR. Thus this V4 setup was about 21.5 dB stronger as seen
by the receiver. Hardware/antenna/placement/power differences remain
confounded; neither the Indicator nor close-range effects are proven causes.

One versus four successes before a miss is too small and stop-conditioned to
establish a reliability improvement or packet-error rate. Repeating center
frequency did not remove the observed failure at this setting. No results for
lower SFs or added settling delays are claimed for the V4. A weaker received
signal is needed before treating a transmitter swap as a clean comparison.

## Artifacts and checks

Base checkout `0e5955f889788a4317863e731b38ffe09e3d5441`, branch
`keymindCascade`, plus local uncommitted changes. RadioLib 7.7.1 pinned at
`187ef24791c3d844939b2be13a68bd890bd04e4c`, Espressif32 6.11.0 / Arduino
ESP32 2.0.17. Both builds/uploads succeeded and uploader verified written
data. The 59 selected host/native tests passed (39 collectors, ten profile,
four SX1262, five radio and one receive-mode), including FEM restore on error
and frequency-repeat scope/error propagation. Temporary build overlay removed.

Actually loaded firmware SHA-256:

- XIAO: `9f82d86ea78cfa6bc03fef4925f10c918c8b9fc7a96a7d2b565a04bd2f281288`
- V4: `2ea2ce37d9d66fb4f8ec9cf3cedfbe7d4eb6d08bfe9abe6c817e5afb04885636`

Raw evidence and byte-preserving SHA-256:

- [RX-only ABBA](../tools/hil/profile_switch_frequency_sf10_rx_only_results.json):
  `38ded968a60ff19f45618ec35d75ce46354d81f163879185e9d6ff0e68da3ae0`
- [Single-write packets](../tools/hil/profile_switch_frequency_v4_sf10_off_results.json):
  `4cadeba0d22b3ec6158ff295f1becfb57fde7ef8bf7fabbdbc6295f274619557`
- [Double-write packets](../tools/hil/profile_switch_frequency_v4_sf10_on_results.json):
  `46bea8d9c095847ef8ccf4a4ed51ab5404eb5bb87d9cf62b832c3298159d1525`
