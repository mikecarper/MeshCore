<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/WiFi/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

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

The firmware role and the build profile are separate choices. USB logging does
not itself provide MQTT. MQTT-capable Full Companions include that feature
without `mqtt` in their filename; check the artifact capability manifest.
See [feature switches by role](role_feature_switches.md) for the different
Companion and infrastructure controls and the USB web console.

## Quick reference

| Firmware target or role | Infrastructure WiFi | MQTT | What WiFi does |
|---|---:|---:|---|
| `*_repeater` | Build-dependent on ESP32 | No | FULL builds provide WebConfig, browser OTA, and the TCP 5001 LoRa-OTA seeder while WiFi is active |
| `*_repeater_observer_mqtt` | Yes | Yes | Uplinks heard and selected transmitted LoRa packets; repeating remains enabled by default |
| `*_room_server` | Build-dependent on ESP32 | No | FULL builds provide WebConfig, browser OTA, and the TCP 5001 LoRa-OTA seeder while WiFi is active |
| `*_room_server_observer_mqtt` | Yes | Yes | Runs the room server and uplinks radio traffic |
| `*_companion_radio_wifi` | Yes | No | Exposes the MeshCore companion protocol on TCP port 5000 |
| `*_companion_radio_wifi_mqtt` | Yes | Yes | Runs both the TCP companion interface and the MQTT uplink |
| `*_companion_radio_full*` | ESP32, subject to board/transport selection | Where compiled on ESP32 | Includes supported USB/wireless transports; configure MQTT through WebConfig |
| Dedicated USB, BLE, or serial companion (not Full) | No | No | Uses the transport named by the target instead |
| `*_repeater_bridge_espnow` | Build-dependent on ESP32 | No | Uses ESP-NOW for its bridge; `bridge.format wrapped|raw` selects legacy bridge framing or primary-ESP-NOW compatibility; a FULL build also exposes the TCP 5001 LoRa-OTA seeder whenever WiFi is usable |
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

On infrastructure observer builds, MQTT publication and LoRa repetition are separate:

- `set repeat on|off` controls whether an observer repeats eligible LoRa
  packets;
- `set mqtt.enabled on|off` starts or stops MQTT and preserves broker settings;
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

## Automatic network outage power saving

ESP32 MQTT firmware uses one shared outage timer for MQTT and NTP. If no broker
demonstrates a working connection for five minutes, the MQTT worker stops its
clients and NTP request before suspending background WiFi. Saved WiFi and broker
settings remain enabled. Failed requests, a WiFi association, and NTP replies
alone do not restart the outage timer or keep a failed MQTT uplink awake.

After 30 minutes, the firmware opens a recovery window lasting up to two
minutes. A broker acknowledgment or a connection that stays up for 30 seconds
returns the node to normal operation. Otherwise WiFi is suspended for another
30 minutes. An explicit management request can ask for an earlier probe.
Automatic NTP requests have a 15-second timeout; failed requests wait 30 minutes
instead of repeatedly running a blocking server search. Successful NTP retains
the daily refresh schedule.

Active browser administration, setup/OTA access points, connected Companion
clients, and an attached WiFi OTA seeder protect their network access. An idle
WebConfig listener does not prevent the cooldown. An enabled SNMP agent keeps a
working LAN connection available. ESP-NOW keeps its radio and channel; only the
failed infrastructure connection is suspended. LoRa continues operating, and
when CPU power saving is enabled, its normal sleep eligibility returns after
background WiFi shuts down. USB console and logging sleep rules still apply
separately. This policy does not automatically turn off working services or
expire an active setup access point.

See [MQTT_IMPLEMENTATION.md](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md) for the complete preset
list, custom broker configuration, topic formats, authentication, diagnostics,
and memory limits.

## MQTT observer setup

WebConfig serves gzip only, including its bootstrap and captive-portal success
pages. Static WiFi setup and browser OTA pages use the same format. Builds use
Google Zopfli 0.4.3 with 1,000 iterations; validated results are cached under
`.pio/gzip-html`. There is no Brotli dependency or second WebConfig page copy.
Browsers decompress the pages automatically over ordinary HTTP. WebConfig
returns HTTP 406 when a client explicitly excludes gzip. Its large page closes
the TCP connection gracefully after the final acknowledged bytes so browsers
do not report a completed download as aborted.
The bootstrap uses ES5 syntax and Promise-based loading, including on Android
5.1.1 with Chrome 43; newer JavaScript syntax must not prevent the page loading.

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
allowed and quotes must not be added. SSIDs may contain at most 31 characters.
ESP32 infrastructure MQTT builds with WebConfig share one canonical WiFi
configuration between this CLI and the browser portal. Passwords accept
0-63 characters or exactly 64 hexadecimal characters for a raw WPA PSK.
Leave the password value empty for an open network. Reboot after changing
credentials to apply them; an already-running MQTT worker keeps its current
connection settings until it stops and restarts.

Older observer-only credentials remain usable until canonical WiFi settings
are saved. The first CLI edit preserves the other legacy credential while
saving the complete pair. Once canonical settings exist, empty, incomplete or
unreadable canonical settings do not revive old observer credentials. Historical
SSID-only canonical settings continue to represent an open network. Builds
without this WebConfig integration retain their legacy 63-character password
limit; this change does not add MQTT support to currently disabled Pico targets.

On these ESP32 MQTT/WebConfig builds, an empty CLI SSID clears the configured
network while retaining saved broker settings:

```text
set wifi.ssid
reboot
```

Use this to return the node to unconfigured provisioning. The WebConfig setup
form still requires a nonempty SSID. After saving a new network, verify
`get wifi.status`, `get mqtt.config.valid` and `get mqtt.running`; credentials
alone do not prove a broker connection. A local `get wifi.pwd` can inspect the
password, while an authenticated remote command returns only set/unset.

Useful checks are:

```text
get wifi.ssid
get wifi.status
get wifi.powersave
get wifi.cli
get mqtt.enabled
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
set mqtt.enabled off
start webconfig ap
```

After changing WiFi credentials, use **Save & Reboot** or `reboot`. For
broker-only changes that keep the existing network, stop the temporary portal
and restart the bridge:

```text
stop webconfig
set mqtt.enabled on
```

Current MQTT observer artifacts use the expanded FULL partition profile so
WebConfig and the complete role CLI are retained. Install the matching merged
image over USB once when moving a device from the legacy partition layout.

## WiFi companion setup

Full and Bluetooth/wired-capable Companions default to infrastructure WiFi off.
Bluetooth starts where the build supports it, and saved WiFi enablement is
preserved across upgrades. Before using the setup AP or station, enable WiFi
through the USB or Bluetooth CLI:

```text
get wifi.enabled
set wifi.enabled 1
```

ESP32 simultaneous-transport builds apply the change without a reboot. SenseCAP
Indicator exclusive-transport builds and Pico W require a reboot. WiFi-only
compatibility builds without any other CLI transport retain WiFi on so they are
not left unreachable. Saving an SSID/password alone does not enable WiFi on an
opt-in build. Use `set wifi.enabled 0` to save it off again. This controls
infrastructure WiFi, not a primary ESP-NOW mesh radio.

A `*_companion_radio_wifi` build replaces the BLE or USB companion link with
the MeshCore companion protocol over TCP port 5000. The phone or computer must
be able to reach the device on the same LAN.

Most ESP32 `*_companion_radio_full` targets keep all three Companion links at
once: USB, BLE, and TCP port 5000. The two SenseCAP Indicator Full targets are
the exception: USB remains available, while a saved next-boot selector starts
exactly one secondary wireless Companion transport, either BLE or
infrastructure WiFi/TCP. On the ESP-NOW Indicator, this selector never stops the
primary ESP-NOW mesh radio. Full ESP32 images also provide a source-only LoRa
mOTA service on ports 5001 and 5002 whenever their WiFi transport is active.
See the [full Companion guide](./companion_radio_full.md) for its build,
terminal mode, and complete update-source workflow.

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
password as it is entered; `get wifi.pwd` is intentionally unavailable. The
standalone credential store accepts an empty password, ordinary passphrases up
to 63 characters, or an exact 64-character hexadecimal WPA/WPA2 PSK. A
non-hexadecimal 64-character value and every longer value are rejected.

After the saved station first connects following boot, every ESP32 WiFi
Companion requests a fresh UTC time from NTP. A verified reply updates both the
MeshCore clock and any detected hardware RTC; the next explicit refresh is due
24 hours after that success. A failed request times out without blocking the
radio loop and retries after five minutes. While WiFi is disconnected or only
the setup AP is available, the request waits for a station connection. Builds
with a configured MQTT bridge use its boot-and-daily NTP owner instead, so two
firmware services never reconfigure the ESP32 SNTP client at the same time. A
later WiFi reconnect does not add another successful-sync request; it preserves
the existing 24-hour deadline. Until a fresh internet reply succeeds, LoRa
clock consensus remains available as the fallback.

On Full Companion targets whose primary mesh radio is ESP-NOW, ESP-NOW, the
setup AP, and the infrastructure-WiFi station must share one 2.4 GHz channel.
The persisted channel defaults to 1 and can be inspected or changed from the
text terminal:

```text
get espnow.channel
set espnow.channel 6
reboot
```

Valid values are 1 through 13; use only a channel permitted in your region and
supported by the router. `set espnow.channel` saves the new value but does not
move the running radio; reboot the node to apply it. Every primary
ESP-NOW node that should communicate with it must use the same channel, and
the configured router's 2.4 GHz radio must be fixed to that channel as well.
A router left on automatic channel selection can move the infrastructure
connection away from the ESP-NOW mesh.

`espnow.channel` configures the node's primary ESP-NOW mesh radio. It is
distinct from `bridge.channel`, which applies only to the separate ESP-NOW
bridge feature. WiFi power saving does not let ESP-NOW and infrastructure WiFi
operate on different channels. On an ESP32 Full build whose primary radio is
ESP-NOW, `wifi.powersave max` is unavailable because a station using maximum
modem sleep can miss ESP-NOW broadcasts; use `min` for coexistence. The primary
mesh transport holds the driver's RF wake reference continuously, so `min`
does not suspend its ESP-NOW receiver.

Espressif long-range WiFi (LR) trades speed for reach: its raw rates are
250 or 500 kbit/s, with up to about 1 km in suitable line-of-sight conditions.
LR traffic needs compatible Espressif hardware at both ends. An LR-enabled
access point sends LR beacons that ordinary phones, routers and Raspberry Pis
cannot receive. A mixed b/g/n+LR station can still join an ordinary router,
using normal WiFi rates for that connection. MeshCore's setup and OTA access
points therefore use ordinary b/g/n. See
[Espressif's LR compatibility guide](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-guides/wifi.html#long-range-lr).

ESP-NOW compatibility also depends on the bytes and PHY used above the common
Espressif transport. The historical `*_repeater_bridge_espnow` firmware wraps
each MeshCore packet with a magic value, checksum, and secret-based XOR, while
`Generic_ESPNOW` and `SenseCapIndicator-ESPNow` send the serialized MeshCore
packet directly using the ESP-NOW LR PHY. Updated bridge firmware can select
either behavior without changing the board target:

```text
get bridge.format
set bridge.channel 1
set bridge.format raw
```

On a Heltec V4, the ordinary `companion_radio_full` image remains a
LoRa-primary Companion and does not instantiate this bridge. Select and flash
the existing `heltec_v4_repeater_bridge_espnow` role before using these
commands. Treat that Companion-to-repeater change as a role/partition
migration: back up the node and use the exact V4 merged install artifact.

`wrapped` remains the default and interoperates with existing bridge nodes
that use the same channel and `bridge.secret`. `raw` interoperates with
primary-ESP-NOW nodes and other raw bridges; it requires channel 1-13, enables
LR reception, forces LR transmission, and ignores `bridge.secret`. Receive
parsing is strict to the selected format, so migrate peers deliberately rather
than expecting one bridge to accept both. Match `bridge.channel` on the LoRa
gateway to the primary nodes' `espnow.channel`; a primary-node channel change
takes effect after its reboot.

Raw mode retains MeshCore's normal packet parsing, authentication/encryption,
and the bridge's duplicate suppression, but it does not provide the wrapper's
secret-based network isolation. That XOR wrapper is itself only lightweight
isolation, not cryptographic security. Multiple LoRa/ESP-NOW gateways covering
the same nodes can temporarily amplify duplicates, even though seen-packet
tracking prevents an individual bridge from immediately echoing a packet back.

If the configured network remains unavailable for two minutes, the companion
opens its setup AP so the credentials can be repaired. It continues retrying
the saved network. WiFi modem sleep has its own persisted `wifi.powersave`
setting and is independent of the Companion device power-saving setting. The
WiFi card in Companion WebConfig exposes `none`, `min`, and `max`. Fresh
Cascade-profile builds select `min`; target-default builds select `none`.
A saved setting takes precedence after an upgrade.

WiFi-only Companions can use all three modes. A Full Companion that runs WiFi
and BLE simultaneously requires at least minimum modem sleep. It reports `min`
when an old or target-default `none` value is found and rejects a new `none`
selection. When ESP-NOW is also the primary mesh radio, `max` is unavailable:
the access point does not buffer ESP-NOW broadcasts for a sleeping station, so
maximum modem sleep can lose them. Firmware caps a previously saved `max` value
to and reports it as `min`, and rejects a new `max` selection.

The SenseCAP Indicator Full targets select BLE or infrastructure WiFi instead
of running both. On a WiFi-selected boot, the LoRa layout accepts
`none|min|max`, while the ESP-NOW layout accepts `none|min` and still rejects
`max`. On a BLE-selected boot infrastructure WiFi is off; the LoRa layout
accepts `min|max` for the saved WiFi setting, while ESP-NOW + BLE requires
`min`. Device power saving remains independent of this modem policy.

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
OTA-enabled Companion builds expose the same local maintenance commands as
binary command `0x42`, plus `ota ...` commands. LoRa staging
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

For browser uploads from an existing 1.17.1.8 ESP32 node, see the
[1.17.1.9 WiFi upgrade guide](upgrading_1.17.1.8_to_1.17.1.9.md).
It covers the node AP, old-page completion behavior, and choosing an
application-only image or the exact partition-migration package.

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

MQTT-capable Full Companions also combine these services. The WebConfig page
contains MQTT settings, and the USB/TCP and browser terminals accept the same
`mqtt.*` settings as infrastructure for shared features. Use
`set mqtt.enabled on|off` to control MQTT without discarding broker settings.
Use `set logging.output off|usb|wifi|both` to select both outputs together.

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
`set wifi.pwd` with no value for an open network. `get wifi.pwd` returns the saved password on a local connection; LoRa
callers cannot read it. Standalone WiFi accepts ordinary passphrases up
to 63 characters and exact 64-character hexadecimal WPA/WPA2 PSKs; other
64-character values and all longer values are rejected. MQTT observer WiFi
passwords remain limited to 63 characters by their fixed persisted layout.

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
setup access point. When active, the WebConfig page has a **CLI** tab. Repeater
and room-server commands use their local CLI after admin authentication.
The browser can run `get password`, `get prv.key`, `erase`, `set freq`,
`stats-core`, `stats-radio`, `stats-radio-diag`, `stats-packets`, and explicit
WiFi/MQTT credential getters. It also streams `get acl` and raw `log` dumps
back to the browser. Log reads pause for slow clients and resume as output is
read, using a fixed 160-byte file chunk. A dump snapshots the file length, so
live logging cannot extend it indefinitely. The browser keeps the complete
requested dump, including files larger than 64 KiB.

Companion uses the same complete terminal as USB and Full Companion TCP port
5002, including `card`, `import meshcore://...`, `list`, `to`, messages, remote
login, and delayed replies. It uses the Companion's trusted LAN. See
[Companion browser terminal](terminal_chat_cli.md#companion-wifi-browser-terminal)
for contact import, terminal ownership, and disconnect behavior.

Type a command at the prompt, or paste multiple lines and confirm the sequence.
Blank lines and comments are ignored. The device reports the supported batch
limit. The browser sends each line after the previous command finishes and
continues polling for incoming text after the sequence. `help` lists the
device's actual terminal commands; Tab completes command names and settings.

The up/down arrow keys recall commands entered during the current browser
session. Commands available in the terminal still
depend on the firmware role and build profile. Commands such as
`stop webconfig`, `set wifi.cli off`, WiFi credential changes, and reboot
operations stop the remaining block and can disconnect the page before it
receives their final reply.

## Build profiles

`build.sh` produces several profiles. A profile changes the features compiled
into a selected target; it does not change that target into another firmware
role.

This fork defaults every `build.sh` target to the USA Cascadia radio preset and
the Cascade firmware profile. The script resolves the live `USA/Canada` entry
by name, so its changing number in the downloaded menu does not affect builds.
If the preset service is unavailable, it falls back to
`910.525 MHz / BW62.5 / SF7 / CR5`. Use
`--radio-preset target --profile default` only when a build intentionally needs
the target's original radio and profile defaults. `--radio-preset usa-cascadia`
is the stable explicit name; legacy numbered choices remain accepted but their
meaning can change when the service reorders or adds presets.

| Build profile | WiFi/MQTT behavior |
|---|---|
| Standard | Uses the selected target's role. Where USB is a safe plaintext console, the same artifact embeds debug/packet logging behind persistent `get/set usb.logging`. Ordinary legacy-slot ESP32 repeater/room-server artifacts omit WebConfig when needed to fit. ESP32 MQTT observer and ESP-NOW bridge targets are automatically promoted to FULL; WiFi-companion targets keep their companion partition profile. |
| Legacy logging | No separate artifact is emitted; USB logging is part of the ordinary image. Logging output itself is not a direct MQTT uplink. |
| MQTT | Builds explicit MQTT observer or WiFi-companion-MQTT targets with USB packet logging off. Non-companion ESP32 MQTT observers always use FULL expanded partitions. |
| FULL ESP32 USB + WiFi | Uses the board's MQTT target with USB packet logging and direct WiFi MQTT together, expanded dual-OTA partitions, up to 254 neighbors, LoRa OTA, and full-size ESP32 features such as WebConfig where supported. `get/set logging.output off\|usb\|wifi\|both` persists the active paths. Classic T-Beam MQTT observers retain their 50-entry table because their persistent discovery state exhausts internal DRAM at 254. |
| FULL ESP32 logging fallback | Uses the board's non-MQTT target only when no matching WiFi MQTT environment exists. It keeps debug and packet logging, expanded dual-OTA partitions, up to 254 neighbors, and LoRa OTA. Persistent `usb.logging off` also provides normal output-off operation, so ESP-NOW FULL roles need no second non-logging image. |
| LoRa-OTA no-external-sensors | A lean repeater image with no MQTT; ESP32 builds retain the compact on-demand browser WiFi uploader and up to 254 neighbors, subject to target RAM limits. |

Repeater profiles use up to 254 neighbors. Selected Generic E22, Heltec V2,
Meshadventurer, T-Beam OTA/MQTT, and TLora MQTT profiles use 50 to preserve
internal-DRAM reserves. Consult the artifact capability manifest and
[ESP32 memory budget](research/esp32_memory_budget.md) for the exact target.

The interactive Option 1 **FULL everything** choice and the standalone FULL
command select the unified USB + WiFi image when a matching MQTT target exists;
otherwise they select the logging fallback. The build matrix no longer emits
any separate standard logging image, or a non-MQTT FULL twin for a role covered
by the unified image. Expanded Full infrastructure profiles include LoRa OTA, WebConfig where supported,
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
card and USB text terminal. Full Companion also accepts the text commands from
TCP port 5002. The normal binary Companion protocol can read or write the
setting over USB, BLE, or TCP port 5000 without entering terminal mode. A Full
Companion that runs BLE and infrastructure WiFi simultaneously rejects `none`
because coexistence requires WiFi modem sleep. An ESP32 Full Companion whose
primary mesh radio is ESP-NOW also rejects `max`; maximum
modem sleep can cause the station to miss ESP-NOW broadcasts because the access
point does not buffer them. A previously saved `max` value is capped to and
reported as `min`. The SenseCAP Indicator exception uses the active-mode rules
above rather than assuming BLE and infrastructure WiFi are active together.
ESP-NOW and
infrastructure WiFi still must use the same fixed channel.

MQTT observer targets normally limit ESP32 WiFi transmit power to 11 dBm unless
the board configuration overrides `MQTT_WIFI_TX_POWER`. This setting affects
WiFi only, not LoRa transmit power.

## Companion WiFi compatibility settings

ESP32 WiFi Companions, including Full Companion, provide two independent,
saved compatibility controls. Defaults remain `bgn` and `normal`; neither
setting changes the running connection until a reboot. They are available
through the USB terminal, framed Companion CLI over USB/BLE/TCP, and the
browser command terminal where included.

```text
get wifi.protocol
set wifi.protocol bg
reboot
```

`wifi.protocol bgn` permits ordinary 802.11b/g/n. `bg` excludes 802.11n for
access-point compatibility testing; it does not change LoRa or radio2.
B/G-only is rejected when it conflicts with a primary ESP-NOW radio or an
active long-range ESP-NOW owner. The temporary setup AP retains its existing
B/G/N compatibility policy.

The separate WiFi-only boot skips Bluetooth controller/host initialization,
not merely advertising. USB and LoRa remain available; existing Bluetooth
names, PINs and bonds are not erased. Enable and configure WiFi first, and
retain USB access when testing an unreliable WLAN:

```text
set wifi.enabled 1
set wifi.mode wifi-only
set wifi.powersave none
reboot
get wifi.status
```

Saving `none` while a WiFi-only reboot is pending queues it for that boot;
it never disables modem sleep while the current Bluetooth stack is active.
`get wifi.status` reports the connection, SSID, IP and RSSI (dBm), followed by
saved/boot/live radio settings in the same bounded reply:

```text
> connected ssid=MyWiFi ip=192.168.11.102 rssi=-74
protocol=bgn/bgn/0x07 mode=normal ps=min coex=yes ota=5001:listen
```

`protocol` lists **saved / boot-selected / live SDK bitmap** (`0x03` = B/G,
`0x07` = B/G/N). For example, `protocol=bg/bgn/0x07` shows a B/G-only change
waiting for reboot. `mode` shows one value when saved and boot choices match,
or **saved / boot-selected** when they differ, such as `wifi-only/normal`.
`ps` is the live modem-sleep mode; `coex` indicates whether Bluetooth
coexistence is required. `ota` is `off`, `<port>:listen`, or `<port>:client`.
`n/a` means the SDK could not read that value; it does not claim the radio is
off. IP and RSSI are omitted when not applicable. Individual getters remain
available, but no separate detailed-status command is needed.

Test B/G-only and WiFi-only separately to identify which change helps. A
successful local test does not establish compatibility with an unavailable AP.
Turning WiFi off/on, or using `set bluetooth off`, is not equivalent to a true
WiFi-only reboot. During that boot, Bluetooth cannot be re-enabled until
`wifi.mode normal` is saved and the device is rebooted.

To restore the usual Cascade combination:

```text
set wifi.mode normal
set wifi.protocol bgn
set wifi.powersave min
reboot
```

The SenseCAP Indicator's existing exclusive `companion.transport wifi|ble`
selector remains authoritative; it does not expose `wifi.mode`.

## Recognizing the wrong firmware

`get wifi.status`, `get wifi.ssid`, `get wifi.powersave`, and `get wifi.cli`
are available on MQTT observers and on FULL non-MQTT repeater/room-server
builds with WebConfig. ESP32 WiFi Companions also expose all four commands,
credential setters, and `start webconfig [ap]` through their USB text terminal.
Their browser CLI defaults to on in station/LAN mode.
Full Companion also exposes its complete role-specific text terminal on TCP
port 5002, including chat and streaming commands.
MQTT commands such as `get mqtt.status` and `set mqtt1.preset ...` require
MQTT code in the image, on Companion or infrastructure. Unknown settings return `Error: unknown setting:
<name>`. Older firmware that used the discontinued compact CLI can instead
report `Unsupported in this firmware` when a command was cut for space.

Check the complete firmware filename and role. In particular:

- `logging` does not mean MQTT;
- `ota` does not mean MQTT;
- `companion_radio_wifi` does not mean MQTT;
- `observer_mqtt` and `wifi_mqtt` identify explicit MQTT targets, but Full
  Companion can include MQTT without either marker; inspect its capabilities.

Rolling firmware back does not restore settings erased by a full flash or a
partition-table change. If the correct MQTT target still has no configuration,
provision WiFi and MQTT again.

## Troubleshooting

For an MQTT observer:

```text
get wifi.status
get mqtt.enabled
get mqtt.status
get mqtt1.diag
get mqtt2.diag
```

For a connection that works initially but later stops reporting, inspect each
configured slot's `pub_ack` count in `get mqttN.diag`. Accepted publishes are
not proof of delivery: a QoS 1 publish is confirmed only when the broker
acknowledges it. A quiet radio channel alone is not a failed MQTT connection.

The shared MQTT bridge recovers failed transports on its owning task, including
errors without a subsequent disconnect callback and connection attempts that
never finish. It also watches accepted QoS 1 publishes for missing broker
acknowledgments. Recovery retains the existing broker configuration, memory
admission and reconnect backoff; it does not reboot the node or cycle WiFi.
See the [MQTT recovery regression](test-results/mqtt_tls_v4_regression.md#long-running-mqtt-recovery)
for the deadlines, test coverage and hardware limitations. Older preview images
do not gain these changes until their firmware is updated.

Common causes are:

- no saved SSID or a changed password;
- blank/invalid IATA for a preset that requires it;
- the bridge disabled;
- all MQTT slots disabled;
- too many TLS/WSS slots for the available internal memory;
- WiFi power saving being too aggressive;
- the wrong firmware role or an older compact-CLI build without WebConfig.

For a WiFi companion, find its station IP in the router, connect the client to
TCP port 5000, and use the open `MeshCore-Setup-XXXX` AP at
`http://192.168.4.1/` if it cannot join the saved network. Current firmware
uses standard b/g/n for the setup AP, including ESP-NOW-capable targets.
A running LR mesh conflicts with a conventional access point; current firmware
refuses that AP startup and defers LR startup while an AP is active. A primary
ESP-NOW target keeps its infrastructure station and mesh on the persisted
`espnow.channel` (channel 1 by default). Its configured 2.4 GHz router and every
other primary ESP-NOW node must use that same channel.
MQTT-capable Companions also accept `get mqtt.enabled`, `get mqtt.running`,
and `get mqtt.status` in their USB/TCP and browser terminals. Extended slot
and NTP diagnostics remain infrastructure features.
