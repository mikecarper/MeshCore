# WiFi and MQTT by Firmware Type

MeshCore itself does not require WiFi or the internet. LoRa packet exchange,
repeating, room servers, companions, and sensors can all operate without either.
WiFi is added by particular ESP32 firmware targets for one or more of these
purposes:

- a TCP connection between a WiFi companion and a phone or computer;
- the WebConfig browser interface;
- MQTT uplinking from the radio to internet or LAN brokers;
- WiFi-assisted OTA services on builds that include them;
- ESP-NOW bridging, which uses the ESP32's 2.4 GHz radio but is not a connection
  to a WiFi access point.

The firmware role and the build profile are separate choices. For example, a
logging repeater is not an MQTT observer, and a WiFi companion does not publish
to MQTT unless its target name also contains `mqtt`.

## Quick reference

| Firmware target or role | Infrastructure WiFi | MQTT | What WiFi does |
|---|---:|---:|---|
| `*_repeater` | Build-dependent on ESP32 | No | FULL builds provide WebConfig, browser OTA, and the TCP 5001 LoRa-OTA seeder while WiFi is active |
| `*_repeater_observer_mqtt` | Yes | Yes | Uplinks heard and selected transmitted LoRa packets; repeating remains enabled by default |
| `*_room_server` | Build-dependent on ESP32 | No | FULL builds provide WebConfig, browser OTA, and the TCP 5001 LoRa-OTA seeder while WiFi is active |
| `*_room_server_observer_mqtt` | Yes | Yes | Runs the room server and uplinks radio traffic |
| `*_companion_radio_wifi` | Yes | No | Exposes the MeshCore companion protocol on TCP port 5000 |
| `*_companion_radio_wifi_mqtt` | Yes | Yes | Runs both the TCP companion interface and the MQTT uplink |
| USB, BLE, or serial companion | No | No | Uses the transport named by the target instead |
| `*_repeater_bridge_espnow` | Build-dependent on ESP32 | No | Uses ESP-NOW for its bridge; a FULL build also exposes the TCP 5001 LoRa-OTA seeder whenever WiFi is usable |
| RS232 bridge | No | No | Bridges through a serial interface |
| Ethernet repeater/room server | No WiFi | No | Uses wired Ethernet for its role-specific network interface |
| `*_sensor` | Build-dependent on ESP32 | No | A FULL ESP32 sensor can expose the TCP 5001 LoRa-OTA seeder through its browser-OTA setup AP |
| Terminal-chat or KISS modem | No in current targets | No | Uses LoRa and its role-specific local interface |
| `*_lora_ota_no_external_sensors` | On demand on ESP32 | No | Lean LoRa-OTA repeater image; ESP32 builds retain the compact `start ota` browser uploader |

Direct on-device WiFi and MQTT are currently ESP32 features. nRF52, STM32, and
the currently enabled RP2040 targets do not run this MQTT bridge. An nRF52
connected to a Raspberry Pi can still be logged or uplinked by software on the
Pi, but that is a separate host-side bridge rather than MQTT running in the
radio firmware.

## How the MQTT bridge works

The MQTT bridge is an outbound observer. It does not subscribe to MQTT topics
and does not inject broker messages into LoRa.

For a received packet, the flow is:

```text
LoRa radio -> successful packet parse -> MQTT capture queue -> broker slots
                                  \----> normal MeshCore filtering/handling
```

Packets transmitted by the node can also be queued according to `mqtt.tx`.
Receive capture happens before the packet is passed to the normal MeshCore
flood filters and forwarding decision. A packet may therefore be observed on
MQTT even when a later scope, path, region, duplicate, or repeat rule prevents
the node from forwarding it over LoRa.

MQTT publication and LoRa repetition are separate:

- `set repeat on|off` controls whether an observer repeats eligible LoRa
  packets;
- `set bridge.enabled on|off` starts or stops the MQTT bridge;
- `set mqtt.rx on|off` controls uplinking of received packets;
- `set mqtt.tx off|advert|on` controls uplinking of transmitted packets.

Fresh MQTT observer settings are:

- bridge and repeating enabled;
- received packet uplinking enabled;
- transmitted packet uplinking set to `advert`, meaning only the node's own
  adverts are included;
- packet and status publishing enabled;
- raw publishing disabled;
- slot 1 set to `analyzer-us`;
- slot 2 set to `analyzer-eu`;
- slots 3 through 6 disabled;
- WiFi modem power saving set to `min` in the Cascade profile and `none` in
  target-default builds;
- WiFi SSID and IATA code empty.

Up to six broker configurations can be saved. The number that can be active at
once depends on the target and available memory. A non-PSRAM ESP32 should
normally use no more than two TLS/WSS brokers; some classic ESP32 targets are
reliable with only one. Excess configured slots remain saved but show as
inactive in `get mqtt.status`.

The bridge has a bounded packet queue for broker or WiFi outages and reconnects
automatically. A non-PSRAM build holds 6 packets and a PSRAM build holds 50.
When the queue is full, the oldest item is replaced; after five minutes with no
broker connected, stale queued packets are flushed. MQTT packet delivery is
best effort rather than durable storage. Each enabled slot publishes
independently, so one failed broker does not intentionally stop the other
slots.

See [MQTT_IMPLEMENTATION.md](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md) for the complete preset
list, custom broker configuration, topic formats, authentication, diagnostics,
and memory limits.

## MQTT observer setup

Most full-size ESP32 MQTT observer builds have the shared WebConfig portal. On a
fresh device with no saved SSID:

1. Join the open `MeshCore-Setup-XXXX` access point.
2. If the captive page does not open, browse to `http://192.168.4.1/`.
3. Enter WiFi, radio, identity, and MQTT settings.
4. Select **Save & Reboot**.
5. After reboot, check `get wifi.status` and `get mqtt.status`.

The setup AP is unauthenticated unless the firmware was built with
`WEBCONFIG_AP_PASSWORD`. It uses plain HTTP, so provision it in a trusted
location. When WebConfig is running on the normal LAN, repeater and room-server
builds require the node's admin password.

On an expanded FULL profile with no saved SSID, this automatic setup AP has an
absolute 30-minute window. Browser activity or a phone left associated with the
AP does not extend it. If no SSID has been saved when the window expires,
WebConfig closes and the ESP32 WiFi radio remains off automatically for the
remainder of that boot. A reboot or power cycle starts a new 30-minute setup
window; the timeout is deliberately not written to preferences. An
administrator can still override the automatic cutoff with an explicit
`start webconfig` command.

Once an SSID is saved, the provisioning cutoff no longer applies. When WiFi is
selected by `logging.output wifi|both`, the station stays enabled and keeps
trying the saved network indefinitely: ESP automatic reconnect remains on and
the explicit fallback advances through 15, 30, 60, 120, then 300-second retry
intervals, remaining at five minutes until it reconnects. MQTT broker retries
use their own backoff; a repeatedly failing broker eventually receives one
probe every 30 minutes. Selecting `logging.output off|usb` keeps the MQTT bridge
off, so saved credentials alone do not force WiFi on unless WebConfig is also
enabled explicitly.

The equivalent MQTT observer CLI setup is:

```text
set wifi.ssid Your WiFi Name
set wifi.pwd Your WiFi Password
set mqtt.iata SEA
set name MyObserver
reboot
```

The SSID and password values are the rest of the command line. Spaces are
allowed and quotes must not be added. SSIDs may contain at most 31 characters
and passwords at most 63. Leave the password value empty for an open network.

Useful checks are:

```text
get wifi.ssid
get wifi.status
get wifi.powersave
get wifi.cli
get bridge.enabled
get mqtt.rx
get mqtt.tx
get mqtt.status
get mqtt1.diag
get mqtt2.diag
```

To change a configured observer through WebConfig without leaving the portal
enabled after every reboot:

```text
start webconfig
```

This uses the LAN when WiFi is connected. To force the setup AP, the MQTT bridge
must release WiFi first:

```text
set bridge.enabled off
start webconfig ap
```

After provisioning, use **Save & Reboot**, or stop the temporary portal and
restart the bridge:

```text
stop webconfig
set bridge.enabled on
```

Current MQTT observer artifacts use the expanded FULL partition profile so
WebConfig and the complete role CLI are retained. Install the matching merged
image over USB once when moving a device from the legacy partition layout.

## WiFi companion setup

A `*_companion_radio_wifi` build replaces the BLE or USB companion link with
the MeshCore companion protocol over TCP port 5000. The phone or computer must
be able to reach the device on the same LAN.

An ESP32 `*_companion_radio_full` target keeps all three Companion links at
once: USB, BLE, and TCP port 5000. It also provides a source-only LoRa mOTA
service on ports 5001 and 5002. See the
[full Companion guide](./companion_radio_full.md) for its build, terminal mode,
and complete update-source workflow.

The LilyGo T-Beam 1W Full Companion maps one press of the physical `BOOT` button
(GPIO0) to a persistent WiFi on/off toggle. Turning WiFi off closes WebConfig,
Companion TCP, mOTA, and MQTT network services while USB, BLE, the display, GPS,
and LoRa remain available. Another BOOT press turns WiFi back on even after
reboot. WiFi/WebConfig starts first; BLE starts two seconds later so the ESP32-S3
can reserve the setup server's internal heap before both radios run together.

The companion loads runtime credentials saved in NVS. A non-placeholder
compile-time `WIFI_SSID`/`WIFI_PWD` can be used as a first-boot fallback, but
saved credentials take priority. With no credentials, its WebConfig portal
starts in setup-AP mode. With credentials, the WebUI is enabled by default on
the station IP.

Full Companion can also be provisioned from its USB terminal or TCP port 5002:

```text
get wifi.ssid
get wifi.status
set wifi.ssid SlowFi
set wifi.pwd your-password
```

Credential writes are persisted immediately. After the reply has drained, the
Companion restarts its WiFi station with the saved pair. A port-5002 client is
expected to disconnect and can reconnect at the new LAN IP. USB masks the
password as it is entered; `get wifi.pwd` is intentionally unavailable.

If the configured network remains unavailable for two minutes, the companion
opens its setup AP so the credentials can be repaired. It continues retrying
the saved network. WiFi modem sleep has its own persisted `wifi.powersave`
setting and is independent of the Companion device `powersaving` setting. The
WiFi card in Companion WebConfig exposes `none`, `min`, and `max`. Fresh
Cascade-profile builds select `min`; target-default builds select `none`.
A saved setting takes precedence after an upgrade.

WiFi-only Companions can use all three modes. A Full Companion also runs BLE,
so ESP32 WiFi/Bluetooth coexistence requires at least minimum modem sleep. It
reports `min` when an old or target-default `none` value is found and rejects a
new `none` selection while Bluetooth is compiled in. Device power saving can
therefore be turned on or off without changing the selected WiFi modem policy.

WiFi companions do not have the repeater/room-server admin CLI password model,
so their LAN WebConfig page is intentionally unauthenticated. Use them only on
a trusted LAN.

The WebConfig **Advanced** card exposes device power saving on WiFi Companion,
repeater, and room-server builds. It also exposes RX power saving on radio chips
that support receive duty cycling. RXPS can select continuous receive, levels
1-10, automatic or explicit 16/32-symbol timing assumptions, or manual
receive/sleep windows. The timing assumption does not change the radio's actual
wire preamble. The WebConfig **WiFi** card exposes WiFi modem power saving. All
three settings are persisted across reboots.

The three settings are independent. A WiFi Companion keeps its transports
available while device power saving reduces CPU and GPS idle power. An
infrastructure node can sleep when device power saving is enabled, so its WiFi
services may be temporarily unavailable. RXPS only duty-cycles the LoRa
receiver. Fresh Cascade-profile builds default to device power saving on,
RXPS on at level 8 with a 16-symbol preamble, and WiFi modem power saving at
`min` on every build that includes ESP32 WiFi. The RXPS preamble value is a
saved timing assumption, not the wire length: starting with v1.17.1.5,
SF5-SF8 packets normally use a 32-symbol physical preamble. Firmware selects
64 for the whole SF/BW tuple only when 32 cannot enable RXPS at any level and
64 can (SF5/BW250 and SF6/BW500), then 128 only when neither shorter length
works (SF5/BW500).

When `ENABLE_OTA` is included, a WiFi companion also listens on:

- TCP 5001 for the OTA folder seeder used by `motatool serve --tcp`;
- TCP 5002 for a text management console.

These ports do not replace the companion protocol on TCP 5000.
On a `companion_radio_full` build, port 5002 is the same role-specific text
terminal available over USB, including chat, remote administration, radio and
power settings, WiFi/WebConfig management, `tempradio`, and the source-only
`ota` commands. Other
OTA-enabled Companion builds keep the bounded `ota ...` console. LoRa staging
and installation on the Full Companion itself remain disabled.

Port 5002 is plaintext and has no independent login gate. Use it only on a
trusted LAN or temporary setup network, especially when entering a remote-node
admin password with the terminal's `login` command.

FULL ESP32 builds share the port 5001 folder seeder. It starts whenever that
role has a usable WiFi station or setup access point and stops when WiFi stops.
For example, a FULL repeater can run `start webconfig` to join its saved
network, then accept:

```bash
motatool serve --dir ./motas --tcp <repeater-ip>:5001 -v
```

The TCP connection supplies `.mota` files for the node to advertise and relay
over LoRa; it is not a raw `.bin` uploader. `start ota` continues to provide the
direct browser uploader on HTTP port 80. A FULL role without WebConfig but with
browser OTA support can use the `MeshCore-OTA` access point raised by
`start ota`; its seeder address is `192.168.4.1:5001`.

Only one external folder link can be active. A TCP client is rejected while
`ota folder on` is using USB serial, and disconnecting `motatool` automatically
removes the TCP folder. Port 5001 has no login layer, so expose it only on a
trusted LAN or temporary setup network. Firmware target and hash checks still
apply at the receiving node, along with its configured signature/trust policy.

When the shared seeder is running, `get wifi.status` appends its live state:

```text
OTA TCP 5001: listening
OTA TCP 5001: client connected
```

The first state means WiFi is usable and the node is waiting for
`motatool serve --tcp`. The second means a `motatool` folder is currently
attached and available for LoRa OTA service.

## WiFi companion with MQTT

A `*_companion_radio_wifi_mqtt` build combines both systems:

- the companion protocol remains available to the phone/computer on TCP 5000;
- the same WiFi station connection is shared with the MQTT bridge;
- received and selected transmitted LoRa packets can be published to the
  configured MQTT slots.

The companion owns WiFi connection and recovery in this build. The MQTT bridge
waits for that connection rather than creating a second one. Stopping MQTT does
not disable the TCP companion service.

The same WebConfig page contains the MQTT settings. MQTT companions have no
text admin CLI, so browser configuration is the normal setup method.

## WebConfig without MQTT

Full-size ESP32 repeater and room-server builds can include WebConfig even when
MQTT is absent. The portal then shows node, radio, and WiFi-related controls but
removes the MQTT wizard step and MQTT tab.

In this case WebConfig owns WiFi only while it is needed. Stopping the portal
disconnects WiFi and turns the WiFi radio off. There is no persistent MQTT
connection keeping WiFi active.

Unified FULL USB + WiFi and FULL logging-fallback repeater/room-server builds
both provide these CLI controls and status checks. The unified profile includes
the MQTT bridge; the fallback is used only where no matching MQTT environment
exists:

```text
get wifi.ssid
get wifi.status
get wifi.powersave
get wifi.cli
get webui
set wifi.ssid SlowFi
set wifi.pwd your-password
set wifi.powersave none
set wifi.cli on
```

`get wifi.status` distinguishes an unconfigured node, an inactive WiFi radio, a
station connection attempt, the setup AP, a connection failure, and a working
LAN connection. For a LAN connection it reports the SSID, IP address, and RSSI.
If the shared OTA seeder is active, the same reply also reports whether TCP
port 5001 is listening or has a `motatool` client attached.
`get wifi.powersave` reports the saved standalone WebConfig setting as `none`,
`min`, or `max`.
An inactive status is normal when `webui` is off: run `start webconfig` to
connect temporarily. Standalone credentials can be changed through WebConfig
or with the listed CLI commands. Changing the SSID or password stops an active
WebConfig session; start it again to connect with the new values. Use
`set wifi.pwd` with no value for an open network. The password is write-only
and is never returned by `get`.

`get webui` starts with the saved boot setting, then reports the current
session. For example, `> off, http://192.168.1.130/` means automatic WebConfig
startup is saved as off, but a temporary session started by `start webconfig`
is currently active at that URL.

### WebConfig CLI terminal

The terminal defaults to on. Enable or disable it from an existing admin CLI:

```text
set wifi.cli on
set wifi.cli off
get wifi.cli
```

The saved `on` setting becomes active only when the WiFi station client is
connected and WebConfig is running in LAN mode. It is never exposed on the open
setup access point. When active, the WebConfig page has a **CLI** tab for sending
commands directly to the repeater or room server. It uses the local
administrator command parser and displays one reply at a time. The terminal is
protected by the WebConfig admin login and uses remote-administrator
permissions, so commands explicitly restricted to a physical serial connection
remain unavailable.

Use **Single command** for the normal prompt, or select **Command block** to
paste up to 100 commands with one command per line. Blank lines are ignored.
The browser validates all lines first, then sends one command at a time and
waits for its reply before sending the next. The block queue is kept in the
browser only, so closing the page or losing its WiFi connection stops the
commands that have not yet been sent. Ctrl+Enter or Command+Enter starts a
block.

The up/down arrow keys recall commands entered during the current browser
session in single-command mode. Commands available in the terminal still
depend on the firmware role and build profile. Commands such as
`stop webconfig`, `set wifi.cli off`, WiFi credential changes, and reboot
operations stop the remaining block and can disconnect the page before it
receives their final reply.

## Build profiles

`build.sh` produces several profiles. A profile changes the features compiled
into a selected target; it does not change that target into another firmware
role.

| Build profile | WiFi/MQTT behavior |
|---|---|
| Standard | Uses the selected target's role. Ordinary legacy-slot ESP32 repeater/room-server artifacts omit WebConfig when needed to fit. ESP32 MQTT observer and ESP-NOW bridge targets are automatically promoted to FULL; WiFi-companion targets keep their companion partition profile. |
| Logging | Enables USB/debug packet logging and disables the MQTT bridge. CommonCLI roles persist `get/set usb.logging`; logging output itself is not a direct MQTT uplink. |
| MQTT | Builds explicit MQTT observer or WiFi-companion-MQTT targets with USB packet logging off. Non-companion ESP32 MQTT observers always use FULL expanded partitions. |
| FULL ESP32 USB + WiFi | Uses the board's MQTT target with USB packet logging and direct WiFi MQTT together, expanded dual-OTA partitions, up to 254 neighbors, LoRa OTA, and full-size ESP32 features such as WebConfig where supported. `get/set logging.output off\|usb\|wifi\|both` persists the active paths. Classic T-Beam MQTT observers retain their 50-entry table because their persistent discovery state exhausts internal DRAM at 254. |
| FULL ESP32 logging fallback | Uses the board's non-MQTT target only when no matching WiFi MQTT environment exists. It keeps debug and packet logging, expanded dual-OTA partitions, up to 254 neighbors, and LoRa OTA. Persistent `usb.logging off` also provides normal output-off operation, so ESP-NOW FULL roles need no second non-logging image. |
| LoRa-OTA no-external-sensors | A lean repeater image with no MQTT; ESP32 builds retain the compact on-demand browser WiFi uploader and 254 neighbors. |

All repeater profiles use the full 254-entry neighbor table, including standard,
logging, bridge, and LoRa-OTA builds on every supported platform. The classic
T-Beam SX1262 and SX1276 MQTT observer repeaters retain 50 entries because their
persistent MQTT discovery state leaves insufficient internal-DRAM margin at 254.

The interactive Option 1 **FULL everything** choice and the standalone FULL
command select the unified USB + WiFi image when a matching MQTT target exists;
otherwise they select the logging fallback. The build matrix no longer emits a
separate standard logging image or non-MQTT FULL twin for a role covered by the
unified image. All FULL profiles include LoRa OTA, WebConfig where supported,
up to 254 neighbors, and expanded dual-OTA partitions. Target-specific
internal-DRAM limits still apply.

FULL images change the ESP32 partition layout. Flash the matching
`*-merged.bin` once when installing that layout. A partition-layout change can
invalidate NVS, including saved WiFi, MQTT, name, and admin settings. Later
updates using the same layout normally preserve them.

## WiFi power behavior

MQTT observers and FULL standalone ESP32 repeater/room-server builds support:

```text
set wifi.powersave none
set wifi.powersave min
set wifi.powersave max
```

Fresh Cascade-profile builds default to `min`; target-default builds use
`none`, which gives the most predictable MQTT and radio performance at the
highest power use. `min` and `max` reduce power but may add latency or reduce
reliability on busy nodes. A saved operator setting takes precedence on an
upgrade. ESP32 WiFi Companions expose the same values in their WebConfig WiFi
card. Full Companion also accepts the text commands from its USB terminal and
TCP port 5002. The normal binary Companion protocol can read or write the
setting over USB, BLE, or TCP port 5000 without entering terminal mode. Full
Companion rejects `none` because its simultaneous BLE transport requires WiFi
modem sleep.

MQTT observer targets normally limit ESP32 WiFi transmit power to 11 dBm unless
the board configuration overrides `MQTT_WIFI_TX_POWER`. This setting affects
WiFi only, not LoRa transmit power.

## Recognizing the wrong firmware

`get wifi.status`, `get wifi.ssid`, `get wifi.powersave`, and `get wifi.cli`
are available on MQTT observers and on FULL non-MQTT repeater/room-server
builds with WebConfig. ESP32 WiFi Companions expose `wifi.powersave` through
their role-specific interfaces described above; they do not expose the full
infrastructure WiFi CLI family.
MQTT commands such as `get mqtt.status` and `set mqtt1.preset ...` still require
an MQTT observer target. Unknown settings return `Error: unknown setting:
<name>`. Older firmware that used the discontinued compact CLI can instead
report `Unsupported in this firmware` when a command was cut for space.

Check the complete firmware filename and role. In particular:

- `logging` does not mean MQTT;
- `ota` does not mean MQTT;
- `companion_radio_wifi` does not mean MQTT;
- the filename must contain `observer_mqtt` or `wifi_mqtt` for the corresponding
  on-device MQTT feature.

Rolling firmware back does not restore settings erased by a full flash or a
partition-table change. If the correct MQTT target still has no configuration,
provision WiFi and MQTT again.

## Troubleshooting

For an MQTT observer:

```text
get wifi.status
get bridge.enabled
get mqtt.status
get mqtt1.diag
get mqtt2.diag
```

Common causes are:

- no saved SSID or a changed password;
- blank/invalid IATA for a preset that requires it;
- the bridge disabled;
- all MQTT slots disabled;
- too many TLS/WSS slots for the available internal memory;
- WiFi power saving being too aggressive;
- the wrong firmware role or an older compact-CLI build without WebConfig.

For a WiFi companion, find its station IP in the router, connect the client to
TCP port 5000, and use the setup AP if it cannot join the saved network. MQTT
diagnostics apply only to a `wifi_mqtt` companion target.
