# PR #7 review and validation

Validated on 2026-09-10 against integration commit
`fa010196b130dee9a8a6fc6325d794e2f4fb9ede`, which combines base `01cc7b8a`
with PR head `c002966c` and the review fixes. This includes the stacked ESP32
memory changes already present in PR #7.

## Review fixes

- Use the [published ExpressLRS Linkflow calibration](https://github.com/ExpressLRS/Targets/blob/504178dcfa469ee32f4290d6ae9ba02e2f2f365e/TX/GEPRC%20900%20Linkflow.json),
  expose 17–30 dBm, and retain the fixed +2 dBm radio drive and correct RF output
  after radio recovery. Propagate startup and radio-setting failures.
- Keep Linkflow LoRa OTA enabled, as intended by the added repeater target.
- Preserve stored ACLs and flood filters when allocation fails. Release owned
  buffers and prevent accidental shallow copies of their owners.
- Release heap OTA workspaces after self-serving windows; retain them for active
  transfers, manual staging, folder operations, and pending application. Service
  cleanup on Companions and reset staged-resume flags when a context disappears.
- Count dynamically allocated clients, filters, and OTA workspaces in the RAM
  budget. Respect explicit OTA storage ownership in PlatformIO flags.

## Automated validation

- `pio test -e native -e native_kiss_modem`: **1,393/1,393 cases passed**.
- Real C++ radio-power, heap-context, ACL persistence/CLI, and shared OTA queue
  tests passed. The applicable radio-power, heap-context, and shared-queue tests
  also passed on Linux with AddressSanitizer and UndefinedBehaviorSanitizer.
- Display/inbox, radio receive, static DRAM, and runtime RAM regression tests
  passed. ACL allocation-failure coverage verifies that stored clients survive.
- Sequential firmware builds and their memory gates passed for:
  `GEPRC_Linkflow_900_repeater`, `Heltec_v2_companion_radio_ble`,
  `Tbeam_SX1262_repeater_observer_mqtt`,
  `heltec_v4_r8_repeater_observer_mqtt`, `RAK_4631_repeater`, and
  `SenseCapIndicator-LoRa_companion_radio_full`.

## Indicator hardware validation

The original SenseCAP Indicator LoRa with 8 MiB flash and 8 MiB octal PSRAM
was reached through the MercerWoodMesh Pi over Wi-Fi. Its MAC, public identity,
board reply, and existing partition layout were checked before updating.
USB interfaces were absent, so this run does not qualify USB behavior.

The Full profile was applied to `SenseCapIndicator-LoRa_comp_radio_usb_wifi`.
The exact application is 2,149,208 bytes:

- Application SHA-256:
  `7a2b11e731f946af79d1104818c7993d3897e6d8ec1f0121d27284c0f0c8c665`
- ELF SHA-256, also verified in the application descriptor:
  `24e60c7785a7aefe3fb0536b0b595bf19e1264e4d5f100d201290dc95eb1761a`

This direct profile build uses the repository's default `v1.17.1` version text
and `14 Aug 2026` build-date text; the hashes above identify the tested image.

The Wi-Fi updater returned HTTP 200 and booted the application in `app1` at
`0x400000`. The previous application remains in `app0` at `0x10000`; the
partition table, NVS, and SPIFFS were not rewritten. Public identity, radio
parameters, and USB logging preference matched their pre-update values.

The existing Companion stress harness, using a TCP socket adapter, completed
100 persistent cycles and 100 reconnects in 109.9 seconds: **501 requests and
501 validated responses**, with no discarded bytes, malformed frames, or device
error flags. Device info, time, storage, and core statistics were exercised.
The text terminal exposed all four display-power controls and `display.inbox`.
The OTA folder service issued a valid `COUNT` request, received an empty-folder
reply, and returned to its disconnected state after the client closed.

After these checks, four memory snapshots showed 69,548–69,996 bytes of free
internal heap and a 61,428-byte largest block. PSRAM availability stayed at
6,408,731 bytes. Final uptime was 262 seconds, with zero error flags and an
empty outbound queue. This is a short regression check, not a long-term soak.
Raw baseline, upload, stress, and final-state records are retained on the Pi
under `/home/mikec/hwtest/runs/pr7-review/`.

No Linkflow module or RF power meter was available in this run. Linkflow power
calibration is supported by the pinned upstream table and executable driver
tests, not a physical RF output measurement. The Indicator's ESP32-S3 does not
exercise the classic ESP32 heap-OTA default; host tests and classic ESP32 builds
cover that path. Physical display readability was not inspected.
