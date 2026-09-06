# Full Companion: turn features on and off

For side-by-side Companion and infrastructure commands, see
[feature switches by role](role_feature_switches.md). MQTT and logging controls
differ between these roles.

Use the exact `companion_radio_full` image for your board. Full includes its
supported transports and MOTA sending in one firmware; ordinary settings do
not require rebuilding it. USB remains the normal way to update the Companion
itself. WiFi/Bluetooth updates are optional, and Full does not install LoRa
MOTA packages on itself.

## Open the text terminal

Close the app or serial monitor using the USB port, then open a terminal at
115200 baud, or use the [USB web console](https://flasher.meshcore.io/console).
A fresh Full Companion boot starts in ASCII text mode. If it is already in Binary
Companion mode, send this line once and wait for the terminal banner:

```text
+++MESHCORE-TERM-START
```

Run `help`, `board`, and `version` to inspect the device. On ESP32 you can
alternatively connect to its text terminal with `nc DEVICE_IP 5002`.
When finished, send `+++MESHCORE-TERM-STOP`, close the terminal, and reconnect
your Companion app. These are local text commands; an app's custom-variable
editor does not necessarily forward them.

## Common switches

| Feature | Turn on | Turn off | When it takes effect |
| --- | --- | --- | --- |
| Device power saving | `powersaving on` | `powersaving off` | Immediately; saved |
| LoRa receive power saving | `set radio.rxps on` | `set radio.rxps off` | Saved; radio applies when available |
| Radio chip RX boost | `set radio.rxgain on` | `set radio.rxgain off` | Immediately; saved; supported radios only |
| External FEM RX boost | `set radio.fem.rxgain on` | `set radio.fem.rxgain off` | Immediately; saved; controllable FEM only |
| External FEM TX gain | `set radio.fem.txgain on` | `set radio.fem.txgain off` | Immediately; saved; controllable PA only |
| ESP32 USB packet/debug logging | `set usb.logging on` | `set usb.logging off` | Immediately; saved; logging owns the USB terminal |
| nRF52 separate USB logging port | `set usb.logging on reboot` | `set usb.logging off reboot` | Saves and reboots to add/remove the second USB port |
| ESP32 browser settings | `set webui on` | `set webui off` | Saved; starts/stops WebConfig |
| ESP32 temporary setup portal | `start webconfig ap` | `stop webconfig` | This session; opens a setup network/QR where available |
| ESP32 WiFi firmware uploader | `start ota` or `start ota ap` | `stop ota` | This session; only usable with two application slots |
| Temporary MOTA radio window | `tempradio 910.525,250,5,5,120` | `normalradio` | Bounded window; saved normal settings return afterward |

Check a saved switch with the corresponding `get` command, for example
`get usb.logging`, `get radio.rxps`, or `get webui`. Use `powersaving` to
inspect device power saving. Fresh installations enable device power saving
and leave USB logging off. Existing saved preferences take precedence.

ESP32 logging and Binary Companion share one USB port. After turning logging
off, return to Binary Companion with `+++MESHCORE-TERM-STOP`. nRF52 keeps its
optional logging port separate from its primary USB port and BLE connection.
See the [USB switching guide](full_companion_usb_switcher.md).

## WiFi, Bluetooth, GPS, and MQTT

On ESP32, configure WiFi through the setup portal or these commands:

```text
set wifi.ssid MyNetwork
set wifi.pwd my-password
get wifi.status
```

Credentials are saved. A credential change schedules a reconnect; a TCP
terminal will disconnect. The WiFi switch on supported device displays or
their assigned WiFi button controls the Companion WiFi services. Stopping
WebConfig alone stops its browser service, while the Companion's TCP and
MOTA services can remain available.

WiFi modem sleep is independent of device power saving. Use
`set wifi.powersave min` for normal BLE/WiFi coexistence; inspect the effective
state with `get wifi.powersave`. `max` is available only where the firmware's
radio coexistence policy permits it.

Most Full Companions provide BLE alongside their other transports. There is
no universal `set bluetooth off` command. SenseCAP Indicator selects one
secondary wireless transport per boot:

```text
set companion.transport wifi
reboot
```

Use `set companion.transport ble` followed by `reboot` to select Bluetooth
instead. USB stays available. The ESP-NOW layout keeps its primary ESP-NOW
mesh active in either mode.

For GPS-equipped boards, use the Companion app's GPS/custom sensor setting:
`gps=1` enables GPS and `gps=0` disables it. Only boards with a compiled GPS
provider expose this setting. This is separate from sharing location with
contacts.

When the exact Full image includes MQTT, use WebConfig's MQTT cards. Select
and configure a broker slot to enable it; choose the `none` preset for every
slot to disable broker connections. The MQTT status, packets, raw, receive,
and transmit switches control their individual functions. Save the settings
and follow any reboot instruction shown. Builds without MQTT omit these
controls. Full Companion does not accept infrastructure text commands such as
`set bridge.enabled`, `set logging.output`, or `set mqtt1.preset`; use these
WebConfig controls for MQTT. There is no need for a separate logging or WiFi-MQTT Companion
image when that feature is included in Full.

## Send MOTA from any Full Companion

Place valid, destination-specific `.mota` files in a host directory. Set the
source, destination, controller, and required relays to the same bounded
temporary radio tuple. For USA Cascade on LoRa hardware:

```text
tempradio 910.525,250,5,5,120
```

On the computer connected to the Full Companion, run:

```bash
motatool serve --serial /dev/ttyACM0 --dir ./motas -v
```

This works on both ESP32 and nRF52 Full. Close the text terminal first.
`motatool` automatically sends `ota folder on` and owns USB while serving.
Stop the host tool to detach the source; use `normalradio` afterward to
return early, or let the window expire. A `.bin`, `.uf2`, or DFU `.zip` is
not itself a `.mota`; use [motatool](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/mota/README.md) to prepare the
package for the exact destination firmware and hardware.

ESP32 also accepts a host over WiFi:

```bash
motatool serve --tcp DEVICE_IP:5001 --dir ./motas -v
```

nRF52 also accepts an authenticated Bluetooth host:

```bash
python3 tools/ble_mota/ble_mota_seeder.py \
  --device MeshCore-MyCompanion --dir ./motas \
  --local 'tempradio 910.525,250,5,5,120'
```

Use one MOTA source transport at a time. See the
[complete MOTA instructions](companion_radio_full.md#serve-mota-images-manually)
for pairing, remote destination commands, and automation. ESP-NOW hardware
uses its ESP-NOW primary radio rather than transmitting LoRa.

## Update the Companion itself

USB is supported for every Full Companion. Use the exact board's UF2/DFU
package or ESP32 image and the normal flasher. Preserve the target's existing
partition layout unless deliberately performing a documented USB migration.

For ESP32 with two application slots, `start ota` returns the WiFi uploader
URL, normally `http://DEVICE_IP:8080/update`. `start ota ap` explicitly opens
`MeshCore-OTA`; join it and use the returned URL. Upload the exact board's
application `.bin`. The device reboots when the upload succeeds. Use
`stop ota` to close an unused uploader. Port 8080 keeps WebConfig on port 80
available. Use a trusted local network or a temporary setup network.

The 4 MB Full layouts and T-Beam 1W Full use a single application slot and
require USB; the command reports that limitation. The artifact's
`.capabilities.json` lists verified `ota_update_methods` when a wireless
self-update path is usable.

nRF52 Full exposes Bluetooth DFU to paired clients. Use the matching
application DFU `.zip` with a compatible Nordic/Adafruit Legacy DFU client
and the board's matching BLE-DFU-capable bootloader/SoftDevice. USB remains
the fallback when the installed bootloader does not support that procedure.
Bluetooth DFU updates the Companion; the separate Bluetooth MOTA source
service feeds packages to other mesh nodes.

For OTAFIX installations, use the exact board/storage profile from
[OTAFIX 2.4.6](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6).
It includes the Bluetooth reconnect fix from 2.4.5. Its release notes cover
upgrades from older bootloaders, including the special UF2-drive precautions
for version 2.4.3. The new 64 KiB retained-RAM staging feature applies to
qualified internal-flash nRF52840 receivers; Full Companions continue to
serve host-supplied packages without needing that receiver storage layout.
