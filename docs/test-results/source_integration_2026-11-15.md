# Incoming-source integration — 2026-11-15

This is a selective backport onto `keymindCascade` at `177c46f1e950e34b6fa42ff22065e820c4b462b4`.
It does **not** merge all three source histories or declare their tips fully integrated.
The original checkout at `094ad60fa` and its unfinished radio/noise-floor work
were preserved; implementation and validation used a separate worktree.

| Source reviewed | Pinned tip |
| --- | --- |
| [meshcore-dev/dev](https://github.com/meshcore-dev/MeshCore/commits/dev/) | [`33cd5dbd9`](https://github.com/meshcore-dev/MeshCore/commit/33cd5dbd906f108b9f8db2d68d531679383f8add) |
| [agessaman/observer-firmware-dev](https://github.com/agessaman/MeshCore/tree/observer-firmware-dev) | [`34e658e20`](https://github.com/agessaman/MeshCore/commit/34e658e20a93c40130faa1401e63c39aacda2c1a) |
| [IoTThinks/Dev-PS18](https://github.com/IoTThinks/MeshCore/commits/Dev-PS18/) | [`62792bbbc`](https://github.com/IoTThinks/MeshCore/commit/62792bbbc29444f709ec90d9ccf7e9aa8649717e) |

## Imported or adapted

- Upstream `c79500e6a`: reject LoRa packets whose explicit header has no CRC,
  across SX1262/SX1268/LLCC68/LR1110/LR1121/LR2021/STM32WL. Read header metadata
  before `readData()` can replace it; drain rejected packets and count errors.
- Upstream `224658694`: enlarge the remaining BaseChatMesh and Sensor CLI reply
  buffers. This fork's Repeater/Room reply-cache paths already have their own bounds.
- Upstream `ab7b5d87a`: release CC310's retained internal mutex after verification
  errors, while retaining this fork's outer session lock, shared scratch storage,
  mismatch result and software fallback.
- Upstream `a655d1f78`: INA219 alternate-address detection at 0x40/0x41, including
  the fork's voltage-channel callbacks. A failed candidate does not replace the
  working driver pointer.
- Upstream KISS hardware controls through `a94f510f2`: AGC interval, FEM RX/TX
  gain and chip boosted RX gain. Commands use **0x24–0x2A** because 0x1B–0x23
  already belong to this fork's radio2/socket extensions. Unsupported is 0x09;
  SaveFailed remains 0x08. Host reset cancels deferred FEM writes; rejected AGC
  maintenance remains due. Existing protocol version and radio2 framing remain.
- Upstream board definitions: Heltec RC52, DFRobot LoRaWAN ESP32-S3, LilyGO T3
  STM32 and Nucleo WL55JC1. RC52 retains the fork's existing GPS provider and
  voltage calibration; active-high LEDs honor the shared LED setting. Shared
  display drivers gain nRF52 SPI and STM32 SPI OLED support.
- Upstream `b44f010bd`: Pico 2 W environments and manufacturer name,
  preserving this fork's existing Pico W combined/WiFi environments and limits.
- Upstream `9a2230b60`/`85bba1074`/`e3c1c3482`: optional Companion mDNS discovery,
  adapted to deferred WiFi startup, failed-start retry and node renames. It uses
  the existing DHCP-safe hostname builder and advertises meshcore/TCP plus node,
  firmware and protocol TXT fields. Enabled in the Heltec V4 WiFi recipes.
- Upstream Road Events number allocation and contribution backtesting guidance.
- Observer: comchan, virginiamesh and meshcore-analyzer-eu presets; in-place JWT
  construction and separate verification stack frame; lwIP-thread DNS dispatch;
  immediate HTTP listener rebinding; offline crash-report collector and its tests.
  Portable observers keep their existing self-check and logging policy.
- Dev-PS18 `9ea68bdca`: **opt-in** INA228 sensor module (`ina228_sensor` in
  platformio.ini), pinned to library 0.4.1. Device ID, manufacturer and calibration
  failures are rejected. Energy uses kWh, correcting the source's Wh/kWh mismatch;
  voltage readings participate in this fork's voltage-channel interface. Existing
  constrained sensor recipes do not enable another driver by default.
- Dev-PS18 `21c8e7fc6`: compatibility name `Xiao_S3_WIO_NoGPS_repeater`; the fork's
  existing WIO base already disables GPS and measures battery voltage on D0.

## Existing equivalents retained

The fork already guards the Companion callbacks covered by upstream `9fd02ff3b`,
persists custom Bluetooth names including the `ble.name` alias, persists ADC
calibration, initializes receive-error counters, handles radio-less ESP-NOW
boards, sleeps the optional RAK3401 radio, and carries the reviewed SX1262 RXPS
post-preamble timer behavior. Their older source implementations were not copied
on top of the current code.

Dev-PS18's Ethernet read-busy reporting, Tracker display power-cycle recovery,
GAT/RAK GPS pin selection and USB host sleep protection also have existing fork
implementations. These remain tied to their current ownership and recovery tests.

## Deferred / excluded

- **Upstream GPS architecture migration** (`df5a768c5` onward): new drivers,
  CommonGpsPrefs, acquisition windows, guard and settings schema replace interfaces
  used by this fork's tracker, serial ownership, persisted preferences and fresh
  clock-sync generations. Existing NMEA over-read protection and clock freshness
  safeguards remain. The new GPS CLI/guard features are not claimed as integrated.
- Upstream e-ink cooperative refresh/hibernate changes (`05423e516`, `1df6262d5`,
  `8d5bd2e2c`) and SenseCAP Solar white-LED behavior (`f9c54a149`): retained for a
  separate port to this fork's MeshPocket refresh and shared LED/power policies.
- Upstream `e365863ff`: its unbounded startup wait for a writable USB host can
  prevent battery-powered XIAO WIO startup. The existing guarded USB startup remains.
- Observer's broader network/MQTT lifecycle rewrite, self-heal/crash firmware
  reporting, automatic/per-slot neighbors modes, breaker/reconnect/renewal changes,
  manifest-OTA worker refactor and reduced-TLS automatic selection remain deferred.
  They overlap the fork's asynchronous NTP, shared WiFi ownership, SDK stop proof,
  transport-health recovery, reply delivery and radio2 payload support. Importing
  the collector does not imply those new firmware crash fields are emitted.
- Dev-PS18 `62792bbbc`: no blanket change to a ten-second first sleep, one-second
  sleep duration or five-second USB hold. The fork's existing startup grace,
  USB-session protection and scheduled-work sleep guards remain.
- Dev-PS18 `39e978f40`, `743bb28fc` and `b49b2208f`: no blanket CPU-floor,
  transmit-power or GPS deadline changes. Keep qualified transport/board settings
  and wrap-safe clock-acquisition policy. Build-script churn and removed build-date
  displays are not imported.
- Upstream `f0c6d471c`'s Arduino-Pico 5.6.0 platform pin remains deferred;
  Pico 2 W was compiled against the checkout's installed Arduino-Pico
  `1.50501.0+sha.275e73d1`. Existing Pico W recipes and platform selection were
  retained. Requalification on 5.6.0 is not claimed.
- New STM32 Repeater/Companion/Room recipes are not exposed until they fit the
  224 KiB application budget. With GCC 14, `-Oz` and LTO, Nucleo Room overflowed
  by 59,196 bytes and LilyGO OLED Companion by 54,972 bytes. Only KISS environments
  are exposed for those two new boards; filesystem boundaries were preserved.

## Validation

All PlatformIO invocations ran sequentially under the checkout's build lock.
No radio was flashed and no physical RF, GPS, power or broker behavior was tested.

- `pio test -e native -e native_kiss_modem -e native_jwt_helper -e native_ota_channel`:
  **2,114 test cases passed**. The JWT suite is isolated from native's other Identity
  implementation and checks real signatures, captured bytes, exact-fit buffers,
  overflow rejection and failed self-check cleanup.
- Radio interrupt/recovery: 23 tests, including CRC-less packet rejection and a
  subsequent valid packet; receive/CAD/RXPS contract: 6 tests.
- CC310 shared-scratch/error/fallback/session tests: 6 tests, including inner-mutex
  recovery while the outer lock is held.
- CLI bounds: 7 tests; sensor reference/discovery/voltage tests: 4 tests.
- SSD1306 power lifecycle: 2 tests; RC32 display detection/recovery: 4 tests.
- New mDNS lifecycle, lwIP DNS/HTTP shims and INA219/INA228 callback tests pass.
- ESP32 USB/serial hygiene: 14 tests, run with PlatformIO's Python environment.
- Crash collector: 85 tests pass, 2 optional paho-dependency tests skipped.

| Firmware environment | Linked application bytes / limit | Internal runtime RAM available / required |
| --- | --- | --- |

| `nucleo_wl55jc_kiss_modem` | 118316 / 229376 | 60,232 / 22,528 |
| `DFRobot_LoRaWAN_ESP32S3_companion_radio_ble` | 1662417 / 1966080 | 154,264 / 142,114 |
| `heltec_rc52_companion_radio_usb` | 451132 / 712704 | 102,824 / 40,960 |
| `Pico2W_kiss_modem` | 491224 / 3665920 | 449,676 / 22,528 |
| `integration_v4_ina228` | 2111797 / 6553600 | 253,784 / 159,776 |
| `LilyGo_T3_STM32_kiss_modem` | 132416 / 229376 | 60,000 / 28,672 |
| `heltec_rc52_without_display_repeater` | 528540 / 815104 | 171,336 / 65,908 |
| `heltec_rc52_without_display_companion_radio_ble` | 461828 / 712704 | 98,144 / 38,688 |
| `heltec_rc52_companion_radio_ble` | 473284 / 712704 | 97,804 / 46,880 |

`integration_v4_ina228` was a temporary, ignored PlatformIO overlay extending
`heltec_v4_companion_radio_wifi_mqtt_femon` with the `ina228_sensor` flags and
library. It exercises MQTT/JWT, mDNS, the network shims and the real INA228 library
in one ESP32-S3 firmware build. Its RAM result is a startup minimum with the
existing runtime admission checks, not a maximum-load TLS/WSS soak result.

The final firmware pass repeated both STM32 KISS builds and the RC52 USB/BLE,
with/without-display Companion and headless Repeater paths after the final board
changes. DFRobot, Pico 2 W and the combined V4 build passed in the earlier pass.
