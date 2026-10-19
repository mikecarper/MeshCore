<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/full_profiles/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Full firmware profiles

Full combines the features supported by an exact board and firmware role in
one image. Use runtime settings to choose the services you need instead of
flashing a separate image for each transport. Hardware, memory, and the
installed flash layout still determine which combinations can run.

**Full is not a runtime role switch.** Choose Companion, Repeater, Room Server,
or Sensor in the [firmware picker](firmware_picker.md). Changing roles requires
the matching firmware; enabling a bridge does not turn a Companion into a
repeater.

## Choose the board, role, and profile

| Choice | What Full combines | Important limits |
| --- | --- | --- |
| Full Companion | The board's supported USB, Bluetooth, ESP32 WiFi, and qualified serial/Ethernet Companion transports, plus terminal chat and mOTA serving | The primary radio and board interfaces remain those of the selected target. Full Companion serves updates to other nodes; it does not install LoRa mOTA on itself. |
| ESP32 Full Repeater or Room Server | Standalone operation, supported USB/WiFi services, and the qualified MQTT, ESP-NOW, or RS-232 combination | Not every image contains all three bridges. Read its capabilities and check the compiled controls. |
| ESP32 Full Sensor | Sensor operation, supported CLI/logging, and qualified WiFi firmware-update and TCP 5001 seeding services | Current Sensor profiles do not gain the repeater's MQTT/ESP-NOW/RS-232 bridges. |
| nRF52 Full supported sensors or Reduced sensors | Two separately qualified Repeater, Room Server, or Sensor recipes with LoRa OTA | These are sensor-driver choices, not ESP32 network features. Full Companion remains a separate profile. |

Select the exact hardware variant and storage choice. Read the selected
image's capability manifest for update methods and requirements; a similar
filename is not evidence that another board's image will work.

## Full Companion: choose the active transports

Close any application using the USB port, then open the
[USB web console](https://flasher.meshcore.io/console) at 115200 baud. A fresh
Full Companion starts in the text terminal. If an app already selected Binary
Companion mode, send this line and wait for the terminal banner:

```text
+++MESHCORE-TERM-START
```

Run `help`, `board`, and `version` to inspect the device. The same image can
serve an app through its compiled transports or provide
[terminal chat](terminal_chat_cli.md). USB remains the normal installation
and recovery path.

On a Bluetooth-capable Companion, `get bluetooth` shows the running state;
`set bluetooth on` enables the saved Bluetooth preference. On an ESP32
profile with WiFi, `start webconfig ap` opens a temporary setup portal where
you can save network credentials. MQTT-capable Companions also expose broker
settings there; MQTT is not included on every Companion board.

Most Full Companions can use multiple transports together. **SenseCAP
Indicator** selects Bluetooth or infrastructure WiFi per boot using
`set companion.transport ble` or `set companion.transport wifi`, followed
by `reboot`; USB remains available. Generic ESP-NOW and Indicator ESP-NOW
profiles use ESP-NOW as their primary mesh radio. Full does not convert those
profiles into LoRa radios.

USB logging shares the ESP32 Companion's single USB port. Use
`set usb.logging off`, then `+++MESHCORE-TERM-STOP` and close the terminal
before reconnecting a Binary Companion app. nRF52 Full Companion can instead
add a separate logging port with `set usb.logging on reboot`; use its primary
port for Companion commands and mOTA serving. A one-port host bridge can use
`set usb.logging stream reboot`, which occupies the primary port until logging
is disabled. See the [USB mode guide](full_companion_usb_switcher.md).

## ESP32 infrastructure: enable the compiled services

Full Repeater and Room Server images retain their normal role while their
supported bridges run. A LoRa/ESP-NOW bridge keeps LoRa as its primary radio
and exchanges packets with its ESP-NOW peers over 2.4 GHz. MQTT publishes
observed traffic to brokers; it does not subscribe to broker messages or
inject them into LoRa. RS-232 uses the board's configured UART and pins.

Check `get bridge.type` on bridge-capable images. Depending on the recipe,
it can report combinations such as `mqtt+espnow`, `rs232+espnow`, or
`mqtt+rs232+espnow`. Use only the controls compiled into your image:

| Service | Saved enable/disable control | Check actual operation |
| --- | --- | --- |
| MQTT | `set mqtt.enabled on` / `off` | `get mqtt.running`, `get mqtt.status` |
| ESP-NOW | `set espnow.enabled on` / `off` | `get espnow.running` |
| RS-232 | `set rs232.enabled on` / `off` | `get rs232.running` |
| USB packet logs | `set usb.logging on` / `off` | `get usb.logging` |

An enabled preference is not proof that a service started. Missing
credentials, a channel mismatch, or a UART conflict can prevent operation.
`bridge.enabled` is a historical alias with different meanings between
recipes; use the explicit transport controls on combined images.

For an MQTT-capable image, first follow its role's
[WiFi/MQTT setup](WiFi.md#mqtt-observer-setup) and verify the actual connection
with `get wifi.status`. Once connected, this example selects a custom broker:

```text
set mqtt.iata SEA
set mqtt1.preset custom
set mqtt1.server broker.example.com
set mqtt1.port 1883
set mqtt.enabled on
get mqtt.status
```

Replace the sample broker value. Add the broker's username and
password if needed. Use the [WiFi/MQTT guide](WiFi.md#how-the-mqtt-bridge-works)
for TLS, publication settings, broker-slot limits, and reconnect behavior.
Where MQTT and USB logging coexist, `set logging.output off|usb|wifi|both`
selects those two outputs together; it does not change LoRa repeating.

For ESP-NOW, configure both peers with the same channel and compatible
`bridge.format`. `wrapped` uses the shared `bridge.secret` for bridge peers;
`raw` connects to primary-ESP-NOW MeshCore nodes. When MQTT WiFi and ESP-NOW
run together, the ESP-NOW channel must match the access point's fixed WiFi
channel. ESP-NOW alone needs no WiFi credentials. Follow the
[two-board setup](espnow_bridge_setup.md) for the complete commands.

For RS-232, stop the UART bridge before changing its configuration:

```text
set rs232.enabled off
get bridge.uart
set bridge.baud 115200
set rs232.enabled on
get rs232.running
```

Use the UART and pin assignment supported by your board. GPS can reserve or
share a UART; enabling the bridge may be rejected or pause a yieldable GPS
provider. Check the board's recipe before changing `bridge.uart`.

### Capacity and role exceptions

MKE S3 combines UART and ESP-NOW in its normal Full repeater. Full Heltec V3,
WSL3, and RAK3112 MQTT repeaters also retain independent RS-232 alongside
MQTT and ESP-NOW. **T-LoRa V2.1** retains two Full repeater choices because
of internal RAM limits: UART plus ESP-NOW, or MQTT plus ESP-NOW. Other
measured capacity exceptions retain dedicated images. Non-ESP32 boards do
not inherit these ESP32 combinations; Wio-E5, for example, keeps a separate
RS-232 image.

## nRF52: Full and Reduced supported sensors

Qualified nRF52 Repeater, Room Server, and Sensor builds offer **Full
supported sensors + LoRa OTA** and **Reduced sensors + LoRa OTA**. Full
includes the drivers supported by that board's full recipe; it does not add
support for every possible attached sensor. Reduced omits selected optional
environmental/ranging drivers while retaining generic I2C and supported board
peripherals. Reduced RAK3401/RAK4631 recipes also retain their supported
INA219/INA226/INA260/INA3221 voltage/current drivers.

The picker recommends Full when both profiles are qualified from the same
source. Reduced remains available through its Advanced sensor option and
explicit shared links, with both downloads and their update identities
retained. An absent sensor does not require a Reduced image; supported sensor
discovery already handles missing peripherals.

Both profiles retain their exact board/role OTA identity and storage layout.
Choose the matching storage variant and meet its staging/bootloader
requirements. These profiles are LoRa OTA receivers; nRF52 Full Companion
is an update source and does not acquire WiFi or on-device MQTT. An attached
Pi can publish its USB packets using a separate host bridge. See
[sensor/storage choices](firmware_picker.md#hardware-and-variant-names).

## Install or update the matching layout

Expanded ESP32 Full images need application slots large enough for the image.
Use `get storage.layout` on firmware that provides it to inspect the installed
partition table. Firmware version alone does not establish the layout; use
the [partition lookup guide](esp32_partition_catalog.md) for older firmware.

For a compatible layout, use the exact board/role application-only image
through a supported updater. To change an ESP32 layout, use the matching
merged image over USB or an exact board/role staged migration ZIP whose README
supports your installed source layout and update route. An app-only image
cannot change the partition table, and a merged image is not a browser-OTA
application. The picker links the matching migration package where available.

ESP32 Full Companion WiFi self-update requires two application slots; the
single-slot profiles require USB. Infrastructure WiFi/LoRa self-update also
depends on the exact image's declared capabilities and installed layout.
nRF52 updates use the matching application UF2/DFU package or a qualified
LoRa route; they do not use the ESP32 browser procedure. See the
[ESP32 WiFi update and partition guide](esp32_wifi_partition_migration.md)
for supported staged routes and the [WiFi guide](WiFi.md) for role-specific
network setup.
