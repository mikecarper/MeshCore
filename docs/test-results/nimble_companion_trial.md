<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/nimble_companion_trial/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32-S3 NimBLE Full Companion trial

Canonical Heltec V3 Full now uses the same pinned NimBLE backend, preserving
its existing 15 saved phones and both notification subscriptions per phone.
Its [Full memory qualification](heltec_v3_full_memory_2026-10-19.md) is separate
from the optional trials below. Switching from Bluedroid requires forgetting
the old phone pairing and pairing again; stored bonds are not migrated.

These optional builds use NimBLE-Arduino 2.5.1 with the existing ESP32 Arduino
2.0.17 toolchain. They are hardware qualification builds, outside the normal
release matrix and firmware picker.

See the [2026-09-08 hardware results](nimble_companion_trial_results.md) for
measured RAM, MAC-policy checks, WiFi updates, and remaining qualification.

The [contact-cache guide](../research/companion_contact_cache.md) adds six optional
350-contact trials for ESP32 boards without PSRAM, including Heltec V3.
Their [qualification results](companion_contact_cache_results.md) include
linked RAM margins, V4/RAK hardware tests and the mOTA queue policy.

| Hardware | Trial environment | Contacts | Channels | Offline queue |
| --- | --- | ---: | ---: | ---: |
| Heltec V4.2/V4.3 OLED, FEM on | `heltec_v4_2_v4_3_companion_radio_full_femon_nimble` | 350 | 40 | 512 |
| XIAO ESP32-S3 with WIO SX1262 | `Xiao_S3_WIO_companion_radio_full_nimble` | 350 | 40 | 256 |

The XIAO profile uses WIO radio pins CS 41, DIO1 39, BUSY 40 and RESET 42.
It is not the generic XIAO profile for separately wired radio modules. Both
boards retain their parent Full Companion features, USB mOTA sender, WiFi
update slots, and PSRAM-backed offline queue. The V4 also retains direct MQTT.

The [small-screen message layout](v4_pixel5_font_trial.md) uses 6-pixel capitals
on 128x64 panels and 5-pixel capitals on very tiny panels. It includes a compact
channel/sender line and full message previews. Font selection is automatic.

## Build

Enable the optional configuration in the ignored `platformio.local.ini`:

```ini
[platformio]
extra_configs =
  variants/*/platformio.ini
  platformio.nimble.ini
```

If that file already contains local settings, merge this list with them. Run
one PlatformIO/build.sh command at a time. Both commands below use USA Cascade:

```sh
OUTPUT_DIR=.releases/nimble-v4 bash build.sh build-firmware \
  heltec_v4_2_v4_3_companion_radio_full_femon_nimble \
  --firmware-version v1.17.1.5-halo-keymind-cascade-nimble-test \
  --radio-preset usa-cascadia --profile cascade --standard --require-ota

OUTPUT_DIR=.releases/nimble-xiao bash build.sh build-firmware \
  Xiao_S3_WIO_companion_radio_full_nimble \
  --firmware-version v1.17.1.5-halo-keymind-cascade-nimble-test \
  --radio-preset usa-cascadia --profile cascade --standard --require-ota
```

Use the merged image at address 0 for a USB installation. The V4 uses 16 MB
flash; the XIAO uses 8 MB. Both use DIO flash mode. Use the application-only
`.bin` for an existing, matching WiFi OTA layout. Remove the optional config
entry when finished to return to the ordinary release matrix.

## Bluetooth identity and pairing

All existing MAC settings remain available from the ASCII USB terminal and
the app's authenticated local CLI. A setting takes effect after reboot.

| Command | Behavior |
| --- | --- |
| `get bluetooth.mac` | Show the saved MAC policy. |
| `set bluetooth.mac C2:17:15:04:00:01` | Use this custom random-static address. |
| `set bluetooth.mac random` | Generate one address and retain it across boots. |
| `set bluetooth.mac random-every-boot` | Generate a new address on each boot. |
| `set bluetooth.mac random-after-connect` | Rotate on the next boot after an authenticated connection; otherwise retain the address. |
| `set bluetooth.mac default` | Restore the factory Bluetooth address. |
| `set bluetooth.stealth on` | Pair once, then accept the saved bonded peer. |
| `set bluetooth.stealth off` | Restore ordinary discovery without changing the MAC policy. |
| `get bluetooth.stealth` | Show pairing or bonded-peer-only state. |

The `ble.mac` and `ble.stealth` aliases also work. A literal custom address must
be a valid BLE random-static address (first byte C0-FF). Forget the old entry
in the phone's Bluetooth settings and pair again after an address change or
switching Bluetooth libraries. Existing Bluedroid bonds are not migrated.

Stealth does not disable the selected rotation policy. If rotation changes
the address on reboot, first pairing opens again for the new identity.

Pairing requires encryption, PIN authentication and a stored bond. NimBLE's
controller byte order is converted explicitly, so a custom address appears
over the air in the order entered. Failed identity setup stops advertising
and retries initialization instead of advertising the wrong address.

## V4 WiFi setup QR

The active WiFi setup page displays a compact QR code with a one-pixel white
border. The normal open setup network fits a 44 x 44 pixel code, with 2 x 2
pixel modules. The SSID, portal address and `HOLD STOP` hint remain beside it.
Short-click to leave the page; hold the button on this page to start or stop
the setup AP. From USB, `start webconfig ap` starts the same setup session.

When the setup AP is inactive and the node has joined WiFi, this page shows
`WIFI CONNECTED`, `IP <address>`, `SETUP AP INACTIVE`, then `HOLD START AP` from
top to bottom. The address appears once assigned and clears when the station
disconnects. While the setup AP is active, the QR page shows the setup portal's
address instead.

## Regression checks

```sh
python3 -B test/test_nimble_companion.py
python3 -B test/test_bluetooth_mac_contract.py
python3 -B test/test_bluetooth_pairing_ui.py
python3 -B test/test_heltec_v4_wifi_setup_page.py
python3 -B test/test_firmware_ram.py
python3 -B test/test_esp32_dram.py
python3 -B test/test_shared_mota_queue.py
python3 -B test/test_companion_mqtt_stack.py
pio test -e native -f test_companion_node_prefs \
  -f test_ble_tx_stall_watchdog -f test_companion_frame_queue \
  -f test_display_driver
```

The adapter test compiles the actual transport under address/undefined-behavior
sanitizers with API doubles. It injects allocation, identity and bond failures;
checks PIN authentication and stealth; and exercises 176-byte frames, MTU and
notification subscription gating, retry and duplicate prevention. Embedded
builds compile against the pinned real library. RAM reports retain the existing
wireless budget; linked headroom is not a measurement of live free heap.
The MQTT save callback also has a compiler-enforced stack-frame regression
check: nested preference copies previously overflowed the V4 loop task when
saving MQTT settings through WebConfig, despite sufficient free heap.

Hardware qualification must additionally verify pairing and reconnection,
advertised addresses for every MAC policy, contact synchronization, WiFi/BLE
coexistence, and uptime under load. BlueZ results do not establish iOS or
Android camera/pairing compatibility.
