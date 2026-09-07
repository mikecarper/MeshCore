# Feature switches by role — 1.17.1.5 USA Cascade

Use these settings with the exact board's canonical release image. Former
logging, power-saving, FEM-gain, and rotated-display variants are now runtime
choices where the hardware supports them. A setting cannot add missing radio
hardware, MQTT code, storage, or an OTA partition. Check the download's
`.capabilities.json` and the [firmware picker](firmware_picker.md).
For exact old device/variant names, search the
[1.17.1.5 variant map](releases/1.17.1.5-variant-map.tsv): it maps all 1,361
previous release entries to 1,325 covered choices or 36 excluded entries.
Many old choices share one current image; the command tables below explain
how to select their former behavior. Excluded entries have no download.

**Search terms** are alternative wording for finding these instructions, not
CLI aliases. Use the commands in the role-specific tables and examples.

## Open the USB web console

**Search terms:** USB terminal, serial console, ASCII terminal, browser terminal, web serial.

Open the [MeshCore USB web console](https://flasher.meshcore.io/console) in
Chrome or Edge, connect a data-capable USB cable, close other applications
using that port, and select the device. Use **115200 baud** when prompted.
Full Companion, Repeater, Room Server, and Sensor start with an **ASCII USB
terminal**. Run `board`, then `version` on a Companion or `ver` on infrastructure.

If a Companion app has already switched USB to its binary protocol, send:

```text
+++MESHCORE-TERM-START
```

To hand the port back to a Companion app or USB MOTA host, send
`+++MESHCORE-TERM-STOP`, close the console, and connect the app/tool. Dedicated
USB/BLE/WiFi Companion images can start in binary mode; use the start token
there too. KISS firmware uses its modem protocol and is outside this release.

The linked console runs in your computer's browser over **USB**. It does not
need node WiFi or `set webui on`. **WebConfig** is a separate settings website
served by supported ESP32 images. Infrastructure WebConfig can include a
browser command terminal (`set wifi.cli on`); Companion WebConfig does not.
Full ESP32 Companion also has a text terminal at TCP port **5002**.

## Which old variant setting should I use?

**Search terms:** old firmware variants, restore features, turn features on or off, enable MQTT, disable MQTT.

These controls apply to every board with the corresponding compiled feature;
use the board exceptions below when the command differs.

| Former choice | Current image / runtime control |
| --- | --- |
| USB logging or Station G2/G3 logging variant | Same role's canonical image; `set usb.logging on` / `off` |
| Infrastructure USB logging, WiFi MQTT, or both | Unified MQTT-capable Full infrastructure; `set logging.output usb`, `wifi`, `both`, or `off` |
| Companion WiFi-MQTT variant | MQTT-capable Full Companion; configure broker slots in WebConfig |
| `_ps` power-saving variant | `powersaving on` / `off` on Companion; infrastructure forms and restrictions below |
| `_femoff` / FEM-gain variant | `set radio.fem.rxgain on` / `off`; separate TX switch where controllable |
| RX boost variant | `set radio.rxgain on` / `off` on a supported radio |
| iKOKA rotated-display variant | Full Companion: `set display.rotation 180`; `0` restores the board default |
| Separate USB, BLE, WiFi, Ethernet, or Terminal Chat Companion | Exact board's Full Companion where listed; supported transports are included, with SenseCAP selection below |
| RS232 repeater variant consolidated into the ordinary repeater | `set bridge.enabled on` / `off` on a build containing the RS232 bridge |
| ESP-NOW bridge or primary ESP-NOW radio | Select the matching hardware/role image first; bridge and primary-radio channel commands differ |
| LoRa OTA / external-storage variant | Still choose the exact receiver/storage image and matching bootloader; this is not a software on/off switch |

Full infrastructure and Full Companion are different roles. In particular,
**`logging.output` and `bridge.enabled` are infrastructure commands**, not
Full Companion MQTT switches.

## Full Companion commands

| Setting | Enable / select | Disable / restore | Read back |
| --- | --- | --- | --- |
| Device power saving | `powersaving on` | `powersaving off` | `powersaving` |
| LoRa RX power saving | `set radio.rxps on` | `set radio.rxps off` | `get radio.rxps` |
| Radio RX boost | `set radio.rxgain on` | `set radio.rxgain off` | `get radio.rxgain` |
| External FEM RX gain | `set radio.fem.rxgain on` | `set radio.fem.rxgain off` | `get radio.fem.rxgain` |
| External FEM TX gain | `set radio.fem.txgain on` | `set radio.fem.txgain off` | `get radio.fem.txgain` |
| ESP32 USB logging | `set usb.logging on` | `set usb.logging off` | `get usb.logging` |
| nRF52 second USB logging port | `set usb.logging on reboot` | `set usb.logging off reboot` | `get usb.logging` |
| ESP32 persistent WebConfig | `set webui on` | `set webui off` | `get webui` |
| ESP32 temporary setup AP | `start webconfig ap` | `stop webconfig` | `get webui` |
| Display rotation | `set display.rotation 90`, `180`, or `270` | `set display.rotation 0` | `get display.rotation` |
| Temporary MOTA radio window | `tempradio 910.525,250,5,5,120` | `normalradio` | `tempradio` |

Saved settings apply immediately unless noted. Rotation and gain controls
report unsupported hardware instead of creating that feature. Fresh Full
Companion preferences enable device power saving and leave USB logging off;
existing saved preferences win after an update.

**ESP32:** logging and binary Companion traffic share one USB port. Turn logging
off before handing USB to an app/MOTA host. **nRF52:** the optional second CDC
port is for logs; use the primary port for Companion/MOTA. Adding/removing the
second port requires the `reboot` suffix shown above.

### Companion MQTT: use WebConfig, not infrastructure CLI commands

**Search terms:** Companion MQTT settings, Companion MQTT on, Companion MQTT off, broker configuration.

1. Run `set webui on` and open the URL reported by `get webui`, or run
   `start webconfig ap` and join the setup network.
2. In the MQTT cards, select a broker preset and configure its required
   details. Save to enable that slot.
3. To disable MQTT connections, select **`none` for every broker slot** and
   save. To enable them again, restore the desired presets/settings and save.
4. Use the separate status, packets, raw, receive, and transmit controls to
   choose what is published. Turning off status publication alone leaves
   broker connections enabled.

Full Companion has no USB text equivalents for `set mqtt1.preset`,
`set bridge.enabled`, or `set logging.output`. Use its WebConfig controls.
USB logging is independent of those broker slots. Images without MQTT code
do not show MQTT cards.

### Companion WiFi, Bluetooth, GPS, and board exceptions

Configure ESP32 WiFi with:

```text
set wifi.ssid MyNetwork
set wifi.pwd my-password
get wifi.status
```

`set wifi.powersave min` enables WiFi modem sleep; `none` disables it. `max`
is accepted only where the BLE/WiFi coexistence policy allows it. Read back
with `get wifi.powersave`. This is separate from device power saving and RXPS.
The assigned WiFi button/display switch controls WiFi services on supported
boards. `stop webconfig` closes only the portal; it is not a WiFi master switch.
There is no universal Bluetooth-off or Ethernet-off text command.

**SenseCAP Indicator Full only:** `set companion.transport wifi` followed by
`reboot` selects WiFi; `set companion.transport ble` followed by `reboot`
selects Bluetooth. Check `get companion.transport`. USB remains available.
Primary ESP-NOW Indicator images keep their mesh radio in either selection.

**GPS-equipped Companions:** use `gps=1` / `gps=0` in the Companion app's
custom sensor settings. These are app settings, not ASCII terminal commands.
See [GPS tracking](gps_tracking.md) for location-sharing settings.

## Repeater, Room Server, and Sensor commands

These roles use CommonCLI. Feature-dependent commands require the relevant
hardware/build; MQTT is present in observer builds, and WebConfig is absent
from some portable builds. The USB browser console still works without it.

| Setting | Enable | Disable | Read back |
| --- | --- | --- | --- |
| Live USB logging | `set usb.logging on` | `set usb.logging off` | `get usb.logging` |
| Capture RX log to node storage | `log start` | `log stop` | `log` prints the capture locally |
| MQTT / RS232 / ESP-NOW bridge master | `set bridge.enabled on` | `set bridge.enabled off` | `get bridge.enabled`, `get bridge.running`, `get bridge.type` |
| MQTT periodic status publication | `set mqtt.status on` | `set mqtt.status off` | `get mqtt.status` shows connection status |
| MQTT packet publication | `set mqtt.packets on` | `set mqtt.packets off` | `get mqtt.packets` |
| SNMP on supported MQTT infrastructure | `set snmp on`, then `reboot` | `set snmp off`, then `reboot` | `get snmp` |
| MQTT raw packet publication | `set mqtt.raw on` | `set mqtt.raw off` | `get mqtt.raw` |
| MQTT receive capture | `set mqtt.rx on` | `set mqtt.rx off` | `get mqtt.rx` |
| MQTT transmit capture | `set mqtt.tx on` (or `advert`) | `set mqtt.tx off` | `get mqtt.tx` |
| ESP32 persistent WebConfig | `set webui on` | `set webui off` | `get webui` |
| WebConfig browser command terminal | `set wifi.cli on` | `set wifi.cli off` | `get wifi.cli` |
| LoRa RX power saving | `set radio.rxps on` | `set radio.rxps off` | `get radio.rxps` |
| Radio RX boost | `set radio.rxgain on` | `set radio.rxgain off` | `get radio.rxgain` |
| Controllable FEM RX / TX gain | `set radio.fem.rxgain on` / `set radio.fem.txgain on` | `set radio.fem.rxgain off` / `set radio.fem.txgain off` | Corresponding `get radio.fem.rxgain` / `get radio.fem.txgain` |
| GPS, when compiled | `gps on` | `gps off` | `gps` |

Infrastructure `set usb.logging` has **no `reboot` suffix**, including nRF52.
`log start/stop` records to storage independently of live USB logging. Use
`log erase` to delete that capture.

### Infrastructure MQTT and logging output

**Search terms:** MQTT settings, MQTT on, MQTT off, logging output, USB and WiFi logging.

On unified Full infrastructure with both MQTT and USB logging compiled:

| Command | USB logs | MQTT bridge |
| --- | --- | --- |
| `set logging.output off` | Off | Off |
| `set logging.output usb` | On | Off |
| `set logging.output wifi` | Off | On |
| `set logging.output both` | On | On |

`get logging.output` reports the selection. Fresh unified Full preferences
select `both`; saved settings override this. To toggle only MQTT while keeping
USB logging unchanged, use `set bridge.enabled off` / `on`. Neither setting
turns LoRa repeating off. Repeater forwarding uses `set repeat off` / `on`
and `get repeat` separately.

For a custom broker on an MQTT-capable Repeater or Room Server:

```text
set wifi.ssid MyNetwork
set wifi.pwd my-password
set mqtt.iata SEA
set mqtt1.preset custom
set mqtt1.server broker.example.com
set mqtt1.port 1883
set bridge.enabled on
get mqtt.status
```

Set `mqtt1.username` / `mqtt1.password` if required by that broker. Configure only
as many slots as the board supports. `set mqtt1.preset none` disables slot 1;
other configured slots remain enabled. `set mqtt.status off` disables status
messages, not MQTT itself. See the [MQTT reference](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md)
for presets, TLS, credentials, and slot limits.

### Infrastructure power saving and bridges

`powersaving` reads the saved device setting. The bare **`powersaving on`**
command rejects local/USB-connected requests on nRF52 and standalone ESP32;
use it remotely with USB data disconnected. ESP32 bridge builds reject that
bare enable command. `powersaving off` disables it.

The separate **`set powersaving on` / `set powersaving off`** form, also used
by infrastructure WebConfig, saves/applies the preference without those
bare-command guards. Actual sleep depends on the board implementation and
can interrupt WiFi. Do not assume it behaves like Companion CPU/GPS saving.

On RS232-capable repeater images, stop the bridge before changing its serial
port or baud rate, then restart it:

```text
set bridge.enabled off
set bridge.baud 115200
set bridge.enabled on
```

**RAK4631:** `set bridge.uart 2` selects UART2 while the bridge is stopped.
Canonical GPS-enabled builds reserve UART1 for GPS; UART1 requires a dedicated
GPS-free image. **ESP-NOW bridge:** use `set bridge.channel <1..13>` and
`set bridge.format wrapped` / `raw` where supported. **Primary ESP-NOW mesh:**
use `set espnow.channel <1..13>` and reboot; this is a different radio setting.

For infrastructure WebConfig on the LAN, use `start webconfig` /
`stop webconfig`. To force a setup AP on an observer, first run
`set bridge.enabled off`, then `start webconfig ap`. When finished, run
`stop webconfig` and restore `set bridge.enabled on` if you did not reboot.

## Updating and sending MOTA

**Search terms:** mOTA, LoRa OTA, update over LoRa, wireless firmware transfer.

All released Full Companions can serve MOTA to other nodes. Close the console
and run `motatool serve --serial /dev/ttyACM0 --dir ./motas -v` on the USB host.
See [Full Companion instructions](full_companion_features.md) for WiFi/BLE
source commands and the bounded temporary radio setup.

| Role / hardware | Start self-update | Stop / requirement |
| --- | --- | --- |
| ESP32 Repeater, Room Server, Sensor with WiFi updater | `start ota` or `start ota ap`; open the returned URL (normally port 80, `/update`) | `stop ota`; close WebConfig first if it shares port 80 |
| ESP32 Full Companion with two application slots | `start ota` or `start ota ap`; returned URL uses port 8080, `/update` | `stop ota`; single-slot Full builds use USB |
| nRF52 infrastructure with Bluetooth DFU | `start ota` enters the Bluetooth update flow | Matching application DFU ZIP and board bootloader required |
| Qualified LoRa OTA receiver | Follow [LoRa OTA directions](ota_easy.md) | Exact destination package, storage profile, and overlapping temporary radio windows |

For nRF52 OTAFIX installations use the exact board/storage build from
[OTAFIX 2.4.6](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6).
Its retained-RAM handoff is required by the new internal-flash hybrid receiver
images. Full Companions are sources and remain normally USB-updated.

The [complete infrastructure CLI](cli_commands.md), [role/build matrix](cli_build_matrix.md),
and [Full Companion feature guide](full_companion_features.md) provide details.
