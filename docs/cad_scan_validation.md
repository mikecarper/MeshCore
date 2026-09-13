# SX1262 channel-scanning hardware results

Hardware test on September 12, 2026 (America/Los_Angeles). Heltec V4 transmitter; XIAO ESP32-S3 with Wio SX1262 receiver. Both used standalone diagnostic firmware.

The best tested scan setting was **four channels with four-symbol RX-preamble windows**. Three-symbol windows were unreliable even in the single-channel re-arm control. In the longer four-symbol runs, four channels received 80/80 packets, five received 79/80, and six received 52/60. These are measured limits of this implementation and test setup, not universal SX1262 channel-count limits.

## Configuration and counting

SF5, 125 kHz, coding rate 4/5, explicit header, payload CRC, and 32 preamble symbols. The programmed preamble is 8.192 ms. Channels start at 909.5 MHz and increase in 1 MHz steps; tests use the first N channels, up to 920.5 MHz. Test packets are transmitted individually with varied timing relative to the scanner. There are no overlapping transmitters in this experiment.

A success requires a CRC-valid packet with the expected sequence, channel, length, and test payload. Every included transmission has its own successful transmitter acknowledgement; delayed serial acknowledgements are recovered by sequence, without retransmitting. The one exploratory RX scan trial with shifted acknowledgements is excluded.

## Completed trials

| Trial | Method/window | Channels | Payload bytes | Received / sent | Mean complete scan |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline-fixed | Fixed-channel RX | 4 | 64 | 16 / 16 | — |
| cad-standard | CAD + software RX | 4 | 64 | 7 / 32 | 14.469 ms |
| cad-auto | CAD + automatic RX | 4 | 64 | 16 / 32 | 14.468 ms |
| rxfast-4 | rxfast 4 | 4 | 64 | 32 / 32 | 6.100 ms |
| rxfast-4-maxpayload | rxfast 4 | 4 | 255 | 35 / 35 | 6.098 ms |
| matrix-cad1-1 | cad 1 | 1 | 64 | 7 / 8 | 2.643 ms |
| matrix-cad1-10 | cad 1 | 10 | 64 | 2 / 20 | 28.389 ms |
| matrix-cad1-11 | cad 1 | 11 | 64 | 5 / 22 | 31.297 ms |
| matrix-cad1-12 | cad 1 | 12 | 64 | 3 / 24 | 34.145 ms |
| matrix-cad2-10 | cad 2 | 10 | 64 | 3 / 40 | 31.040 ms |
| matrix-fixed-12 | Fixed-channel RX | 12 | 64 | 24 / 24 | — |
| matrix-rx1-10 | rxfast 1 | 10 | 64 | 0 / 20 | 7.555 ms |
| matrix-rx1-11 | rxfast 1 | 11 | 64 | 0 / 22 | 8.316 ms |
| matrix-rx1-12 | rxfast 1 | 12 | 64 | 0 / 24 | 9.072 ms |
| matrix-rx2-10 | rxfast 2 | 10 | 64 | 0 / 40 | 10.133 ms |
| matrix-rx3-7 | rxfast 3 | 7 | 64 | 2 / 35 | 8.892 ms |
| matrix-rx3-8 | rxfast 3 | 8 | 64 | 1 / 40 | 10.160 ms |
| matrix-rx4-5 | rxfast 4 | 5 | 64 | 39 / 40 | 7.630 ms |
| boundary-rx3-1 | rxfast 3 | 1 | 64 | 4 / 24 | 1.268 ms |
| boundary-rx3-4 | rxfast 3 | 4 | 64 | 2 / 24 | 5.080 ms |
| boundary-rx3-5 | rxfast 3 | 5 | 64 | 0 / 25 | 6.350 ms |
| boundary-rx3-6 | rxfast 3 | 6 | 64 | 2 / 30 | 7.621 ms |
| boundary-rx4-4 | rxfast 4 | 4 | 64 | 80 / 80 | 6.104 ms |
| boundary-rx4-5 | rxfast 4 | 5 | 64 | 79 / 80 | 7.630 ms |
| boundary-rx4-6 | rxfast 4 | 6 | 64 | 52 / 60 | 9.158 ms |

`rxfast N` means a continuous-RX preamble observation window of N symbols, with minimal reconfiguration between frequencies. It is not CAD. `cad N` uses the SX1262 CAD detector with N symbols and automatic handoff to RX. CAD has no three-symbol mode.

Scan times describe uninterrupted sweeps. When reception starts, the radio stays on that frequency through the packet and cannot hear packets on the other frequencies. Serial status requests can add occasional timing outliers.

The maximum-payload trial ended after 35 confirmed transmissions when a serial acknowledgement was delayed. Its last TX acknowledgement and matching RX were subsequently recovered from the serial logs; all 35 were received. It is not a completed 64-packet trial.

## Interpretation and limits

The initial four-channel fast-RX test received 32/32 64-byte packets and 35/35 255-byte packets. That is a successful strong-signal bench result, not a guarantee of zero loss. Ordinary four-symbol CAD took approximately 14.47 ms to cover four frequencies and was unreliable with an 8.192 ms preamble.

Reducing the observation window also reduces the time available for the modem to recognize a preamble. Dividing preamble duration by scan duration alone overestimates the number of usable channels. One- and two-symbol RX windows produced no preamble detections in the tested high-channel-count cases.

The three-symbol single-channel control repeatedly re-arms RX, just as a scan would, and received only 4/24 packets. It is distinct from continuous fixed-channel reception, which passed its baseline. This isolates a detection-window limitation even without a long multi-channel sweep.

CAD detection peak 18 and minimum 10 were held constant. These are the datasheet four-symbol SF5 thresholds; the one- and two-symbol CAD tests do not establish optimized thresholds or weak-signal sensitivity. CAD activity followed by no decoded packet can be a late packet detection, not necessarily a noise false positive.

Received signals were strong. These tests do not establish weak-signal range, adjacent-channel rejection, simultaneous-packet capacity, production firmware scheduling performance, power consumption, FCC compliance, or months of uptime.

## Reproduction and evidence

See [the test harness](../tools/hil/cad_scan/README.md). Compact validated results, timing counters, and final firmware/source hashes are in [cad_scan_results.json](../tools/hil/cad_scan/cad_scan_results.json). Detailed sequence-level logs and private lab transport helpers are retained locally under `out/cad-scan/`.
