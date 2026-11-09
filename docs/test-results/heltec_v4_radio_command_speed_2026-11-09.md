<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_v4_radio_command_speed_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# V4 faster radio commands — 9 November 2026

Keeping more of the existing RadioLib and Arduino SPI driver in internal
instruction RAM reduced average V4 switching time by **33% for frequency changes**
and **28% for frequency plus modem changes**. This extends the
[earlier V4 improvement](heltec_v4_radio2_switch_speed_2026-11-09.md).

The change is enabled for V4 and V4 R8 builds. It costs **2,704 additional internal
bytes** in the matched diagnostic builds. SPI remains **8 MHz** by default.
No command bytes, BUSY timeouts, bus locks, IRQ checks, receive preservation or
failure recovery were changed. CPU power-saving policy is unchanged.

## Experiments

One V4.3 OLED running Full Companion. Numbers are weighted means in microseconds,
with two quiet 20-second windows per profile. No CLI reads ran inside those windows.

| Driver configuration | CPU MHz | Frequency only | Frequency + modem |
|---|---:|---:|---:|
| Starting HEAD | 240 | 565 | 692 |
| Additional RadioLib command functions in RAM | 240 | 466 | 580 |
| Direct SPI-register transfers and resident airtime calculation | 240 | 402 | 519 |
| **Existing SPI driver and airtime calculation in RAM: selected** | **240** | **380** | **498** |
| Selected driver, synthesizer shortcut off | 240 | 438 | 549 |
| Selected driver, brief BUSY spin before yielding | 240 | 384 | 496 |
| **Selected driver at 80 MHz** | **80** | **572** | **745** |

The custom raw-transfer path was slower than keeping the existing SPI driver
resident. The brief-spin experiment offered no useful advantage, so normal
scheduler yielding remains. Disabling the synthesizer shortcut was slower too.

**16 MHz SPI failed qualification.** Starting at that rate failed on both tested
transfer implementations. Changing to it after an 8 MHz startup also failed:
the SDK path accumulated 37 switching errors. It is not enabled. The
[Semtech SX1261/2 data sheet](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf)
allows up to 16 MHz under its specified electrical conditions; that does not
establish reliable operation on this tested board.

The selected path reduced measured cache misses from about 168/176 to 72/73 per
switch. That counter covers both CPUs, so it supports the timing result rather
than attributing every miss to this task. ELF checks verify the selected command,
GPIO and SPI functions reside in IRAM. This is not a claim of ISR or cache-disabled
safety for the complete call chain.

## Checks and limits

- **239/240 packets received** on normal firmware without the private timing
  counters: 30 per receive profile, at both 80 and 240 MHz, for matching and mixed
  modem settings. One packet was missed on the 240 MHz mixed-modem secondary;
  the strict zero-loss matrix therefore **failed**. No RF retries, duplicates,
  corrupt payloads or switching errors. Per-profile RX counters agreed.
- Probes alternated 40/160-byte payloads on 906.525 and 907.525 MHz. Matching
  profiles used BW62.5/SF7/CR5/preamble32; the mixed secondary used
  BW500/SF5/CR5/preamble200.
- A follow-up isolated the affected 240 MHz secondary: 99/100
  with the original 200-symbol preamble, 100/100 with
  512 symbols, and 100/100 with 200 symbols and
  scanning disabled. These are additional tests, not replacements for the loss
  above. No firmware changed between runs. The original run's 3,007 us guarded
  switch budget implies a 264-symbol fast-profile recommendation, longer than
  the manually chosen 200. At BW500/SF5, 200 symbols last 12.8 ms and 512
  last 32.768 ms. This is consistent with a scan-acquisition limitation;
  it does not prove the cause of that individual miss or guarantee zero loss.
- The cumulative switching maximum was **3,446 us** in the initial matrix and
  **3,453 us** across the follow-up tests.
  The averages above are not a maximum-latency guarantee.
- 38 focused driver, receive, profile, buffering and build-middleware tests,
  plus seven documentation-link tests, passed.
  Full V4 Companion, Repeater and Room, plus V4 R8 TFT Repeater, built successfully
  and passed their existing RAM/flash capacity checks. No features or capacities
  were cut.
- Only the V4 OLED Companion was physically tested here. Other roles and the R8
  TFT received build checks. This was not a sensitivity, power-draw or maximum
  WiFi/MQTT-load qualification.

The V4 is left on normal firmware `v1.18.0.2-v4-radio-fast-46540028`, with its
original settings and identity. The witness radio's settings were restored.
Flashing wrote only the application after verifying its partition table matched.
The initial USB admission was attempted too soon after flashing; the same image
connected after the startup wait, before any RF probes were sent. The follow-up
completed both scanning windows, then rejected a single-radio setup command
because the harness had not waited for the acknowledged two-second radio2
shutdown. Settings were restored; only the unstarted single-radio control was
run separately with that wait. No firmware changed.

[Machine-readable measurements and checks](heltec_v4_radio_command_speed_2026-11-09.safe.json).

The source-copy build scripts verify the pinned library/SDK hashes and place
reviewed functions in RAM without modifying shared dependencies. The rejected
raw-transfer, 16 MHz and BUSY-spin experiments are absent from normal firmware.
