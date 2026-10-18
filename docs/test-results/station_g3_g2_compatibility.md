<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/station_g3_g2_compatibility/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Station G3 and G2 compatibility audit

Source audit on 2026-10-18, starting from
`1147c28b279f01443c9ec44c3b71cc767f11482e`. A user reported that G2 firmware
works on a physical G3 while G3 firmware has problems. The comparison below
identifies differences in configuration and startup; it does not establish the
cause of that device's symptoms or claim a successful hardware retest.

## Radio and peripheral pins

The G2 radio map matches G3 physical slot 1 exactly. G3's `_r2` suffix refers
to physical socket 2, not motherboard revision 2. Current G3 firmware detects
the populated socket at boot and retains both legacy target names.

| Signal or build macro | G2 | G3 slot 1 | G3 slot 2 |
| --- | --- | --- | --- |
| SX1262 DIO1 / interrupt | 48 | 48 | 2 |
| SPI NSS / chip select | 11 | 11 | 43 |
| SX1262 reset | 21 | 21 | 44 |
| SX1262 BUSY | 47 | 47 | 1 |
| SPI SCK | 12 | 12 | 39 |
| SPI MISO | 14 | 14 | 41 |
| SPI MOSI | 13 | 13 | 40 |
| External LNA control | Not controlled by G2 firmware | 10, active low | 42, active low |
| PA PL1 control | Not controlled by G2 firmware | 9, shared; high selects higher level | 9, shared |
| I2C SDA / SCL | 5 / 6 | 5 / 6 | 5 / 6 |
| User button | 38 | 38 | 38 |
| `PIN_GPS_RX` / `PIN_GPS_TX` | 15 / 7 | 15 / 7 | 15 / 7 |

The radio and FEM maps agree with the manufacturer's
[ESP32-S3 pinout viewer](https://tools.bqvoy.com/stationg3/pinout/?board=BQESP32V1M-40pin)
and its [pin data](https://tools.bqvoy.com/stationg3/pinout/boards.json), and the tracked
[G2 recipe](https://github.com/mikecarper/MeshCore/blob/1147c28b279f01443c9ec44c3b71cc767f11482e/variants/station_g2/platformio.ini),
[G3 recipes](https://github.com/mikecarper/MeshCore/blob/1147c28b279f01443c9ec44c3b71cc767f11482e/variants/station_g3_esp32/platformio.ini), and
[runtime socket table](https://github.com/mikecarper/MeshCore/blob/1147c28b279f01443c9ec44c3b71cc767f11482e/variants/station_g3_esp32/StationG3RadioSlot.h).
The GPS macros name the peripheral's signals: the ESP32 startup calls
`Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX)`, giving MCU RX GPIO7 and TX GPIO15.
No incorrect radio map was established. Both base recipes select a 1.8 V
TCXO and disable the SX1262's internal boosted RX gain by default. The base TX
power is 7 dBm in both; their configured maximum chip-drive limits differ
(G2: 19 dBm; G3: 22 dBm). This audit does not change those power limits.

## Ordinary-profile flash compatibility

Before this change, both board manifests declared QIO flash, but the G2 recipe
overrode it to DIO. Ordinary G3 profiles inherited QIO. The G3 board manifest
now selects DIO as well, following the known-working G2 startup policy.
All G3 role recipes, including the legacy `_r2` targets, inherit that setting.

ESP32-S3 Full builds already force DIO through
[the Full partition policy](https://github.com/mikecarper/MeshCore/blob/1147c28b279f01443c9ec44c3b71cc767f11482e/scripts/esp32_full_partition.py). This change
therefore corrects an ordinary-profile difference; it does not explain a
failure in an already verified DIO Full image. The PSRAM memory type remains
`qio_opi`. Flash capacity, partition layouts, USB configuration, and radio pin
assignments are unchanged.

Ordinary G2 firmware uses the legacy `default.csv` partition table, while G3
uses `default_16MB.csv`. Electrical compatibility does not make those layouts
interchangeable. When moving a node that currently runs a G2 image to a G3
image, check its installed layout and use the documented
[partition migration](../esp32_wifi_partition_migration.md) if needed to retain
its identity and settings. This fix does not change either layout.

The two board manifests otherwise have matching build/upload settings; their
board names and manufacturer-page URLs distinguish them. DIO is a compatibility
choice here, not a claim that QIO caused the user's reported problem.

## Bounded radio startup probe

The G3-only socket probe added in `f765604cb8` imposed a 100 ms BUSY wait and
only three RC-standby attempts before rejecting a socket. G2 goes directly
through RadioLib's normal initialization, which permits a longer reset and
RC-standby settling interval. This is a separate, reproducible startup
compatibility gap, independent of the flash-mode difference.

The corrected probe shares one rollover-safe 1,000 ms deadline across reset,
BUSY waits, and standby/version retries. It services cooperative USB while
waiting. It retains identification-only operation, safe chip-select/reset
cleanup, explicit socket overrides, and the existing fallback rules.

The native production-code fixture models both sockets and timer rollover.
Elapsed times below are simulated fixture clocks, not physical measurements:

| Modeled condition | Previous probe | Corrected probe |
| --- | --- | --- |
| BUSY remains high for 200 ms after reset | Rejected at 111 ms, no version-register reads | Detected at 202 ms, 16 bytes read |
| First five standby commands ignored | Rejected at 44 ms, 48 bytes read | Detected at 67 ms, 96 bytes read |
| BUSY never clears | Rejected early | Rejected at exactly 1,000 ms, no register reads |
| Socket absent | Rejected after three attempts | Rejected at exactly 1,000 ms |
| 800 ms BUSY followed by persistent command refusal | Rejected early | Rejected at exactly 1,000 ms total |

An immediately responsive chip still completes its probe in under 20 ms in
the fixture. Negative controls restoring the old short BUSY budget or the
three-attempt limit fail the delayed-startup regressions. These results prove
the software tolerance gap; they do not prove it caused the user's hardware
failure. No transmit or TCXO-setting override was introduced.

## Saved FEM and socket settings

G3 firmware actively drives its LNA and PA-level GPIOs. G2 firmware does not,
so a saved FEM setting can have a visible effect only after switching to G3
firmware. Once the normal CLI has started, read these values before changing
settings:

```text
get radio.slot
get radio.fem.rxgain
get radio.fem.txgain
```

If startup reports `Station G3 radio unavailable`, the normal CLI has not
started. Press Enter and use the limited USB recovery commands instead:

```text
!g3 help
!g3 get radio.slot
```

That recovery console accepts only socket selection, help, and reboot; the
normal FEM getters are unavailable there.

`radio.slot` reports the saved selector, active socket, and detected sockets.
An explicit socket override does not silently fall back to another socket.
`radio.fem.rxgain` controls the external LNA and is distinct from
`radio.rxgain`, which controls the SX1262's internal boosted gain.

Preserve the saved values and jumper positions while diagnosing the device.
The manufacturer documents GPIO control as effective with the corresponding
LNA P/LNA S or PA PL1 jumper removed; PA PL2 selects the hardware power-level
pair. Changing gain or jumpers is an operator choice, not an automatic part of
this compatibility fix.

## Git history and attribution

| Change | Introducing commit and author |
| --- | --- |
| Initial G2 support and board manifest | [acac20dc89](https://github.com/meshcore-dev/MeshCore/commit/acac20dc89b6bc183125bcb9341eb33c56e1ece8), Rastislav Vysoky, 2025-03-07 |
| G2 radio pin flags moved into the variant recipe | [7cb2e0863a](https://github.com/meshcore-dev/MeshCore/commit/7cb2e0863a8171a300c062547d95697e1c69f009), recrof, 2025-10-12 |
| Original G3 slot-1 map, PA9 and LNA10 | [75fb07fc2c](https://github.com/meshcore-dev/MeshCore/commit/75fb07fc2c9d87238c1a2c88142d7e1e64415979), Chris / Cisien, 2026-05-09; merged in [upstream PR #2515](https://github.com/meshcore-dev/MeshCore/pull/2515) |
| G2 DIO recipe override | [1a43123d2c](https://github.com/mikecarper/MeshCore/commit/1a43123d2c5ca409e7f310d74e4495ea1c7f6d52), mikecarper, 2026-08-24 |
| G3 second-slot recipes and GPIO map | [d8d72b7833](https://github.com/mikecarper/MeshCore/commit/d8d72b783346b2d775a87dbda87588e48eacc51a), Adam Gessaman / agessaman, 2026-09-05 |
| Runtime socket discovery and shared pin binding | [f765604cb8](https://github.com/mikecarper/MeshCore/commit/f765604cb83eb2556228efd5a09e829186aee093), mikecarper, 2026-10-15 |

`git blame` and the introducing diffs were checked, rather than attributing
copied pin values to the latest refactor. Original G3 support is present in
upstream `dev`; the second-slot recipes and
runtime discovery were added in this fork. The original G3 pull request reports
hardware testing by its author. That historical report does not qualify the
current firmware or the user's device.

## Validation boundary

The host recipe regression resolves every tracked G3 environment, checks that
ordinary roles cannot override DIO, and verifies that `qio_opi` PSRAM remains
unchanged. It also executes the existing Full policy for canonical Repeater,
legacy `_r2` Repeater, and Full Companion configurations. A deliberate nested
QIO override must fail that check.

| Check | Status |
| --- | --- |
| G3 recipe suite, including all 16 current role environments and the deliberate QIO override | PASS, 10 test methods |
| Existing ESP32 Full partition policy suite | PASS, 11 test methods |
| Native socket-detection suite, including 144 selection cases and 20 delayed/deadline cases | PASS, 6 test methods, ASan/UBSan |
| Combined socket detection/settings, boot recovery, radio pins, KISS slot and GPIO-hold suites | PASS, 36 test methods, including the 6 detection methods above |
| Ordinary Repeater, legacy `_r2` Repeater and KISS firmware builds | PASS; all bootloader and application image headers verified as DIO |
| Canonical Full Companion package | PASS; 24 linked capability checks, internal RAM and application-slot checks passed; bootloader/application and merged-image headers verified as DIO |
| Physical G3 boot and RF receive/transmit | Not performed for this change |

No successful physical G3 boot, RF receive/transmit trial, or reproduction of
the user's original problem is claimed by these host checks.
