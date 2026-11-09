<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/rak3401_radio_speed_experiments_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# RAK3401 switching experiments, 9 November 2026

No experimental driver change was promoted. The current Full Companion
averaged **0.299 ms for frequency-only switches** and **0.433 ms when bandwidth
and spreading factor also changed**. The five experiments below either slowed
it down or produced too little benefit to justify a separate SPI implementation.

## Measured results

All builds used source `f30a0a25dc70e791e9ceb22fe4abe5d43f6d2c83`, a 64 MHz
nRF52840, buffered 8 MHz SPI1/SPIM2, AGC 16, power saving off, USB connected,
and the same DWT instrumentation. These are complete RX-to-RX switch means,
including admission and restarting receive, not PLL-lock times.

| Build | Frequency only, µs | Changed modem, µs | Samples, frequency / modem |
| --- | ---: | ---: | ---: |
| Existing driver | 298.578 | 432.647 | 4,408 / 4,508 |
| Selected radio functions placed in RAM | 326.154 | 459.043 | 4,630 / 4,404 |
| Selected radio functions compiled with `-O2`, no inline | 313.347 | 440.707 | 4,628 / 4,404 |
| Four bounded BUSY polls before yielding | 313.814 | 441.175 | 4,482 / 4,416 |
| Direct SPIM2 transfer inside existing SPI transactions | 297.272 | 425.130 | 4,482 / 4,418 |
| Two-entry LoRa airtime calculation cache | 306.605 | 433.070 | 4,484 / 4,420 |

All **53,684 measured switches completed without a switch-command failure**.
Each result combines both directions and two 33-second windows. Temporary
profiles used 906.525 and 907.525 MHz. Both used BW 62.5 kHz, SF7, CR5,
preamble 32 for frequency-only testing. The changed-modem secondary used
BW 500 kHz, SF5, CR5, preamble 200.

These averages are not maximum guarantees. The existing driver's cumulative
worst samples were 2.550 ms and 2.634 ms, dominated by the full receive setup
on initial entry. Its frequency-only histogram put 4,358/4,408 switches in
256–319 µs; the changed-modem histogram put 4,420/4,508 in 384–447 µs.
Interrupts, cold entry and full receive setup still produce longer samples.
No sub-0.5 ms maximum claim is made.

## What the experiments established

- RAM placement consumed another 4,352 bytes and was slower. Instruction
  caching was already enabled; copying the ESP32 placement approach did not
  help this nRF52840 build.
- Selective optimization and bounded BUSY spinning also lost against the
  existing build. These comparisons include any resulting code-layout changes.
- Direct SPIM2 saved only 1.31 µs (0.44%) for frequency-only switches and
  7.52 µs (1.74%) for changed-modem switches. This private experiment bypassed
  the normal nrfx transfer entry. It was not qualified for faults, Bluetooth
  load or power management and was removed.
- The airtime cache used 48 bytes of RAM and reduced its measured calculation
  stage by about 1–2 µs. Whole-switch timing did not improve. Its host check
  covered alternating profiles, TX-length eviction and every calculation input;
  neither the cache nor its experimental tests were retained in production.

The current driver already has buffered SPI and acknowledged modem/IQ/packet
caches. We retained its command checks, calibration and recovery behavior.
No SPI overclock, interrupt masking or new runtime setting was enabled.
The [earlier 16 MHz readback failures](rak3401_radio2_fast_switch_2026-10-23.md#why-16-mhz-is-not-enabled)
were not rerun; this campaign stayed at 8 MHz.

## Normal-firmware reception check

The RAK was restored to normal Full Companion from `f30a0a25`, with no timing
instrumentation or experimental changes. A XIAO ESP32-S3 source sent 120 unique,
single-shot packets at -9 dBm, alternating 40- and 160-byte payloads. No missing
packet was retransmitted. Source TX counters confirmed all 120 sends.

| Scanning setup | Primary received / sent | Secondary received / sent |
| --- | ---: | ---: |
| Both profiles BW 62.5, SF7, CR5, preamble 32 | 29 / 30 | 29 / 30 |
| Primary as above; secondary BW 500, SF5, CR5, preamble 512 | 30 / 30 | 30 / 30 |

**Reception qualification did not pass: 118/120 arrived.** There were no
duplicates or new switch-command errors. The receiver's profile RX-counter
increments agreed with the captured payload counts. This run had no independent
fixed-channel witness, so it does not isolate the cause of the two misses.
The mixed-modem RF check intentionally used a longer secondary preamble than
the timing test; its result is not evidence of delivery at preamble 200.

Both nodes' original tested settings were restored, including radio2 off.
The RAK's identity and existing bootloader were preserved. Normal Full Companion
remains installed; no test image or temporary channel remains active.

## Evidence and scope

[Machine-readable timing results](rak3401_radio_speed_experiments_2026-11-09.safe.json)
include exact application hashes, stage means and histogram counts. Timing
used the existing `MESH_HIL_PROFILE_TIMING` instrumentation and 64 MHz DWT
cycle counter. SWD read each coherent snapshot after temporary radio2 was
disabled; the processor ran throughout the measurement windows.

This campaign covers one RAK3401 Full Companion. It does not qualify other
nRF52 boards, repeater/room roles, CPU sleep, Bluetooth load, or long-term
packet delivery. Earlier tests with different images and instrumentation are
not a controlled before/after comparison.
