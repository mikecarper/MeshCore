<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/firmware_variant_consolidation/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Firmware variant consolidation validation

All ten packaged profiles passed. Firmware source:
[`b31d942b231360386fdfb92d0561f36095fdf011`](https://github.com/mikecarper/MeshCore/commit/b31d942b231360386fdfb92d0561f36095fdf011).
Version: `v1.18.0.1-variants-b31d942b`. PlatformIO builds ran one at a time.
The packages were built locally; physical testing of these combinations remains pending.

## Qualified behavior

- The Pico W combined Companion supplies USB, Bluetooth LE and WiFi in one image. It does not acquire a wireless self-updater from that transport combination.
- The base Heltec V4 OLED Repeater supports the matching OLED expansion kit. The original V4 TFT Full Companion supports the matching TFT kit and retains its 3,400 mV shutdown guard. TFT and OLED wiring remain distinct.
- V4 R8 TFT Companion, Repeater, Room Server and Sensor applications contain the optional-panel detection and runtime-rotation implementations. Saved `display.rotation` values select clockwise offsets of 0, 90, 180 or 270 degrees from the compiled board default without another portrait image. Zero restores that board orientation; `display.flip` adds 180 degrees. The stock expansion kit has no orientation sensor.
- Muzi Base Uno and Duo Full Companions detect their SuperIO SH1107 panel and handle optional GPS independently. Their SX1262 and LR1121 radio choices remain distinct.
- T-Echo Lite Full Companion checks the SSD1681 panel before initializing e-paper and supports the corresponding core-only assembly.

Future release selection retires an alternate choice only after its exact successor is qualified. Existing published binaries, filenames and update identities retain their original meaning. Switching between different target identities requires the documented local installation or an exact compatible update route.

## Packaged applications

| Profile | Application bytes | RAM admission margin (bytes) | Application SHA-256 prefix |
| --- | ---: | ---: | --- |
| Pico W combined Companion | 902,956 | 58,540 | `246b07c54f5c` |
| V4 R8 TFT Full Companion | 2,287,528 | 66,528 | `19170cc8866b` |
| V4 R8 TFT Full Repeater | 2,297,368 | 5,736 | `0837183ff083` |
| V4 R8 TFT Full Room Server | 2,302,648 | 7,784 | `28ffdb3bd325` |
| V4 R8 TFT Full Sensor | 1,590,952 | 131,472 | `064c4c98d2b6` |
| V4 OLED Full Repeater | 2,283,496 | 5,904 | `73ec156914e8` |
| V4 TFT Full Companion | 2,281,768 | 68,176 | `85c2dcaa00e8` |
| Muzi Base Uno Full Companion | 580,640 | 32,704 | `0d9fbe41f3ed` |
| Muzi Base Duo Full Companion | 571,792 | 32,784 | `d5970c20227f` |
| T-Echo Lite Full Companion | 595,896 | 28,928 | `91495097a524` |

The [machine-readable evidence](firmware_variant_consolidation.json) contains the full application SHA-256 values, hashes for every offered BIN/merged BIN/UF2/DFU ZIP, exact requested and packaged targets, source version, hardware/update identity where present, detection markers and RAM values. The application hash covers the ESP32 BIN, nRF52 DFU application's BIN, or decoded RP2040 flash application; container hashes are recorded separately.

RAM margins are linked capacity after the complete modeled heap requirement and transient reserve. They are admission results; runtime load and physical current were not measured.

The verifier checked every memory manifest's file bindings and RAM admission. It read the promised marker and exact firmware/source version from each uploadable application, compared ESP32 application bytes with their merged install, compared nRF52 DFU application bytes with decoded UF2, and required Pico W's decoded UF2 to match its BIN. UF2 headers, unused sector bytes and alignment padding cannot provide application capability evidence. Observer-named ESP32 packages retain their actual target and embedded observer version in the JSON.

## Software and browser checks

| Check | Result |
| --- | --- |
| Native R8 rotation, Muzi accessories and T-Echo Lite detection | PASS, 11 methods across three suites; production paths, sanitizers and negative controls |
| RP2040 application admission and existing capability checker | PASS, 11 RP2040 methods and 18 existing methods; malformed UF2, wrong family/address/layout, BIN mismatch and metadata-only proof rejected |
| Consolidation policy, release controls and legacy compatibility | PASS, focused Python suites; packaged marker/config/source gating, paired sensor policies and unchanged historical downloads |
| Picker JavaScript | PASS, five suites covering consolidation, Full defaults, explicit Reduced links, historical metadata and runtime directions |
| WebConfig source checks | PASS, 13 methods |
| WebConfig browser behavior | PASS, 25 methods in the real Chromium headless browser, including saved rotation load/change |
| Firmware picker browser behavior | PASS, 14 DOM checks across Full default/Advanced interaction, explicit Reduced links and stale metadata |
| Complete build-profile selection suite | PASS |
| Actual package controls and picker replay | PASS, 10 qualified profiles; R8 rotation commands offered for all four roles |
| Physical boards/accessories, sleep current and radio/transport load | Not performed |

The production controls generator and picker used release metadata assembled from these exact local files. Public firmware gains the new behavior when a matching qualified image is published. Full sensor support is the ordinary picker default; Advanced retains Reduced images and explicitly linked Reduced selections. Release policy keeps both Full and Reduced downloads and their exact update endpoints.

## Physical acceptance still pending

- **V4 R8 TFT:** cold USB/battery boots with the panel present and absent; all four angles, saved reboot persistence and `display.flip` interaction; dashboard geometry, touch wake/toggle behavior and sleep/wake at every angle; radio transmit/receive and GPS operation.
- **Muzi Base Uno/Duo:** cold boots with SuperIO present and absent; screen supply shutdown/wake, joystick and buzzer; GPS discovery and mode-switch changes; the power-button shutdown/wake sequence; radio operation throughout.
- **T-Echo Lite:** cold boots with the panel present and absent; real controller readback, BUSY settling and timeout behavior; e-paper refresh and sleep/wake; headless buttons, radio and GPS operation.
- **Pico W:** USB, Bluetooth LE and WiFi connections individually and concurrently, including frame delivery under normal radio receive/transmit load.

These checks are required before describing the hardware combinations as physically qualified.

The earlier [optional-screen record](../optional_screens.md#validation) covers separate RC32 and Tracker smoke packages; those are not part of this ten-profile matrix.
