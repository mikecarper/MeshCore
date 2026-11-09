<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/esp32_psram_enablement_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# RAK3112 and ThinkNode M2/M5 PSRAM, 2026-11-09

The shared board recipes now enable 8 MB OPI PSRAM for all 34 existing
environments: 13 RAK3112, 10 ThinkNode M2 and 11 ThinkNode M5. Existing
PSRAM-aware contacts, queues, repeater tables and network buffers can use it.
Flash sizes, partition selection and USB transport settings retain their
existing contracts, except that all RAK3112 roles now use a DIO flash header.

Hardware evidence:

- [RAK3112 datasheet](https://docs.rakwireless.com/product-categories/wisduo/rak3112-module/datasheet/): 8 MB PSRAM. DIO flash with the `qio_opi` SDK follows the [Observer RAK3112 boot-loop fix](https://github.com/agessaman/MeshCore/commit/43bba0d3).
- [ThinkNode M2 datasheet](https://www.elecrow.com/download/product/CIL13002M/ThinkNode_M2_Meshtastic_Signal_Transceiver_DataSheet.pdf): ESP32-S3-WROOM-1-N4R8.
- [ThinkNode M5 manual](https://www.elecrow.com/download/product/ILM13205D/ThinkNode_M5_User_Manual.pdf): 4 MB flash and 8 MB PSRAM. Its OPI configuration still needs confirmation on physical hardware.

M2/M5 Full Companion no longer forces the non-PSRAM RTC queue backend. Their
existing PSRAM queue retains 350 contacts and 256 queued messages. Full RAK3112
Repeater requires its scope and advert tables in PSRAM: 36,828 bytes including
allocation overhead. Failure uses the existing USB recovery loop before LoRa
starts. Other roles keep their existing allocation fallback policy.

## Validation

All six builds passed compilation, runtime RAM admission, capability markers
and application-partition fit. These are build measurements, not running free
heap or hardware qualification. [Machine-readable results](esp32_psram_enablement_2026-11-09.safe.json)
record each source commit and build-log hash.

| Full image | Application / partition bytes | Internal RAM budget margin | Capability checks |
| --- | ---: | ---: | ---: |
| RAK3112 Companion | 2,268,200 / 3,342,336 | 64,952 | 26 |
| ThinkNode M2 Companion | 2,233,688 / 3,145,728 | 56,848 | 26 |
| ThinkNode M5 Companion | 2,254,984 / 3,145,728 | 54,648 | 26 |
| RAK3112 MQTT Repeater | 2,226,840 / 3,342,336 | 35,700 | 17 |
| ThinkNode M2 Repeater | 1,694,120 / 2,031,616 | 56,992 | 14 |
| ThinkNode M5 Repeater | 1,721,672 / 2,031,616 | 53,320 | 14 |

Builds used `bash build.sh build-firmware TARGET --firmware-version VERSION
--radio-preset usa-cascade-fixed`, one PlatformIO process at a time. The build
script selected each board's existing Full profile. Companion images with a
single application partition are not evidence of dual-slot OTA support.

The profile inheritance, PSRAM allocation/fallback, required-memory USB
recovery, RTC queue and USB recipe tests passed. No boards were flashed.
Physical boot, reported PSRAM capacity and sustained operation remain untested
on these three models. `memory` should report approximately 8 MB total PSRAM;
`0/0` would mean the running firmware has not made it available.
