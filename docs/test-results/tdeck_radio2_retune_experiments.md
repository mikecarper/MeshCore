<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/tdeck_radio2_retune_experiments/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# T-Deck retune command, priority and SPI experiments

Measured on 18 October 2026 (America/Los_Angeles) on one ESP32-S3 T-Deck with
16 MiB flash and 8 MiB PSRAM. This follows the
[display redraw scheduling measurements](tdeck_radio2_display.md).
The receive-only harness uses the production radio wrapper, profile scheduler
and display driver. It does not start Bluetooth or WiFi or send radio packets.
The [sanitized measurements](tdeck_radio2_retune_experiments.json) retain each
result once, firmware image hashes, counters, sample counts and timing summaries;
serial-port identifiers and raw console transcripts are omitted.

For this tested board, the selected setting is 8,888,888 Hz with the confirmed
modem and IQ caches enabled, normal owner priority, and the original SPI clock
phase programming. This was the highest bulk-transfer clock that passed these
checks. It does not establish a board or transceiver physical maximum. The
canonical Full Companion build and runtime smoke checks are recorded below.

## Measurement method

The primary profile was 909.5 MHz / SF7 / BW62.5 / CR5 with a 32-symbol
preamble. The secondary was 910.5 MHz / SF8 / BW500 / CR5, using a 32-symbol
preamble for the main and load comparisons and 40 symbols for the IQ comparison.
These controlled settings are not a packet-delivery or preamble recommendation.

All 42 controlled trials used the same 394,304-byte application, SHA-256
`9afce4f59227382dfe383b6b53ac52001644b05f8636c8f4c4f3ed6e12937668`,
built from base `22cdb7014` with the experimental changes applied. Each case ran
three times for five seconds with either a static screen or requested 20 Hz
buffered redraws. Actual frame counts are retained because rendering can take
longer than the requested interval.

Retune timings bracket the radio wrapper's production retune hooks. Radio
service gaps, display frame durations and receive-window deadline lateness are
separate measurements. Table means combine both retune directions and all three
trials, weighted by sample count; maxima are the worst observed. The source
means were recorded to three decimal places. JSON percentile fields are
histogram bucket upper bounds, not exact percentiles.

Command counters count the instrumented RadioLib HAL transfers, transferred
bytes, packet-type queries and acknowledged cache skips. They describe actual
execution of the selected policy rather than an estimated command saving.

## Confirmed modem query cache

The cache omits the repeated packet-type query only while a previously
confirmed LoRa modem remains valid. Lifecycle invalidation and the existing
receive and packet-ownership guards remain part of the production path.

| Policy at 8 MHz | Static mean retune | Static maximum | Buffered mean retune | Buffered maximum |
| --- | ---: | ---: | ---: | ---: |
| Query on each hop | 428.810 us | 450 us | 486.632 us | 595 us |
| Reuse confirmed modem | 400.021 us | 423 us | 459.761 us | 575 us |
| Query on each hop, scoped priority 3 | 439.244 us | 460 us | 495.471 us | 605 us |

Across the cache-enabled main cases, 1,844 hops recorded 1,844 skipped modem
queries and zero actual modem queries. The corresponding uncached baseline
recorded 1,844 modem queries. Scoped priority alone did not improve these quiet
harness timings.

## Scoped priority under a competing task

The single existing radio/UI owner normally ran at priority 1 on core 1. The
experiment temporarily raised only the retune call to priority 3, then restored
priority 1. It did not create a second task that could manipulate radio state.
The competing task ran on the same core at priority 1, performing a bounded
250-microsecond CPU burst every two RTOS ticks.

Both policies enabled the confirmed-modem cache. The load cases recorded
25,269 competing-task bursts. Every result confirmed the selected actual
retune priority and restoration of the original owner priority.

| Priority during retune | Static mean / maximum | Buffered mean / maximum | Static maximum service gap | Buffered maximum service gap |
| --- | ---: | ---: | ---: | ---: |
| 1 | 429.704 / 665 us | 594.379 / 825 us | 730 us | 1,494 us |
| 3, then restore 1 | 405.178 / 492 us | 527.755 / 832 us | 738 us | 1,519 us |

Scoped priority reduced mean retune time with this particular competitor, but
did not improve the worst complete radio-service gap. This synthetic task does
not establish behavior with a connected Bluetooth client, network traffic or
the other services in Full Companion.

## IQ workaround reuse when packet parameters change

Using a 40-symbol secondary preamble makes the packet tuple change between the
two profiles. The comparison tests reuse of the already applied normal-IQ
workaround while retaining the packet-parameter writes required for that change.
Both cases used the confirmed-modem cache and normal owner priority.

| IQ workaround cache | Static mean / maximum | Buffered mean / maximum |
| --- | ---: | ---: |
| Off | 511.358 / 533 us | 584.149 / 708 us |
| On | 430.664 / 457 us | 491.475 / 600 us |

All 12 IQ trials passed their runtime checks. The enabled cases recorded 1,843
IQ workaround skips across 1,843 hops. This is timing and state evidence from a
quiet receiver, rather than a packet-delivery comparison.

## SPI sweep and transfer-path comparisons

The fresh-boot sweep and bytewise diagnostic used a separate 395,408-byte
application, SHA-256
`d0124aac39a949f8a1cc292cb039f64ac61351d3fd3ca248137f14b4fb06ac90`.
Every independent clock attempt began with a device reboot. Each inspection
performed 100 paired reads of packet type and the LoRa sync word, with standby
and recovery at 8 MHz. Static and buffered timing trials followed only after
the preceding checks passed. Clock order alternated between repeats.

The frequency column is calculated from the SPI peripheral's configured clock
divider, captured during the radio HAL transfer. It is not an oscilloscope
measurement of the clock signal.

| Requested MHz | Divider-derived MHz | Complete passing attempts | Incorrect modem reads per 100, across three inspections |
| --- | ---: | ---: | --- |
| 8 | 8.000000 | 3/3 | 0, 0, 0 |
| 9 | 8.888888 | 3/3 | 0, 0, 0 |
| 10 | 10.000000 | 0/3 | 6, 7, 7 |
| 11 | 10.000000 | 0/3 | 5, 5, 6 |
| 12 | 11.428571 | 0/3 | 20, 20, 28 |
| 13 | 11.428571 | 0/3 | 22, 20, 20 |
| 14 | 13.333333 | 0/3 | 59, 49, 67 |
| 15 | 13.333333 | 0/3 | 58, 54, 55 |
| 16 | 16.000000 | 0/3 | 64, 68, 64 |

These inspections recorded no sync-word mismatch or explicit SPI read error;
the failing check was an incorrect modem value. The sweep's highest passing
divider-derived frequency was 8.888888 MHz for this bulk-transfer implementation.
This observation must not be treated as the radio's physical SPI limit.

A separate bytewise diagnostic read all 100 modem values correctly at both
10 MHz and 16 MHz, with no sync-word mismatch or reported read error. Its
16 MHz bulk comparison returned 29 correct values and 71 incorrect values;
returning to bulk 8 MHz read all 100 correctly. The different transfer paths
therefore prevent interpreting the bulk failures as a transceiver clock limit.

## Clock-register experiment and longer read checks

The final diagnostic and comparison image was 396,560 bytes, SHA-256
`6e830f93dd3bb632e73e47878e8bbe61795a0389fa1ba75aab3c2a1ff9e14a85`.
With the original phase programming, 10,000 paired modem/sync-word reads passed
at both 8 MHz and 8.888888 MHz, with zero incorrect modem values, sync-word
mismatches or reported read errors. A 10 MHz check returned 58 incorrect modem
values in 1,000 pairs.

Inspection found differences between some N/pre-divider/phase fields and an
experimental reconstruction of the clock register. Changing those phase fields
did not improve the link: at 8.888888 MHz it introduced 587 incorrect modem
values in 10,000 pairs; at 10 MHz it returned 64 incorrect values in 1,000 pairs,
compared with 58 using the original programming. The experiment also failed at
higher tested clocks. It was rejected for production and remains confined to
the HIL helpers under `tools/hil`.

These observations neither explain every transfer-path difference nor establish
a causal driver defect. The reported frequencies remain divider-derived values;
clock waveforms and phase lengths were not measured with an oscilloscope.

## Final comparison with exact clock settings

All 24 final trials used that same final image, with exact clock settings enabled
and the rejected phase-field correction disabled. Each policy ran twice for five
seconds in both display modes. A nominal 9 MHz request in the earlier sweep
produced an 8,888,888 Hz divider; the final setting requests that exact frequency,
avoiding the rounded-frequency divider search. The timing comparison below is
within this final image rather than across different instrumentation builds.

| Secondary preamble | Clock setting | Caches | Static mean / maximum retune | Buffered mean / maximum retune |
| --- | ---: | --- | ---: | ---: |
| 32 | 8 MHz | Neither | 426.751 / 457 us | 518.300 / 626 us |
| 32 | 8 MHz | Modem | 404.635 / 438 us | 493.754 / 610 us |
| 32 | 8.888888 MHz | Modem | 402.134 / 436 us | 491.574 / 608 us |
| 40 | 8 MHz | Neither | 562.482 / 605 us | 652.882 / 777 us |
| 40 | 8 MHz | Modem and IQ | 437.898 / 472 us | 528.139 / 648 us |
| 40 | 8.888888 MHz | Modem and IQ | 435.327 / 471 us | 526.319 / 653 us |

The additional clock increase saved about 2–3 microseconds in mean retune time
for these cached paths. Avoiding redundant commands supplied the larger saving.
The cache-enabled final trials recorded 4,912 modem-query skips; the IQ-enabled
cases recorded 2,457 IQ workaround skips.

Across the cached 8.888888 MHz final cases with secondary preambles 32 and 40,
the best observed individual retune was 378 us and the worst was 653 us.
The separate Full Companion smoke check below reached 1,224 us under its
application workload; those measurements are not interchangeable.

The selected T-Deck configuration uses `MC_SX1262_SPI_HZ=8888888`,
`MC_SX1262_FAST_MODEM_CACHE=1` and `MC_SX1262_FAST_IQ_CACHE=1`. Scoped priority
stays off because it did not improve the maximum complete radio-service gap.
The clock-register correction stays off because its hardware comparison was
worse. This selection is limited to the tested board and workload.

## Full-screen redraw duration

| Renderer and radio policy | Frames | Mean redraw | Minimum | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Original direct redraw, bytewise 2 MHz | 204 | 73.603 ms | 72.298 ms | 75.131 ms |
| Final buffered redraw, 8.888888 MHz, preamble 32 | 176 | 57.035 ms | 56.123 ms | 57.839 ms |
| Final buffered redraw, 8.888888 MHz, preamble 40 | 176 | 57.161 ms | 56.085 ms | 57.964 ms |

A typical redraw therefore fell from about 73.6 to 57.1 ms: roughly 16.5 ms
saved, or 22% less time. The worst observed redraw fell from 75.1 to 58.0 ms.
These durations include the cooperative radio service performed during rendering.

The original result combines three five-second trials from the first display
HIL image; each final row combines two five-second trials from the final
comparison image. They are observations across different images and combined
policies. Most of the saving comes from the PSRAM canvas path; this comparison
does not isolate the benefit of increasing the radio clock above 8 MHz.

## Checks, limits and remaining follow-up

The 42 controlled trials completed 12,920 profile switches and 1,801 display
frames, with zero reported switch failures, failed retunes, BUSY timeouts or
chip errors. All final RX-mode, modulation-cache and priority-restoration
checks passed. The minimum remaining owner-task stack was 3,288 bytes.

The 24 final exact-clock trials also passed every runtime check, completing
7,367 switches and 1,054 frames with zero reported switch failures, failed
retunes, BUSY timeouts or chip errors. All final RX-mode, cache and priority
checks passed. Their minimum remaining owner stack was 3,224 bytes.

No packets were decoded in those trials. They do not establish reception under
redraw, channel-load behavior, Bluetooth/WiFi coexistence or long-term reliability.
The SPI sweep failures are retained separately from the passing 42-case control
matrix, and were not followed by dependent timing trials on the failed link.

All seven hosted-document link tests passed after adding this report.
Twenty-three focused native regression tests also passed.
The final HIL source, including the isolated clock experiment helpers, compiled.

## Reproduction

Build and install the receive-only HIL using the
[display report's build instructions](tdeck_radio2_display.md#reproduction-and-checks).
The experiment controls change RAM only and the harness has no radio transmit
path. On its USB console, reproduce the final buffered, cached comparison with:

```text
exactclock 1
clockfix 0
inspect 9 1 10000
trial 2 9 1 1 0 0 40 5 20 1
```

The trial selects buffered display, nominal 9 MHz, modem and IQ caches on,
normal owner priority, no synthetic load, secondary preamble 40, five seconds
and requested 20 Hz redraws; the last number is the result sequence. Check
`actual_spi_hz` for 8,888,888, `exact_clock` for true and
`clock_timing_corrected` for false, alongside the error and cache counters.
`actual_spi_hz` is derived from the configured divider during the transfer;
it is not an oscilloscope measurement.

For repeated 8 MHz control cases, the collector accepts a port placeholder:

```text
python tools/hil/tdeck_retune_experiments.py <port> --suite main --spi 8 --seconds 5 --repeats 3 --fps 20 --output retune-main-8mhz.json
```

`tools/hil/tdeck_spi_sweep.py` explicitly reboots before every independent clock
attempt. Reboot resets the RAM-only exact-clock flag to false. Its original
clock matrix and the final manual exact-clock comparison therefore use
different clock-setting policies and must be interpreted separately.

## Full Companion follow-up

The canonical Full Companion
`v1.18.0.1-tdeck-radio2-tuned-22cdb701` compiled with the selected 8,888,888 Hz
radio SPI setting, both caches enabled, and normal owner priority. Its unchanged
RAM gate reported 230,680 internal bytes available, 164,896 required and a
197,912-byte largest region. These are linked capacity checks rather than
free heap after boot. The application body size check was 2,261,101 bytes;
the packaged application was 2,261,784 bytes, SHA-256
`9251694050ad0acdf4699cbedfabc11fe0597d28e57156f1e30d2b80df5ac9a3`.
All 31 capability verification checks passed.

The application was flashed into app0 while retaining saved device state.
Five intentional reboots returned the expected firmware, Bluetooth on and
radio2 off. USB Binary Companion device queries reported protocol 14,
350 contacts, 40 channels and manufacturer `LilyGo T-Deck`.

In the first 45-second runtime check, the screen was held on and a temporary
RX-only secondary used a 40-symbol preamble. The node completed 2,450 switches
with zero switch errors. At least six primary packets arrived while scanning
(`RX=6` advanced to `RX=12`). The longest observed active-pair retune was
1,224 us, with a provisional guarded budget of 1,347 us. The timing
recommendation window had not finished, so this remains a runtime smoke check.

A second runtime check also retained zero switch errors. Its lifetime `max`
field already contained 4,518 us before that active test, following the earlier
return to single-profile operation. That value is not attributed to an
active-pair retune. The first run's results remain in its log; the second run's
JSON replaced the earlier runtime JSON, so the sanitized report identifies
these sources separately.

Windows completed a fresh PIN pairing, and a subsequent connection confirmed
`ENCRYPTION_AND_AUTHENTICATION`. A requested 30-second, 10 Hz DEVICE_INFO
exercise returned 69 successful queries and then timed out; it recorded no
notification-queue overflow. It did not complete the intended 300-query workload
and is not a passing sustained BLE test. That earlier run overlapped
administrative settings writes.

A controlled retry configured the screen and secondary before starting BLE,
kept USB open without configuration changes during traffic, and performed
cleanup after traffic finished. It completed 30.060 seconds with 208 successful
DEVICE_INFO queries, achieving 6.886 queries/second against the requested 10 Hz.
Reply latency was 118.109 ms minimum, 145.063 ms mean, 239.963 ms p95 and
300.103 ms maximum. There were 213 notifications, including five unrelated
notifications, and no queue overflow or traffic disconnect. The authenticated
connection was still alive immediately before normal cleanup. APP_START was
not required. This passes the sustained-duration check while documenting the
lower achieved query rate; it does not establish the cause of the earlier timeout.

During that retry, radio switches advanced from 6,494 to 8,402 with zero errors,
and four primary packets arrived (`RX=125` to `RX=129`). The provisional guarded
switch budget was 1,281 us. The lifetime 4,527 us `max` was already present
before the retry and is not attributed to an active-pair retune.

The documented cleanup returned radio2 to off and the USB display mode to
`button-pairing`, and Bluetooth was on.
