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

See [MQTT_IMPLEMENTATION.md](../MQTT_IMPLEMENTATION.md) for the complete preset
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

Some size-constrained, portable MQTT observer artifacts omit WebConfig so they
fit the legacy ESP32 application slot. They retain the serial/remote CLI and a
small WiFi updater. Configure those builds with the CLI.

## WiFi companion setup

A `*_companion_radio_wifi` build replaces the BLE or USB companion link with
the MeshCore companion protocol over TCP port 5000. The phone or computer must
be able to reach the device on the same LAN.

An ESP32 `*_companion_radio_full` target keeps all three Companion links at
once: USB, BLE, and TCP port 5000. It also provides a source-only LoRa mOTA
service on ports 5001 and 5002. See the
[full Companion guide](./companion_radio_full.md) for its build, terminal mode,
and complete update-source workflow.

The companion loads runtime credentials saved in NVS. A non-placeholder
compile-time `WIFI_SSID`/`WIFI_PWD` can be used as a first-boot fallback, but
saved credentials take priority. With no credentials, its WebConfig portal
starts in setup-AP mode. With credentials, the WebUI is enabled by default on
the station IP.

If the configured network remains unavailable for two minutes, the companion
opens its setup AP so the credentials can be repaired. It continues retrying
the saved network. WiFi modem sleep is forced off on WiFi companions because
modem-sleep pauses can interfere with timely LoRa radio servicing.

WiFi companions do not have the repeater/room-server admin CLI password model,
so their LAN WebConfig page is intentionally unauthenticated. Use them only on
a trusted LAN.

When `ENABLE_OTA` is included, a WiFi companion also listens on:

- TCP 5001 for the OTA folder seeder used by `motatool serve --tcp`;
- TCP 5002 for the OTA text console.

These ports do not replace the companion protocol on TCP 5000.
On a `companion_radio_full` build, port 5002 additionally accepts bounded
`tempradio` and `normalradio` commands, while LoRa staging and installation on
the Companion itself are disabled.

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

FULL MQTT and FULL logging repeater/room-server builds both provide these CLI
controls and status checks. FULL MQTT includes the MQTT bridge; FULL logging
does not:

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
| Standard | Uses the selected target's role. Ordinary portable ESP32 repeater/room-server artifacts omit WebConfig to fit the legacy app slot. Explicit MQTT and WiFi-companion targets still use WiFi. |
| Logging | Enables USB/debug packet logging and disables the MQTT bridge. Logging output is not an MQTT uplink. |
| MQTT | Builds explicit MQTT observer or WiFi-companion-MQTT targets with USB packet logging off. |
| FULL ESP32 | Uses the board's MQTT target with logging off, expanded dual-OTA partitions, up to 254 neighbors, LoRa OTA, and full-size ESP32 features such as WebConfig where supported. Classic T-Beam MQTT observers retain their 50-entry table because their persistent discovery state exhausts internal DRAM at 254. |
| FULL ESP32 logging | Uses the board's non-MQTT target with debug and packet logging enabled, expanded dual-OTA partitions, up to 254 neighbors, and LoRa OTA. |
| LoRa-OTA no-external-sensors | A lean repeater image with no MQTT; ESP32 builds retain the compact on-demand browser WiFi uploader and 254 neighbors. |

All repeater profiles use the full 254-entry neighbor table, including standard,
logging, bridge, and LoRa-OTA builds on every supported platform. The classic
T-Beam SX1262 and SX1276 MQTT observer repeaters retain 50 entries because their
persistent MQTT discovery state leaves insufficient internal-DRAM margin at 254.

The interactive Option 1 **FULL everything** choice selects the FULL logging
profile: logging is enabled and MQTT is disabled. The standalone FULL ESP32
profile and Profile 4 of the five-profile matrix use the matching MQTT target
instead. Both FULL profiles include LoRa OTA, WebConfig where supported, up to
254 neighbors, and expanded dual-OTA partitions. Target-specific internal-DRAM
limits still apply.

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

The default is `none`, which gives the most predictable MQTT and radio
performance at the highest power use. `min` and `max` reduce power but may add
latency or reduce reliability on busy nodes. WiFi companions always disable
modem sleep regardless of this observer setting.

MQTT observer targets normally limit ESP32 WiFi transmit power to 11 dBm unless
the board configuration overrides `MQTT_WIFI_TX_POWER`. This setting affects
WiFi only, not LoRa transmit power.

## Recognizing the wrong firmware

`get wifi.status`, `get wifi.ssid`, `get wifi.powersave`, and `get wifi.cli`
are available on MQTT observers and on FULL non-MQTT repeater/room-server
builds with WebConfig.
MQTT commands such as `get mqtt.status` and `set mqtt1.preset ...` still require
an MQTT observer target. Unknown settings return `Error: unknown setting:
<name>`. Older portable builds can instead report `Unsupported in this
firmware` when a command was intentionally cut for space.

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
- the wrong firmware role or a portable build without the full WebConfig CLI.

For a WiFi companion, find its station IP in the router, connect the client to
TCP port 5000, and use the setup AP if it cannot join the saved network. MQTT
diagnostics apply only to a `wifi_mqtt` companion target.
