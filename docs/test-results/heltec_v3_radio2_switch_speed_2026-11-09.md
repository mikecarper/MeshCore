<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_v3_radio2_switch_speed_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Heltec V3 radio2 switching speed — 9 November 2026

The V3 now uses the existing resident SX1262 retune path previously enabled on
V4. This makes switching **23–34% faster** on the tested Full Companion without
PSRAM or capacity reductions. The radio commands, receive checks and rollback
handling are unchanged.

A [later command/SPI residency experiment](heltec_v3_radio_command_speed_2026-11-09.md)
reduces the quiet-loop means further to 383/499 us at 240 MHz and 570/744 us
at 80 MHz. It remains experimental because the RF qualification failed;
that report includes the packet-loss results and limits.

## Measured results

Physical Heltec V3, 8 MHz SPI, default fast-driver settings. Values are mean
microseconds from admission through receive restart, using two 20-second quiet
windows for each configuration.

| CPU | Change | Before | After | Improvement |
|---|---|---:|---:|---:|
| 240 MHz | Frequency only | 887 us | **587 us** | 34% |
| 240 MHz | Frequency and modem | 1,023 us | **712 us** | 30% |
| 80 MHz | Frequency only | 1,068 us | **781 us** | 27% |
| 80 MHz | Frequency and modem | 1,259 us | **964 us** | 23% |

There were no switch-error increments. At 240 MHz, instruction-cache misses
per switch fell from about 326 to 177 for frequency changes and 337 to 182 for
modem changes. The counter covers both CPUs, so it is supporting evidence,
not per-task attribution. Both builds use colocated Xtensa literals; the
candidate also moves existing queue storage. This compares the complete change,
not an isolated estimate of code placement alone.

Timing used 906.525/907.525 MHz. The frequency-only test used BW62.5/SF7/CR5 with
preamble32 on both profiles. The mixed test used BW500/SF5/CR5/preamble200 on the
secondary. CPU speed was selected explicitly; battery current was not measured.
Both timing builds used `agc 16` and `agc.old auto`. These are quiet-loop averages,
not worst-case latency guarantees.

## Keeping the existing capacities

The V3 has no PSRAM. The resident path adds 5,364 bytes of instruction RAM.
Full Companion moves another 32 existing frames (5,664 bytes) into RTC SLOW,
retaining **100 contacts, all 256 queue frames, and independent OTA storage**.
The queue remains complete during OTA and is cleared normally on boot.

Full Repeater moves 48 existing advert-history records (6,720 bytes) into RTC
FAST/SLOW, retaining **all 128 records**. Full Room needs no storage change.
These are the existing CPU-only RTC storage implementations; SDK/DMA buffers
remain in ordinary memory.

| Normal Full image | Application / slot bytes | Internal RAM available / required |
|---|---:|---:|
| Companion | 2,118,824 / 3,342,336 | 190,632 / 185,632 |
| Combined MQTT Repeater | 2,196,072 / 3,342,336 | 213,120 / 208,820 |
| Combined MQTT Room | 2,166,344 / 3,342,336 | 220,064 / 202,528 |

The required RAM includes the existing transient margin. These build checks do
not qualify maximum simultaneous WiFi/BLE/MQTT traffic.

## Validation

- Normal firmware received **120/120** unique RF probes: 30 on each profile at
  both 80 and 240 MHz, alternating 40/160-byte payloads. No retries, duplicates,
  corruption or switch errors; per-profile RX counters confirm delivery.
- The cumulative switch maximum reached **3,602 us at 80 MHz** during RF traffic.
  The later 240 MHz windows inherited that maximum. The average-speed gain does
  not eliminate occasional scheduling/interrupt latency.
- Full Companion, combined MQTT Repeater and combined MQTT Room pass capability,
  partition-size and RAM gates. **40 focused host tests** and the full
  build-profile suite pass, including full-queue OTA and RTC ownership/reset
  behavior. Repeater/Room were build-checked, not physically qualified here.
- Only the application partition was written; the existing partition table
  matched before every write and all three application readbacks matched.
  The V3 finishes on normal `v1.18.0.2-v3-fast-9303dc51`, with original identity
  and saved settings restored. The XIAO S3 witness settings were also restored.
  The reserved RAK and T1000 recovery devices were not touched. The old firmware
  had reported `agc.old 0`; after testing, that effective setting was explicitly
  restored because the current firmware defaults to `auto` without an override.

Source change: `9303dc510`. Baseline source: `5ed23fdd6`. Detailed measurements,
image hashes and RF results are in the [machine-readable report](heltec_v3_radio2_switch_speed_2026-11-09.safe.json).
