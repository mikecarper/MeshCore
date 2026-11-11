<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/esp32_cpu_clock_settle_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 CPU clock changes, 9 November 2026

Status: passed on the Mercerwood Heltec V4.3 OLED Full Companion.

The previous firmware rejected valid `set cpu.freq 80` and `set cpu.freq 240`
requests with `CPU clock apply failed; unchanged`, retaining Auto at 160 MHz.
ESP-IDF can apply its new clock configuration after `esp_pm_configure()` returns.
The fix yields while waiting for a fixed clock to take effect, with at most ten
one-millisecond delays. It still rejects an incorrect clock and preserves rollback.
Already-established clocks and variable Auto policies do not wait.

## Hardware results

| Check | Result |
| --- | --- |
| Fixed 80, 160 and 240 MHz, power saving off | All applied on the first command; actual clock readback matched |
| Fixed 80, 160 and 240 MHz, power saving on | All applied on the first command; actual clock readback matched |
| Radio2 active: Auto | Raised actual CPU clock to 240 MHz |
| Radio2 active: fixed 80, 160 and 240 MHz | All applied and scanning continued |
| Radio2 switching during clock changes | Counter advanced from 65 to 686; errors remained zero |
| Restoration | Original primary radio, radio2 off, power saving off, Auto at 160 MHz |

Temporary receive profiles used 906.525 and 907.525 MHz, BW 62.5 kHz, SF7,
CR5 and 32-symbol preambles. This checks continued scanning, not packet-delivery
rate or switching-speed improvement. The timing recommendation was still
provisional when the test ended. USB command round trips included persistence
and transport time; they are not measurements of the clock settling delay.

The application was built from `a6509289d58e7263d8311ad28e7c545fa31cdb99`
plus this clock fix, using `heltec_v4_2_v4_3_companion_radio_full_femon`,
Cascade profile and the target radio preset. Runtime version:
`v1.18.0.2-clocksettle-test-a6509289`.

Application size: 2,585,736 bytes. SHA-256:
`bbb1dd68207fbb99821526911f7d04e9cc08803e93c6086d18ccb10f1730d99f`.
The existing partition table matched. Application-only flashing and complete
readback passed; device identity and saved settings were retained. The patched
application remains installed. The first reconnect caught startup output before
the command interface was ready; the subsequent full test completed successfully.

## Automated checks

All 37 focused tests passed: board clock API (12), CPU policy (3), profile clock
(1), receive contract (6), USB sleep (10), profile scan (2), and WebConfig clock
controls (3). The delayed-transition CLI regression fails against unmodified
`a6509289d` and passes with the fix.

Host tests cover delayed application, timeout, persistence failure and rollback,
fixed versus variable Auto policies, the Indicator restriction, and 100 stable
radio2 clock preparations without repeated waits. Silicon variants include
ESP32, S2, S3, C3 and C6; only the V4 ESP32-S3 was tested physically.
The Full Companion build passed capability and runtime-RAM checks.
