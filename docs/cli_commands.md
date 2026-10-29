<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/cli_commands/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# CLI Commands

For SolarXiao Base/Advanced/MPPT automatic detection, manual overrides, live
battery/MCU readings and charger controls, see [SolarXiao carrier profiles](solarxiao_power.md)
(`get solar`, `get solar readings`, `set solar.board auto`, `get mppt`, `set mppt`).

See [two LoRa profiles](radio_profiles.md) for `radio2`, `tempradio2`, scheduling,
RX-only mode, cross-transmission policy, and optional radio preambles.

For a saved MediaMesh second profile, use `set radio2 mediamesh`
(911.150 MHz, 250 kHz, SF7, CR 4/5, `rxtx`, automatic preamble), or
`set radio2 mediamesh-range` for 125 kHz. These leave the primary profile
and crossing policy unchanged. Both radio2 and `radio2.cross` default to `off`.
On repeaters, room servers and sensors, either preset also saves an allowed
`#mediamesh` forwarding region. `set radio2 off` or `set radio2 auto` disables
that permission while retaining the region entry. Default/home regions,
wildcard permissions and outgoing app scopes stay unchanged.
See [MediaMesh presets](radio_profiles.md#mediamesh-presets).

For supported Full Companions, see [Meshtext channel chat](radio_profiles.md#meshtext-channel-chat)
for `mt-` channels and the explicit `send mt.nodeinfo <channel slot>` command.
Chat remains plain text. A separate GPS-free NodeInfo is sent after the first
successful chat transmission per used channel/identity per boot; the explicit
command can also request one. Automatic metadata waits for a 250 ms gap after
the latest successful MT transmission; explicit requests bypass that gap.
MT messages default to 15 total attempts (the first send plus up to 14 retries)
unless a matching repeat is heard. The Companion's `flood.retry.count` can lower
that budget; `0` or `mt.hops 0` sends once. NodeInfo keeps its metadata cap and
`flood.retry.advert` gate. Scheduled beacons remain fire and forget.

See [carrier-wave RF checks](carrier_wave.md) for `cw on [seconds]`,
`cw2 on [seconds]`, and stopping a carrier with `off`.

For keyed Morse TX, use `morse send TEST DE N0CALL` and `morse stop`.
Defaults are **902.100 MHz and 25 WPM**; `set morse.freq <MHz>` and
`set morse.wpm <1–60>` save separate frequency/speed settings.
Small-flash STM32 Companion builds retain Morse TX but do not include Morse
RX by default; listen/read/stats/clear return an unsupported-RX error there.
The constrained `wio-e5-mini_repeater` omits Morse TX/RX and CW tools entirely;
normal packet TX/RX and radio2 remain available.
`get morse` shows status. See [TX-only Morse messages](carrier_wave.md#tx-only-morse-messages)
for supported characters, limits, overlap checks and the temporary pause in
packet reception. Nothing transmits automatically.

For timed Morse RX, use `morse listen <minutes>` (fractions allowed, 1 second
through 5 minutes). `morse stop` ends TX or RX. All roles retain the bounded
capture in RAM for `morse read [offset]`; follow each page's `next` value until
`next=END`. `morse clear` removes it when idle. A new successful session or
reboot replaces/removes the capture. Buffered USB/browser Full Companion ASCII
terminals show decoded progress; other terminals and binary clients request it
through the CLI, without adding
unidentified text to authenticated channel/chat messages. Normal LoRa is paused
during listening, so a remote administrator must wait for the selected session
to end before reading the capture over LoRa. See [timed reception](carrier_wave.md#timed-morse-reception-and-retained-captures)
for arming time, real chip bandwidths and truncation behavior.

For copy/paste on/off recipes and the differences from Full Companion, see
[feature switches by role](role_feature_switches.md). The
[USB web console](https://flasher.meshcore.io/console) opens the default ASCII
terminal at 115200 baud; it does not require on-device WebConfig or WiFi.

This document provides an overview of CLI commands that can be sent to MeshCore Repeaters, Room Servers and Sensors.

See [CLI Availability by Firmware Build](cli_build_matrix.md) for the role and
profile matrix. Commands depend on compiled features; some portable builds
omit WebConfig while retaining the complete role CLI and compact WiFi updater.

Small-flash Mini and STM32 Companion profiles, plus RAK4631 Full Companion,
use shorter human-readable validation replies. Command syntax, returned
values, protocol acknowledgements and destructive-operation warnings stay
the same.

See [CLI Command Availability Matrix](cli_command_availability.md) for the
command-by-command nRF52 and ESP32 build tables.

The first word of a command is case-insensitive, so `set`, `Set`, and `SET`
are equivalent, as are `get`, `Get`, and the other command verbs. The case of
arguments such as node names, passwords, and keys is left unchanged.

Repeater retries carrying the same logical request ID and original command
bytes reuse the cached result, even when command parsing trims whitespace or
splits arguments in place. Changing the timestamp alone does not execute the
operation again. Normal authentication and stale-request checks still apply.
Deferred commands and host-service replies remain bound to the full authenticated
public key, even if permission changes move ACL entries. Removing or revoking
the requester cancels pending execution; a running `setperm` can still acknowledge
its own completed change to the original sender.

Use the site search or your browser's Find command with everyday wording such
as **tx retries**, **retry attempts**, **serial logging**, or **tx power**.
**Search terms** below are alternative wording to help find a command, not
additional CLI aliases. Enter the syntax shown under **Usage**; supported
command aliases are listed there explicitly.

## Navigation

- [Operational](#operational)
- [Neighbors](#neighbors-repeater-only)
- [Statistics](#statistics)
- [Logging](#logging)
- [Information](#info)
- [Configuration](#configuration)
  - [Radio](#radio)
  - [System](#system)
    - [GPIO](#control-an-exposed-gpio)
  - [Routing](#routing)
  - [Flood Rules](#change-persistent-flood-rules-in-the-field)
  - [Group Text Moderation](#moderate-flood-group-text-by-channel-sender-and-source-path)
  - [ACL](#acl)
  - [Region Management](#region-management-v110)
    - [Region Examples](#region-examples)
  - [GPS](#gps-when-gps-support-is-compiled-in)
  - [Sensors](#sensors-when-sensor-support-is-compiled-in)
  - [Bridge](#bridge-when-bridge-support-is-compiled-in)
  - [Ethernet](#ethernet-when-ethernet-support-is-compiled-in)

---

## Operational

### Reboot the node

**Search terms:** restart, restart node, reboot device.

**Usage:** 
- `reboot`

**Note:** No reply is sent.

---

### Power-off the node

**Search terms:** turn off device, shut down, power off.

**Usage:**
- `poweroff`, or
- `shutdown`

**Note:** No reply is sent.

---

### Select the next application slot (ESP32)

**Usage:**

```text
get boot.slot
set boot.slot app1
get boot.slot
reboot
```

Use `app0` or `app1` to select an installed application for the next reboot.
This works through the ESP32 Repeater, Room Server, Sensor and Companion CLI
with the usual administration permissions. Selection sends a reply and does
not reboot automatically.

`get boot.slot` reports the running application, the next boot selection and
each slot's capacity, or `missing`. A capacity does not mean the slot contains
a valid image. Selection verifies the complete image through ESP-IDF and
rejects missing, corrupt, rejected or unconfirmed applications. It does not
create a second slot or install firmware.

Finish or stop an active/staged OTA update before changing slots; stop the
WiFi OTA server if it is running. Use firmware for the exact board and installed
partition layout. Both applications share NVS and filesystem settings, and a
later OTA update can overwrite the inactive application or change the next
boot selection. Automatic rollback depends on the installed bootloader.

---

### Enter the UF2 bootloader (nRF52 only)

**Search terms:** bootloader mode, USB firmware update, UF2 mode.

**Usage:**
- `uf2reset`

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

**Note:** Reboots directly into the UF2 bootloader on supported nRF52 boards.
This includes the Repeater, Room Server, Sensor, Companion, and Terminal Chat
local serial command surfaces. It is never accepted as a remote mesh command.

---

### Reset the clock and reboot
**Usage:**
- `clkreboot`

**Note:** No reply is sent.

---

### Sync the clock with the remote device
**Usage:** 
- `clock sync`

---

### Display current time in UTC
**Usage:**
- `clock`

---

### Set the time to a specific timestamp
**Usage:** 
- `time <epoch_seconds>`

**Parameters:**
- `epoch_seconds`: Unix epoch time

---

### Send a flood advert
**Usage:** 
- `advert`

---

### Send a zero-hop advert
**Usage:**
- `advert.zerohop`

---

### Ask a USB-connected host service over LoRa (Repeater Only)

**Usage:**

- `host <text>` - authenticated remote administrator only
- `get host` - show bridge state and byte limits

`host` hands one remote LoRa CLI request to a service on the repeater's USB
host and returns that service's reply over LoRa. It does not execute the text
inside the firmware. The service has 4 seconds to prove the exact request is
still pending, then 6 seconds to execute and reply. A second remote command
receives busy while either phase is pending.

Request text is limited to 155 UTF-8 bytes, or 152 bytes when a companion uses
its legacy three-byte correlation prefix. The complete LoRa reply is limited to
162 bytes. USB records use Base64URL text, a random 64-bit nonce, and the
repeater identity's Ed25519 signature. Before executing anything, the service
must return a random one-time challenge and receive the repeater's signed live
proof. This avoids any dependency on repeater/Pi clock agreement. Every
`host.reply` USB command must carry the matching request ID and nonce; it is not
accepted from LoRa or the Ethernet CLI.

The included Raspberry Pi endpoint supports exact commands for `help`,
`cpu-temp`, `hostname`, `uptime`, `load`, `memory`, `disk-free`, and
`clock status`. It also provides strictly validated `clock sync` and
`clock set <unix_epoch>` recovery actions, opt-in `network restart` and
`reboot` actions, `action status <operation_id>`, and
`run <alias> [arguments]` for locally allowlisted executables with typed
arguments. Clock changes and host recovery actions are disabled by default,
arbitrary executables are never accepted, and no action uses a shell. Clock set
accepts only canonical unsigned decimal epochs from 2020 through 2099; a root-owned
Unix-socket service revalidates the request and authenticates the local service
account with `SO_PEERCRED` before changing system time. Network restart and
reboot use a separate root-owned Unix-socket broker with fixed systemd units;
installing clock control does not grant those actions. The endpoint never uses
`sudo` for privileged host actions.
See [LoRa CLI host service](host_cli_service.md) for the complete
`meshcoretomqtt` setup and security model.

---

### LoRa OTA speed

```text
get ota.speed
set ota.speed <0.05..3>
```

`1` preserves the current OTA timing. Values below `1` slow OTA traffic;
values above `1` shorten adjustable delays. For example, `0.5` doubles those
delays and `3` divides them by three. Decimal values such as `.05` work.
`ota config speed <factor>` is an equivalent setter. `get ota.speed` and
`ota config` show both the saved value and the effective `packet` pacing
factor. Automatic loss response may make packet spacing slower without
changing the saved setting. The setting can be changed during a transfer.

Apply it on the source, destination, and relays. It controls all LoRa OTA
packet types on both profiles, relay timing, discovery, and OTA adverts.
Slower settings extend recovery waits and space queued OTA packets while
allowing ordinary traffic through. Faster settings retain the physical retry
allowance and the configured airtime budget, so throughput is not guaranteed
to rise in direct proportion. Proof turnaround keeps its physical allowance.
Ordinary messages, node adverts, and local staged-image verification keep their
timing. Available in OTA-enabled builds for all roles, including Companions.
See the [OTA guide](ota_user_guide.md#adjust-lora-ota-speed).

### Start or stop an Over-The-Air (OTA) firmware update

**Search terms:** WiFi OTA, wireless firmware update, OTA uploader, update firmware.

**Usage:**
- `start ota`
- `start ota ap`
- `stop ota`

On nRF52, `start ota` invokes Bluetooth DFU with the matching bootloader and
application DFU ZIP. The WiFi/AP instructions below apply to ESP32.

On ESP32, `start ota` enables WiFi as needed and serves the web upload page on
the station IP when connected to WiFi; otherwise it raises the `MeshCore-OTA`
access point. The reply gives the URL to open and tells you which network to
use. `start ota ap` always selects the access point, including when OTA is
already running on a LAN address. This is useful with client-isolated networks.

The runtime browser uploader uses HTTP Basic authentication: username `admin`,
password the node's saved admin password. The OTA hotspot reuses that password
for WPA2 when it is 8–15 printable ASCII characters; otherwise it is open and
the HTTP login still applies. Temporary partition migration and recovery apps
retain their documented access policy.

Starting OTA automatically stops WebConfig and reports `WebConfig stopped` in
the reply. The existing LAN connection stays up; a setup hotspot is replaced
by `MeshCore-OTA`. WebConfig's delayed HTTP cleanup cannot stop OTA's WiFi.
Use the USB/Bluetooth or LoRa command line to start the uploader; WebConfig's
own terminal cannot deliver the handoff reply after shutting itself down.
Stop OTA before starting WebConfig again.

FULL ESP32 builds also expose the `.mota` folder seeder on TCP port 5001
whenever WiFi is usable. This listener is independent of port 80, so a host can
run `motatool serve --dir ./motas --tcp <node-ip>:5001` while WebConfig is
active. The TCP connection auto-attaches and detaches the folder; do not run
`ota folder on` at the same time because that command selects the USB-serial
folder transport.

---

### Browser configuration portal (ESP32 repeater and room server)

**Search terms:** web UI, web interface, WiFi settings page, configuration website, WebConfig.


**Usage:**

- `start webconfig`
- `start webconfig ap`
- `stop webconfig`
- `set webui on`
- `set webui off`
- `get webui`
- `get wifi.ssid`
- `get wifi.pwd`
- `get wifi.status`
- `get wifi.powersave`
- `get wifi.cli`
- `set wifi.ssid <network name>`
- `set wifi.pwd [password]`
- `set wifi.powersave <none|min|max>`
- `set wifi.cli <on|off>`

`set webui on` is the persistent master switch. It starts the portal now and
again after future reboots; `set webui off` closes it and disables that boot
start. `get webui` reports the saved on/off state plus whether the portal is
inactive, joining WiFi, serving a setup AP, or serving a LAN URL. The default is
`off` on repeater and room-server builds. An unconfigured MQTT node can open
first-time setup while no WebUI choice has been saved. An explicit
`set webui off` prevents that AP from returning after reboot and pausing
ESP-NOW. On display-equipped repeaters, an
otherwise-unused triple click toggles the same saved setting.
Consequently, `> off, http://192.168.1.130/` means the saved boot setting is
off while a temporary WebConfig session is currently active at that URL.

`start webconfig` is a temporary start that does not change the saved switch.
It serves the shared WiFi, radio, flood, loop, and status page and reports its
URL. MQTT builds also show the MQTT tab and wizard step; non-MQTT builds remove
them entirely. Sign in with the node's admin password. If the node has no saved
WiFi SSID, the command starts the `MeshCore-Setup-XXXX` captive AP at
<http://192.168.4.1/> instead.

If a saved WiFi network is unavailable and the node falls back to its setup
AP, sign in with the existing admin password before changing settings. Joining
that AP does not grant permission to replace the password. The hotspot uses
the same admin password for WPA2 when it is 8–15 printable ASCII characters.
Other valid admin passwords leave the hotspot open and require the HTTP admin
login. Protected first-time setup without a saved SSID keeps its provisioning
flow. ESP32 WiFi Companions also have a saved admin password, initially
`password`. Deliberately passwordless images retain their local no-password
policy; use them only on a trusted network.

`start webconfig ap` forces captive-AP mode. It will not interrupt an active
MQTT bridge, so run `set mqtt.enabled off` first. Use `stop webconfig` to
close either mode for the current boot. (`stop webconfig` does not change a
saved `webui on`.)
LAN mode otherwise remains active until reboot. On expanded FULL builds, an
unconfigured automatic setup AP receives one absolute 30-minute window per
boot, then powers WiFi off even if a client remains attached; rebooting starts
a new automatic window unless `webui off` is saved. An administrator can
explicitly run `start webconfig` again without rebooting. Once an SSID is saved,
the cutoff no longer applies and the selected WiFi/MQTT mode keeps reconnecting.
Other setup sessions retain
their profile's idle timeout.

Full Repeater and Room Server suspend unconfigured network bridges during
automatic first-boot setup. Automatic ESP-NOW retries remain suspended after the AP
closes, so they cannot keep an unused WiFi driver running. Saved bridge choices
remain intact; explicitly enabling the bridge resumes it for this boot.
Manual WebConfig/WiFi starts preserve an explicitly enabled ESP-NOW bridge,
including when `set 2.4ghz on` restores the previously selected services.
Stopping a browser OTA session restores only bridges that session actually
paused, rather than starting services from saved intent alone.

Every ESP32 build with WebConfig supports the browser command terminal,
including WiFi Companion and Full Companion. Companions use their complete
USB/TCP terminal on the trusted LAN, including contact import, chat, recipient
selection, and delayed replies. ESP32 roles require their WebConfig admin
login before browser terminal access; the HTTP login does not protect TCP.

To add a Companion contact, run `import meshcore://<full-contact-card-data>`,
then `list` to confirm it appears. Get the complete card with `card` on the
other Companion. A public key alone is insufficient. See the
[terminal command guide](terminal_chat_cli.md#companion-wifi-browser-terminal)
for `to`, `send`, `login`, and `cmd` examples and session ownership.

The saved `wifi.cli` setting defaults to `on`. Use `set wifi.cli off` to disable
the **CLI** tab.
`get wifi.cli` reports `off`, `on, waiting for WiFi client`, or `on, active`.
The saved setting becomes active only in station/LAN mode while the WiFi client
is connected. It is deliberately unavailable on the setup access point.
The tab displays each reply in the browser, including local diagnostics,
infrastructure `get password`, private-key and WiFi/MQTT credential reads, and
`erase`. ESP32 Companion's `get password` reports status without the secret.
Infrastructure `get acl` and raw `log` stream to the requesting browser, with
backpressure for slow clients. These maintenance commands retain their LoRa
restrictions. `set freq` works both here and through authorized LoRa CLI.
The bounded `/api/cli` endpoint accepts local maintenance commands; use
`/api/terminal` for complete ACL and log listings.
Select **Command block** to paste up to 100 commands with one command per line.
Blank lines are ignored, and every nonblank line must fit the normal 159-byte
CLI command limit. The browser sends the lines sequentially and waits for each
reply before sending the next line. The queue exists only in that browser page;
closing it stops any commands that have not yet been sent. A lost connection
also stops the remaining block.

ESP32 WiFi Companions also expose AP-compatibility controls through the same
local/framed command parser:

- `get wifi.protocol` / `set wifi.protocol <bg|bgn>` (default `bgn`)
- `get wifi.mode` / `set wifi.mode <normal|wifi-only>` (default `normal`)
- `get wifi.status` (connection plus saved/boot/live radio settings)

The same reply includes SSID, IP address and RSSI when connected,
`protocol=<saved>/<boot>/<live bitmap>`, `mode=<saved>[/<boot>]`, live modem
sleep (`ps`), Bluetooth coexistence (`coex`), and `ota=off|<port>:listen|<port>:client`.
The boot mode is omitted only when it matches the saved mode; differing values
mean a reboot is needed. No `detail` argument is required.

Both controls are saved for the next reboot, without interrupting the command
connection. `bg` excludes 802.11n and cannot override an ESP-NOW/LR owner.
`wifi-only` requires `wifi.enabled 1` and skips Bluetooth stack initialization,
while retaining USB and LoRa. To test without modem sleep, run
`set wifi.mode wifi-only`, `set wifi.powersave none`, then `reboot`; `none`
is safely deferred until that reboot if Bluetooth currently owns the radio.
Restore with `set wifi.mode normal`, `set wifi.protocol bgn`,
`set wifi.powersave min`, then `reboot`. Keep USB recovery available when
testing a WLAN that may not work. The Indicator keeps its existing
`companion.transport` selector instead of `wifi.mode`.
See [Companion WiFi compatibility settings](WiFi.md#companion-wifi-compatibility-settings).

Up/down arrow keys recall commands from the current browser session in
single-command mode. A command that stops WebConfig, changes its WiFi
connection, disables `wifi.cli`, or reboots the node stops the remaining block
and can close the page before its reply is collected.

On unified FULL USB + WiFi and FULL logging-fallback ESP32
repeater/room-server builds,
`get wifi.ssid` reports the saved standalone WebConfig network and
`get wifi.status` reports whether WiFi is unconfigured, off, connecting,
running the setup AP, failed, or connected. A connected result includes the
SSID, LAN IP, and RSSI. When the shared OTA seeder is running, the reply appends
`OTA TCP 5001: listening` or `OTA TCP 5001: client connected`. WiFi being off
is normal while WebConfig is inactive; run `start webconfig` when a temporary
connection is wanted. `get wifi.powersave` reports the saved standalone setting
as `none`, `min`, or `max`. Fresh Cascade-profile builds default to `min`;
target-default builds use `none`. A saved setting takes precedence after an
upgrade.

The WiFi `set` commands work on MQTT observers and on FULL standalone
ESP32 repeater/room-server builds. Changing the SSID or password automatically
restarts an active WebConfig session after its requests drain and reconnects
enabled MQTT with the latest saved credentials. An explicit WebUI or WiFi off
command cancels pending starts. `set wifi.pwd` with no value selects an open network.
Standalone WiFi also accepts an exact 64-character hexadecimal WPA/WPA2 PSK;
ordinary passphrases remain limited to 63 characters. Other 64-character values
and all longer values are rejected. MQTT observer WiFi passwords retain their
fixed 63-character limit.
Power-save changes are applied immediately when WiFi is running and otherwise
take effect on the next connection. `get wifi.pwd` returns the saved password
on a local connection. It remains unavailable or masked over LoRa, and the
configuration form continues to mask stored secrets.

ESP32 WiFi Companion WebConfig exposes the same `wifi.powersave` values in its
WiFi card. Every ESP32 WiFi Companion with WebConfig exposes the standalone
`wifi.ssid`, `wifi.status`, `wifi.powersave`, `wifi.cli`, and WebConfig command
families through its USB text terminal. Full Companion exposes the same
role-specific terminal on TCP port 5002. Credential writes reply before
restarting the WiFi station, so a TCP client should expect to reconnect at the
new address; USB password input is masked. Binary Companion clients can use
USB, BLE, or TCP port 5000 without the terminal-start token: send command
`0x42` (`CMD_RUN_CLI_COMMAND`) followed by the same CLI text, such as
`get wifi.powersave`, `get wifi.pwd`, `stats-core`, or `set wifi.powersave min`.
Full and Bluetooth/wired-capable Companions default to infrastructure WiFi off
and Bluetooth on where supported. Legacy WiFi-only builds without another CLI
transport retain WiFi on so they are not left unreachable.
Use `get wifi.enabled` and `set wifi.enabled 1` to inspect or persistently enable
WiFi; `set wifi.enabled 0` disables it. Saved enablement survives upgrades, and
credentials alone do not turn it on. ESP32 simultaneous-transport builds apply
the change from the main loop; exclusive-transport builds and Pico W apply it
after reboot. This does not disable a primary ESP-NOW mesh radio or add Bluetooth
to a WiFi-only build.

ESP32 WiFi Companions also accept `get wifi.tcp` over USB, BLE, or the browser
CLI. This checks the app's TCP listener (normally port `5000`), not just the
WiFi association. `listening` means the socket is open; `waiting-ip` means no
usable station or setup-AP address yet; `retrying` means listener startup
failed and will be retried automatically; `off` means no enabled listener
request. `reg=1` confirms the interface is registered for Companion commands.
`client=1` means a TCP client is connected. `acc`, `rx`, and `tx` count accepted
clients, complete received frames, and complete transmitted frames since boot;
the value after `/` is the last frame's hexadecimal opcode. `fail`, `err`, and
`retry` show listener failures, the last listener-start socket error, and seconds until retry.
`drop` gives the last client-disconnect reason. These counters contain no
message contents or credentials. An open listener does not prove a router or
guest-network firewall permits access from the phone.

Listener retries wait 1, 2, 4, 8, 16, then at most 30 seconds. They do not
reconnect WiFi, interrupt BLE/WebConfig/OTA, replace a healthy app connection,
or re-enable explicitly disabled WiFi. `get display.wifi` includes `tcp=1`
only when the listener is actually open; `services=1` alone is not proof.

The ESP32 TCP transport services input even while radio logs or other outgoing
frames are pending, so unsolicited traffic cannot starve the app handshake.
Socket sends are non-blocking. A client that makes no outbound progress for
10 seconds while data is pending is disconnected (`drop=tx-timeout`), without
blocking the other transports. `drop=tx-error` identifies a fatal send error.
Commands wait when all reply slots hold delivery-required data, rather than
executing with nowhere to return their reply. A partly sent frame stays pinned
until it finishes; input and output remain separate TCP streams.

See [local maintenance commands](terminal_chat_cli.md#local-maintenance-commands)
for the complete access rules. WiFi-only Companions accept
all three modes. A Full Companion that runs BLE and infrastructure WiFi
simultaneously rejects `none` because coexistence requires modem sleep.
Companion device power saving and LoRa `radio.rxps` remain independent. On an
ESP32 Full Companion whose primary mesh radio is ESP-NOW, `max` is also
unavailable: a station using maximum modem sleep can miss ESP-NOW broadcasts,
which the access point does not buffer for it. A previously saved conflicting
value is capped to and reported as `min`, and a new conflicting selection is
rejected. The primary mesh radio also holds the ESP-IDF WiFi wake reference
continuously so unsolicited ESP-NOW frames remain receivable; selecting `min`
does not put that primary receiver to sleep.

SenseCAP Indicator Full is the exclusive-secondary exception. On a
WiFi-selected boot, LoRa accepts `none|min|max` and ESP-NOW accepts `none|min`.
On a BLE-selected boot infrastructure WiFi is not started; LoRa accepts
`min|max` for the saved WiFi setting, while ESP-NOW + BLE requires `min`. USB
and the primary LoRa or ESP-NOW radio remain available in every mode.

#### View or change the primary ESP-NOW/WiFi channel

**Usage:**

- `get espnow.channel`
- `set espnow.channel <channel>`

**Parameters:**

- `channel`: Shared 2.4 GHz WiFi channel from `1` through `13`; choose one
  permitted in your region and supported by the router.

**Default:** `1`

This command is available when ESP-NOW is the node's primary mesh radio. The
setting is persisted. A `set` reply reports that reboot is required; until
reboot, the running ESP-NOW radio remains on its previous channel. On builds
that also provide ordinary WiFi, its station and setup AP share that channel.

After reboot, ESP-NOW, the setup AP, and the infrastructure-WiFi station use
the selected channel. Every primary ESP-NOW node that must communicate with
this node, plus the configured router's 2.4 GHz radio, must use the same fixed
channel. WiFi power saving does not allow the transports to use different
channels. On an ESP32 Full build with primary ESP-NOW, `wifi.powersave max` is
unavailable because maximum modem sleep can miss ESP-NOW broadcasts; use `min`
for coexistence. The firmware keeps the primary ESP-NOW receiver awake while
still using the `min` WiFi/Bluetooth coexistence policy.

`espnow.channel` is the channel of the primary ESP-NOW mesh transport. It is
separate from `bridge.channel`, which configures only an ESP-NOW bridge on a
firmware role whose primary mesh radio is LoRa.

The browser portal is not compiled into the two 4 MB
`LilyGo_TLora_V2_1_1_6_*_observer_mqtt` targets because it does not fit while
retaining the two app slots required for LoRa OTA. Their normal CLI settings
remain available.

### Switch the OTA release channel (observer builds)

- `ota branch` - shows the selected channel, the channel this build was made for, and the manifest base `ota check`/`ota update` will use.
- `ota branch prod` (alias `stable`) / `ota branch beta` (alias `dev`) - pull future `ota update`s from that channel. The selection is saved; run `ota update` to switch.
- `ota branch default` - follow the channel this build was made for.

The selector is saved only after the preferences transaction succeeds. A queued
`ota update` keeps the channel it checked, even if `ota branch` is changed before
the download starts. Switching channels offers the target image even when its
commit matches the running image; build numbers are not comparable across channels.

Before an image is made bootable, its compatibility tag must confirm support for
this fork's saved-settings format and required transports. Upstream observer
images without the `keymind1` capability are refused, including images that use
the different `/mqtt.json` format. A refusal leaves the current image running;
select a compatible channel to continue OTA. Production and beta URLs are supplied
by the build recipe, not accepted as arbitrary CLI URLs.

---

### Erase/Factory Reset

**Search terms:** factory defaults, reset settings, erase configuration.

**Usage:**
- `erase`

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

**Warning:** _**This is destructive!**_

---

## Neighbors (Repeater Only)

### List nearby neighbors

**Search terms:** nearby nodes, neighbor list, neighbour list.

**Usage:** 
- `neighbors`

**Note:** The output of this command is limited to the 8 most recent adverts.

**Note:** Each line is encoded as `{pubkey-prefix}:{timestamp}:{snr*4}`

---

### Remove a neighbor
**Usage:** 
- `neighbor.remove <pubkey_prefix>`
- `neighbor.remove` - remove all neighbors

**Parameters:** 
- `pubkey_prefix`: An even-length hexadecimal prefix (up to the full 64-character
  public key). All neighbors matching it are removed. Invalid keys are rejected.

Omitting the prefix clears the table. An empty or whitespace-only prefix is
also accepted for compatibility with the mobile app's **Remove All Neighbors**
action. Newly heard zero-hop adverts can populate the table again.

---

### Discover zero hop neighbors

**Usage:** 
- `discover.neighbors`

This command is available in every repeater build profile, including portable
MQTT, standard, logging, OTA, unified FULL, and FULL logging-fallback artifacts. It does not
require MQTT or PSRAM.

---

### Enable or disable discovery responses

**Availability:** Repeater firmware in newer source builds. These switches are
not included in the existing published **1.18.0.1** binaries. They do not require
MQTT or PSRAM and are not Companion, Room Server, or Sensor settings.

```text
get discovery.node.responses
set discovery.node.responses on
set discovery.node.responses off
get discovery.region.responses
set discovery.region.responses on
set discovery.region.responses off
```

| Setting | Default | What `off` does |
| --- | --- | --- |
| `discovery.node.responses` | `on` | Stops replies to normal zero-hop node-discovery requests. |
| `discovery.region.responses` | `on` | Stops replies to anonymous region/scope queries, even while adverts remain enabled. |

The two choices are independent. They apply immediately after a successful
save and survive reboot; no reboot is needed. Fresh installations and upgrades
without saved choices keep both on, preserving the previous behavior. Use a
local console or authenticated administrator CLI to change them.

Enabling node responses still honors the existing hidden-node rule: when both
`advert.interval` and `flood.advert.interval` are `0`, the repeater does not
answer normal discovery requests. Enabling a response switch also retains the
normal request-validation and rate limits.

These switches do not stop the node's own adverts, outgoing
`discover.neighbors` or `discover.scopes` commands, or automatic
[MQTT neighbor discovery](#view-or-change-periodic-neighbors-publishing-mqtt-observer-neighbors-feature).
They do not restrict an authorized administrator's `region` CLI commands.
Use `set mqtt.neighbors off` to stop the periodic outgoing MQTT discovery pass.

---

### Discover neighbor scopes (MQTT observer, neighbors feature)

Refreshes the zero-hop neighbor table, then queries each neighbor for its region
scopes and publishes the assembled table to the MQTT `neighbors` topic once.

**Usage:**
- `discover.scopes`

**Note:** Requires an MQTT observer build with the neighbors feature compiled in
(all PSRAM boards, plus non-PSRAM boards built with `MQTT_NEIGHBORS_WITHOUT_PSRAM`).
Elsewhere it replies `Err - neighbors not enabled in this build`. If a
`discover.neighbors` refresh is already in flight, the scope pass is queued behind it.

This is an outgoing query. A repeater's separate
[discovery response switches](#enable-or-disable-discovery-responses) control
whether that repeater answers other nodes' requests.

---

## Statistics

### List Rate-Limited Flood Adverts

**Usage:** `get flood.advert [page]` or `get flood.advert key <index>`

Lists tracked advert origins currently subject to a forwarding limit. Page 1
is the default, with three entries per page so replies fit both USB and admin
LoRa CLI. Each row shows its index, the first 12 public-key hex characters,
the limit reason, and the minimum wait before another new advert can pass
the automatic limiter. Ordinary forwarding filters still apply.

```text
get flood.advert
get flood.advert 2
get flood.advert key 1
```

Reasons are `quota` (the prefix's three-hour forwarding allowance is spent),
`history` (distinct receive history is full), and `bad` (the full key is under
the stricter one-advert-per-12-hours rule). A `bad` key remains listed when
`wait=0s`: one forward is eligible, but its seven-day recovery is not complete.
If several limits apply, `bad` takes precedence over `history`, then `quota`;
the displayed wait accounts for all applicable limits.

The `key` form returns the complete 64-hex public key, reason, wait, shared
prefix forwarding count/quota, shortest received hop count, and remaining
bad-list recovery time. Different full keys with the same 12-hex prefix remain
separate entries. Indices refer to the current live list and can change as
history expires; read the full key before using a targeted clear.

Queries do not clear history, spend quota, or refresh abuse/recovery timers.
Elapsed history expires normally. Origins rejected only because every table
slot protects abuse history are not retained and cannot be enumerated.
Available on repeaters, room servers, and forwarding sensors through their
ordinary local/admin CLI. Companions do not have this table.

### Clear Automatic Flood Advert History

**Usage:** `clear flood.advert all` or `clear flood.advert <64-hex-full-public-key>`

Clears automatic advert forwarding quotas, first strikes, and seven-day bad-list
history for all keys or one exact full key. Available on repeaters, room servers,
and forwarding sensors through their ordinary local/admin CLI (including admin
LoRa CLI). A missing selector, a key prefix, or a malformed key is rejected.
It does not change saved settings or manual flood rules. Reboot also clears this
RAM-only history. See [automatic advert limits](flood_filtering.md#automatic-flood-advert-limits).

### Clear Stats
**Usage:** `clear stats`

---

<a id="stats-core"></a>
### System Stats - Battery, Uptime, Queue Length and Debug Flags
**Usage:** 
- `stats-core`

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

---

<a id="stats-radio"></a>
### Radio Stats - Noise floor, Last RSSI/SNR, Airtime, Receive errors

**Search terms:** signal strength, signal quality, RSSI, SNR, radio noise.

**Usage:** `stats-radio`

`last_rssi` is the strength of the last received packet. It is not a live
signal-strength meter and will not show an unmodulated carrier; see the
[carrier-wave guide](carrier_wave.md) for a separate receiver that can measure one.

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

---

<a id="stats-packets"></a>
### Packet stats - Packet counters: Received, Sent
**Usage:** `stats-packets`

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

---

<a id="read-repeater-telemetry-history"></a>
### Read repeater and room-server telemetry history

Repeater and room-server firmware record one UTC-aligned sample every 30
minutes. Temperature and battery voltage retain 336 samples (seven rolling
days). Each detected INA219, INA226, INA260, or INA3221 voltage channel retains
192 samples (four rolling days). GPS-capable builds retain three GPS days by
default; repeaters request a seven-day default at startup. Builds without a GPS
provider omit the GPS history commands to conserve flash. All history and any
runtime retention change are held in RAM and reset after a reboot.

Radio noise-floor history is recorded separately every 15 minutes, retaining
768 positions (eight rolling days) per profile. It uses idle RSSI calibration
estimates rather than received-packet RSSI. Main-radio and radio2 history are
kept separate; failed measurements retain a Missing position in the timeline.

The feature is omitted from flash-constrained STM32 repeater and room images.
The `telemetry.tx` commands, including packet-type selections, are repeater-only.
Room servers provide local history and its CLI reads, without batch transmission.

**Usage:**

- `get telemetry.temp [page]`
- `get telemetry.volt [page]`
- `get telemetry.volt.i2c [channel [page]]`
- `get telemetry.gps [page]`
- `get telemetry.noise [page]`
- `get telemetry.noise2 [page]`
- `set telemetry.gps <days>`
- `get telemetry.tx`
- `set telemetry.tx <off|direct|path>`
- `set telemetry.tx schedule <off|1-30d>`
- `get telemetry.tx.types`
- `set telemetry.tx.types <on|off>`
- `get telemetry.tx.<type>`
- `set telemetry.tx.<type> <on|off>`
- `send telemetry.tx now`
- `get data.tx`
- `get data.tx path`
- `set data.tx path <direct|none|path>`
- `get data.tx region`
- `set data.tx region <auto|default|none|name>`

**Parameters:**

- `page`: Page `1` is always newest. Temperature and voltage pages each hold
  24 hours and accept `1`-`7`. GPS pages each hold 12 hours and accept `1`
  through twice the current GPS retention in days. Omitting the page selects
  page `1`. Noise pages contain up to 176 quarter-hour positions (44 hours)
  and accept only pages already collected, up to `1`-`5`. Use `telemetry.noise`
  for the main profile and `telemetry.noise2` for radio2.
- `channel`: Cayenne LPP channel assigned to an external voltage monitor.
  `get telemetry.volt.i2c` lists channels that have at least one non-zero
  sample. Supplying a channel returns 48 points (24 hours) per page; pages
  `1`-`4` cover all four days. A detected input whose complete retained history
  is zero is treated as disconnected and is omitted until it reports a
  non-zero voltage. An INA3221 contributes three consecutive entries, in
  hardware-input order. If it is the only detected external sensor these are
  normally LPP channels `2`, `3`, and `4`; always use the no-argument command
  as the authoritative list because earlier detected sensors shift the LPP
  numbers.
- `days`: Requested GPS retention from `1` through `30` days. Retention above
  three days uses heap memory. The allocator reduces the requested value as
  needed to leave at least 2048 bytes free and replies with the days and pages
  actually available. For example, a request can return
  `OK - telemetry.gps days=18 pages=36 requested=30`.
- `direct`: Set the shared `data.tx` path to zero-hop and, in the legacy
  combined command, enable the telemetry schedule.
- `path`: A comma-separated direct route using the same one-, two-, or
  three-byte hop hashes accepted by `set outpath`.
- `schedule`: Automatic interval in whole days. Automatic transmission is off
  on a fresh install. The retained cadence defaults to `2d`; `off` retains the
  shared route for manual test sends.
- `type`: `temp`, `volt`, `volt.i2c`, `noise`, or `noise2`, as listed below.

Local serial and remote administrator CLI sessions can read the history on
both roles. Collection uses the MCU temperature, battery voltage, external I2C
voltage monitors, and an already-valid onboard GPS fix. It does not wake GPS,
so an off, sleeping, or unfixed GPS produces a missing location sample without
changing its power-saving schedule.

Noise history combines three fresh estimates collected roughly five minutes
apart in each UTC-aligned quarter-hour. If any is at or below `-100 dBm`, use
the minimum. Otherwise, if any is at or above `-80 dBm`, use the maximum.
Otherwise, average all three. Thus a bucket containing both low and high
values uses the minimum. Minimums round down, maximums round up, and averages
round to the nearest encoded step. Incomplete buckets are Missing. A busy
channel can prevent a fresh estimate; old calibration results are not reused
as fresh measurements.

External voltage history allocates 360 bytes for each detected monitor channel
(1,080 bytes for all three INA3221 inputs). The allocation reserves all
detected inputs so a sensor connected later can start recording, but all-zero
inputs are omitted from command replies and LoRa transmission.

Packed noise history uses 480 bytes per retained profile, plus its collection
state. It does not add a 15-minute transmission schedule. USB and administrator
LoRa CLI return the same Base64 format in the existing reply buffer.

Replies contain `> ` followed by standard padded Base64. After decoding, all
multi-byte integers are little-endian. Packed fields are written most
significant bit first, oldest sample first.

Use the browser-based [Telemetry history decoder](telemetry_decoder.md) to
turn a reply into a timestamped table or downloadable CSV without uploading
the data.

`telemetry.tx` is disabled by default. Its enabled state and cadence are stored
separately from the shared `data.tx` path and region. `data.tx` defaults to
zero-hop `direct` plus `region=auto`, but it does not enable telemetry or any
other producer. For compatibility, `set telemetry.tx direct|path` updates the
shared path and enables the default `2d` schedule; new configuration should use
`set data.tx path ...` followed by `set telemetry.tx schedule ...`.

Each packet type has a separate saved on/off setting. All five default to
`on`; this does not enable the automatic schedule. These settings select both
manual and scheduled transmissions while history sampling and CLI reads
continue normally.

| Setting | Selected packets |
|---|---|
| `telemetry.tx.temp` | MCU temperature (`TTB1`) |
| `telemetry.tx.volt` | Battery voltage (`TVB1`) |
| `telemetry.tx.volt.i2c` | External I2C voltage (`IVB1`), all monitor channels |
| `telemetry.tx.noise` | Main-radio noise floor (`N1B1`) |
| `telemetry.tx.noise2` | Radio2 noise floor (`N2B1`) |

For example, omit noise packets while keeping the other selections:

```text
set telemetry.tx.noise off
set telemetry.tx.noise2 off
get telemetry.tx.types
```

`get telemetry.tx.noise` reads one selection. `set telemetry.tx.types on`
restores all five selections; `set telemetry.tx.types off` disables them all.
Turning a type off cancels its batch work that has not yet been queued. Packets
already queued can still transmit. The remaining batch keeps its 90-second
pacing. Turning a type on includes it in the next batch.

An automatic run still waits until 165 half-hour positions are available,
regardless of the selected types. It then sends the selected temperature and
battery-voltage snapshots, up to three `IVB1` packets for every populated I2C
voltage channel when selected, and the selected noise-floor snapshots. GPS is
never included in this raw transmission. The history remains boot-local, so
the first scheduled run after a reboot needs about 82.5
hours to fill. On the default two-day interval, each 82.5-hour temperature or
battery packet overlaps its predecessor by 34.5 hours. Packets are queued 90
seconds apart. A queue failure is retried after 30 minutes only for the packet
that did not queue; successfully queued packets are not duplicated.

`get telemetry.tx` includes the source ID, path, and age of this node's last
completed telemetry transmission:

```text
> on 2d id=0123456789ABCDEF i2c=0 last=30m p=direct
```

`last` uses seconds, minutes, or hours (`30s`, `30m`, `2h`; two days is `48h`).
It counts completed sends and retries of this node's `TTB1`, `TVB1`, `IVB1`,
`N1B1`, or `N2B1` packets, including manual sends while the schedule is off.
Queue acceptance and relayed telemetry do not update it, and it does not confirm
observer receipt. It shows `last=never` until the first completed send after a
reboot. The age uses uptime, so clock corrections do not affect it.

`send telemetry.tx now` is an administrator-only test action. It queues the
selected temperature and battery-voltage snapshots plus selected, available
I2C voltage chunks over the configured path. When selected, it also queues
one main-radio `N1B1` packet once a noise bucket has completed and one `N2B1`
packet when radio2 is enabled and has a completed bucket. Missing positions
are included; an all-Missing history is still sent. It uses all currently
available base positions up to 165 and all available I2C positions up to 192
per populated channel, plus the latest 255 positions per noise profile
(63 hours 45 minutes).
It works while the schedule is off and does not move the next
scheduled send time. Selected I2C or noise data can be sent without any
temperature or battery history. With all types off, it returns
`Err - no telemetry packet types enabled`. If selected types have no data
available, it returns `Err - no enabled telemetry data available`.

With a non-empty hop path, `TTB1`, `TVB1`, `IVB1`, `N1B1`, and `N2B1` telemetry
use the existing direct-message echo retries. They follow `direct.retry`,
`direct.retry.count`, `direct.retry.heard`, and the direct-retry coding-rate
policy. A matching downstream forwarding echo stops retries. The default
zero-hop `direct` route has no forwarding echo and does not use these retries.
Updated relays can retry their own forwarding hop; stock relays remain compatible
and their forwarded packets can provide the echo. The packet format is unchanged:
there is no new ACK or confirmation that the final observer received the data.

Full temperature and battery snapshots are 184-byte RAW_CUSTOM payloads.
Manual tests can be shorter while history fills. Both formats are binary,
not Base64 and not encrypted:

| Bytes | Meaning |
|---|---|
| `0`-`3` | ASCII magic `TTB1` for temperature or `TVB1` for voltage |
| `4`-`11` | First eight bytes of the repeater public key |
| `12`-`15` | First sample UTC epoch, unsigned 32-bit little-endian |
| `16`-`17` | Sample interval in minutes, unsigned 16-bit little-endian (`30`) |
| `18` | Sample count (`1`-`165`) |
| `19` onward | Oldest-to-newest encoded samples for the selected series |

Temperature codes reserve `0` for no reading, `1` for below `-50 C`, and `2`
for above `+77 C`. Codes `3` through `130` represent exact whole degrees from
`-50 C` through `+77 C`; decode them as `code - 53`. Voltage codes use the
same encoding as the paged voltage payload documented below.

Noise snapshots reuse this 19-byte telemetry header. Their magic is `N1B1`
for the main radio or `N2B1` for radio2; the interval is `15`, the count is
`1`-`255`, and data begins at byte `19`. Samples are packed 5-bit codes,
oldest first and most significant bit first, with zero padding in the last
byte. The payload is `19 + ceil(count * 5 / 8)` bytes: 179 bytes for 255
positions. No GPS coordinates are included in a noise packet. Noise packets
are additional RAW_CUSTOM telemetry snapshots, not a new MeshCore packet type.

External voltage snapshots use a separate packed layout. A full four-day
channel takes three 140-byte `IVB1` payloads of 64 points each:

| Bytes | Meaning |
|---|---|
| `0`-`3` | ASCII magic `IVB1` |
| `4`-`11` | First eight bytes of the source public key |
| `12`-`15` | First sample UTC epoch, unsigned 32-bit little-endian |
| `16`-`17` | Sample interval in minutes, unsigned 16-bit little-endian (`30`) |
| `18` | Cayenne LPP voltage channel |
| `19` | Sample count (`1`-`64`) |
| `20` onward | Oldest-to-newest packed 15-bit voltage codes |

Code `0` means missing or disconnected. Codes `1`-`32767` represent `0.02 V`
through `655.34 V` in `0.02 V` steps; decode millivolts as `code * 20`.

Match bytes `4`-`11` to the first 16 hex characters of the repeater public key
shown by its advert or `get public.key`. This compact identifier is useful for
association but is not authenticated and can be spoofed. An MQTT observer can
upload the received raw packet to LetsMesh Analyzer `/packets`; its packet hex
contains the direct-route header and path followed by this payload.

Temperature payload (`0x11`, 61 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x11` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`48`) |
| `7`-`18` | 48 packed 2-bit temperature statuses |
| `19`-`60` | 48 packed 7-bit temperatures |

Temperature status codes are `0` none, `1` value, `2` below range, and `3`
above range. For status `1`, the 7-bit temperature is an exact whole-degree
integer from `-50 C` through `+77 C`; decode it as `code - 50`. Low values use
code `0`, and high values use code `127`. The separate status map is required
because 7 bits contain exactly 128 codes, leaving no spare code for none, low,
or high when the complete range is represented at 1 C resolution. No
fractional temperature is stored or transmitted.

Voltage payload (`0x12`, 55 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x12` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`48`) |
| `7`-`54` | 48 8-bit voltage codes |

Voltage codes reserve `0` for no reading, `1` for below `1.88 V`, and `255`
for above `4.40 V`. Codes `2`-`254` represent `1.88 V` through `4.40 V` in
`0.01 V` steps; decode millivolts as `1880 + (code - 2) * 10`.

External I2C voltage payload (`0x14`, 98 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x14` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`48`) |
| `7` | Cayenne LPP voltage channel |
| `8`-`97` | 48 packed 15-bit voltage codes |

The codes have the same `0.02 V` through `655.34 V` meaning as `IVB1`.

Noise-floor payload (`0x15` main radio, `0x16` radio2; up to 117 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x15` or `0x16` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`15`) |
| `6` | Sample count (`1`-`176`) |
| `7` onward | Oldest-to-newest packed 5-bit noise-floor codes |

The same codes are used by `N1B1` and `N2B1`:

| Code | Meaning |
|---|---|
| `0` | `-120 dBm` (also clamps lower values) |
| `1` | `-119 dBm` |
| `2`-`29` | `-118` through `-64 dBm`, in 2 dB steps |
| `30` | MAX: above `-64 dBm` |
| `31` | Missing: fewer than three fresh, valid estimates |

The last byte's unused low bits are zero. A full page encodes as 156 Base64
characters, plus the existing `> ` reply prefix. The decoder keeps radio1 and
radio2 separate and rejects wrong lengths, intervals, or padding. MAX is a
status, not an invented numeric `-62 dBm` reading.

When a Companion CLI command includes its three-character correlation prefix
(for example `01|`), noise replies retain that prefix and omit the `> ` prompt.
A full `01|Base64` response then fits in 160 bytes including its terminating
NUL. The decoder accepts either reply style.

GPS payload (`0x13`, 101 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x13` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`24`) |
| `7`-`10` | Page origin latitude in signed degrees times `10^7` |
| `11`-`14` | Page origin longitude in signed degrees times `10^7` |
| `15` | Origin sample index, or `255` when the page has no GPS fix |
| `16` | Flags; bit 0 means at least one differential was clipped |
| `17`-`100` | 24 records: signed 14-bit north then signed 14-bit east |

GPS differentials use signed 14-bit two's-complement values at 10-meter
resolution and are applied to the preceding decoded valid point. The origin
sample begins at the header coordinates. A no-fix slot encodes `0,0` and does
not advance the reference; a stationary valid fix also quantizes to `0,0`.
When a page has no fixes, its origin is `0,0`, origin index is `255`, and all
differentials are `0,0`. Values outside `-8192` through `8191` are clipped and
set flag bit 0.

---

## Set Companion TX routing per contact or channel

```text
set tx.user "Alice Jones" radio2
get tx.user "Alice Jones"
set tx.channel 0 both
get tx.channel 0
set tx.channel #wardriving radio
```

Both command families accept `auto`, `radio`, `radio2`, `both`, or `off`.
`auto` uses the global crossing rules. Explicit choices override
`radio2.cross`, but radio2 must still be enabled in `rxtx` to transmit there.
`radio2` never falls back to the primary profile; `both` uses whichever of its
selected profiles can transmit. Set a target back to `auto` to remove its
override.

Use an exact contact/channel name, a zero-based channel index, or
`key:<12-64 hex digits>` for a unique contact public-key prefix. Duplicate
names/prefixes are rejected. `get tx.user` and `get tx.channel` show override
counts; queries with a target show its saved choice and current effective
profile selection. Choices are saved with the contact/channel and work for
phone-app and terminal messages. A contact's `off` setting also blocks its
ACKs and addressed responses.

See [Companion routing details and examples](radio_profiles.md#companion-tx-routing-by-contact-or-channel)
for temporary profiles, persistence, and the traffic covered by each setting.

## Companion Meshtext rebroadcasting

```text
get mt.rebroadcast
set mt.rebroadcast on|off
```

Choose `on` or `off` after `set`. This saved option defaults to `off` and is
also shown in `get mt.status`. Only broadcast text successfully decoded with
a configured channel key can be relayed; unknown-key, non-text and direct
messages are excluded. Relays wait five additional actual packet airtimes
plus flood jitter, on top of existing radio waits. Hearing another forwarded
copy cancels the pending relay. `mt.maybe_rebroadcast` is an equivalent alias.
See [optional decoded-text rebroadcasting](radio_profiles.md#optional-decoded-text-rebroadcasting)
for hop limits, cancellation and queue behavior.

## Set Companion media and SSH application TX profiles

```text
get tx.media
set tx.media auto|radio|radio2|both|off
get tx.ssh
set tx.ssh auto|radio|radio2|both|off
```

Choose one value after `set`. Both default to `auto` and are saved across
reboot. Explicit choices override a channel route for the matching application
data only; they do not change ordinary text, adverts or contact ACKs.
`tx.media auto` selects only the active `rxtx` MeshCore second profile,
including `tempradio2`, when available. Otherwise, it uses the normal
channel/global route. `tx.ssh auto` keeps that normal route.
`radio2` requires an active `rxtx` MeshCore profile and never falls back to
the primary. `both` uses the available profiles; `off` blocks sending only.
Queries include the effective `active=` selection and warn if radio2 is
unavailable for TX.

Media covers Open TAK's binary photo/voice and fetch packets. SSH requires
an application to opt into the new `0x5353` channel-data type or raw
`53 53 48 01` envelope; existing untagged SSH/IP tunnel apps are not detected
automatically. See the [application routing contract](radio_profiles.md#companion-media-and-ssh-tx-routing)
for the exact framing, precedence and limits.

## ESP32 Full Companion Ethernet tunnel

```text
get ethernet
get ethernet.radio
set ethernet.radio <MHz>,<kHz>,<SF>,<CR>[,<preamble>]
set ethernet on
set ethernet off
```

This separate `ethernet` mode carries the entire raw Ethernet link over
radio2, including SSH, while primary radio stays MeshCore. It defaults off,
saves its settings, and requires an explicitly configured supported RF tuple.
Supported ESP32 Full SX1262/SX1276 builds expose trusted-LAN KISS-over-TCP on
station port 5003. It does not add an SSH server, use `tx.ssh` tags, or bridge
Ethernet into MeshCore. Conflicting radio2 owners are not overwritten.
See [Ethernet setup, host commands and limits](radio_profiles.md#esp32-companion-ethernet-over-lora-kiss).

## Full firmware raw radio2 KISS modem

```text
get kiss
set kiss.transport usb
set kiss on
set kiss off
```

Full ESP32 and nRF52 LoRa images can keep their normal MeshCore role on
radio1 while a connected computer sends and receives raw packets on radio2.
The setting is saved and defaults to **off**. First configure an explicit
saved `radio2` tuple in `rxtx` mode. `get kiss` reports the transport, state,
packet counters and any radio2 conflict. Setting changes require a local
USB, BLE or network management connection.

With USB selected, the normal CLI stays available until the computer sends
a KISS frame delimiter (`0xC0`). That session then owns USB; functional text
and debug logging are suppressed without changing their saved preferences.
KISS RETURN (`C0 FF C0`) or a detected USB disconnect returns to the CLI.

On ESP32, `set kiss.transport tcp` selects a separate KISS listener on the
station WiFi address, port **5004**. It does not start WiFi or listen on the
setup AP. One computer can connect at a time. This is an unauthenticated raw
modem endpoint for a trusted LAN, like the existing Ethernet KISS service.

KISS port 0 DATA maps only to radio2. Packets are 1-255 bytes, unchanged over
the air, using the normal private LoRa sync word, explicit headers, CRC and
normal IQ. The host must honor transmit pacing. This interface does not add
RNode framing, fragmentation or Reticulum routing; those remain host tasks.
See [Full host KISS framing and limits](kiss_modem_protocol.md#full-firmware-host-kiss)
for the wire protocol and session rules.

## Set repeater, room-server, and sensor reply profiles

```text
get tx.reply
set tx.reply both
set tx.reply both force
set tx.reply radio
set tx.reply auto
```

Persistent choices: `auto`, `radio`, `radio2`, `both` (default), and `off`.
Explicit choices override `radio2.cross` only for CLI replies to authenticated
admins, including cached results and delayed admin GPIO command completion.
Login replies, ACKs, app telemetry/status responses, returned paths, room posts,
LoRa OTA, signed fleet acknowledgements, and non-admin CLI replies follow normal
crossing rules. Region/filter-manager roles do not get the admin override.
Append `force` to `both` or `radio2` to permit admin CLI reply TX on an active RX-only
second profile. It never enables a disabled profile or ordinary forwarding.
Omitting `force` removes that override. `off` suppresses admin CLI replies only.

See [admin CLI reply routing](radio_profiles.md#repeater-room-server-and-sensor-replies)
for temporary profiles, receive-only exceptions, and queued reply handling.

## Set Companion display rotation

SSD1306 Full Companion builds support a persisted runtime orientation:

```text
get display.rotation
set display.rotation 0
set display.rotation 90
set display.rotation 180
set display.rotation 270
```

The values are clockwise degrees. `0` clears the override and restores the
board's compiled default. Unsupported display drivers return an error.

## Select RAK OLED controller

Display-capable RAK4631 and RAK3401 images with OLED controller detection
support a saved selection in Companion, Repeater, Room Server and Sensor roles:

```text
get display.controller
set display.controller auto
set display.controller ssd1306
set display.controller sh1106
```

Choose `auto` for normal use. If a panel is shifted, wraps at its edge or is
otherwise incorrectly detected, select its controller explicitly and restart
the node. Changes are saved immediately and apply after restart; the current
screen keeps its active controller until then. `get display.controller`
reports the configured choice, active choice and whether a restart is
needed. A custom firmware build is not needed to change this choice.

Automatic detection lets the same RAK4631 image serve WisMesh Pocket v2 and
v3. Both controllers retain rotation support and use one 1 KiB framebuffer.

Detection checks the configured `DISPLAY_ADDRESS` (default `0x3C`). Only
repeated SH1106 status readings select that controller; ambiguous responses
keep SSD1306, preserving compatibility with older RAK1921 panels. With no
saved choice, the default is automatic detection unless the build sets
`OLED_CONTROLLER_OVERRIDE=1` (SSD1306) or `=2` (SH1106). This build-time
override remains a fallback for existing custom builds. Any saved selection
takes precedence, including `auto`, which restores detection even when that
fallback forces a controller.

See [the Pocket v3 issue](https://github.com/meshcore-dev/MeshCore/issues/3296).

---

## Show touchscreen touch areas

All touchscreen Companion builds using the shared touch UI support temporary
diagnostic outlines, including both SenseCAP Indicator rendering sizes:

```text
get display.touch
set display.touch on
set display.touch off
```

The default is **off at every boot**. Enabling wakes the display without
selecting anything. Dotted yellow borders mark the actual scaled tap targets;
the area under a held finger turns green and returns to yellow on release.
Disabling removes the borders on the next redraw. Pairing screens suppress the
overlay while they consume navigation. This option does not change the font,
touch calibration, navigation actions, or saved settings, and is not persisted.

Use the text terminal or Companion framed CLI (`0x42`) through an available
USB, BLE, or TCP connection. Non-touchscreen, missing-display, and legacy UI
builds report `Error: touchscreen diagnostics unsupported`. No Indicator-only
build flag is needed.

---

## Select the original Heltec V4 display

New unified original-V4 images support V4.2/V4.3 OLED, the original TFT
touchscreen expansion kit, and operation without a screen. This feature is
not present in the older 1.17.1.1 or 1.17.1.5 images. V4 R8 remains a separate
hardware target.

```text
get display.type
set display.type auto
set display.type oled
set display.type tft
set display.type off
board
```

The default `auto` requires an OLED I2C response or a stable identity from the
stock TFT assembly's touch controller. No OLED response alone is not evidence
of a TFT. When neither responds, the node runs without a screen and `board`
reports `none detected`. Touch identification establishes the stock assembly,
not LCD health. A damaged or nonstandard panel can require an override.

`oled` explicitly selects and checks the OLED driver. `tft` explicitly selects
the TFT driver and reports `TFT (forced)` rather than claiming detection.
`off` disables display initialization and reports `off`, distinct from
`none detected`. Selections apply immediately and survive reboot in the checked
`/display_type` file. A failed save leaves the live selection unchanged.

`board` combines the detected radio front end with the live display status,
for example `Heltec V4.3 OLED`, `Heltec V4.2 TFT`, or `Heltec V4.3 off`.
The radio front end identifies the V4.2/V4.3 hardware generation; it is not a
read of a PCB revision register. Use `display.mode` below for normal screen
sleep/wake policy without changing the selected hardware driver.

## Set display modes and timeouts

Companion message sound, vibration, LED, screen and GPIO rules have separate
CLI controls. See the [notification builder and CLI reference](notifications.md),
including per-contact/channel rules, trusted-contact/room `!notify` permission,
and USB test controls.

Display-equipped Companions, repeaters, room servers, and sensors have four
saved settings. The WebConfig **Display** card exposes the same controls:

| Setting | CLI | Default |
| --- | --- | --- |
| Battery mode | `get/set display.mode` | `button-pairing` on BLE Companions; `button` elsewhere |
| Battery timeout, seconds | `get/set display.timeout` | `900` on e-paper; `15` elsewhere |
| USB mode | `get/set display.usb.mode` | Same as battery mode |
| USB timeout, seconds | `get/set display.usb.timeout` | `900` on e-paper; `15` elsewhere |

Modes are `off`, `on`, `button`, `pairing`, `button-pairing`, and `automatic`.
Pairing modes are available only on builds supporting BLE pairing.

Color displays also support saved night-mode controls in the CLI and WebConfig:

| Setting | CLI | Default |
| --- | --- | --- |
| Night mode | `get/set display.night auto\|on\|off` | `auto` |
| Minutes after sunset | `get/set display.night.delay -180..180` | `60` |

Night mode uses red and black only. `auto` starts after sunset plus the selected delay and ends at sunrise. Negative
delays start before sunset. Automatic mode requires a valid clock and location;
use `on` or `off` to choose manually. Monochrome and headless boards do not
support these controls.

The device's cached GPS fix takes priority. Without GPS, it averages the latest
16 distinct neighbors with coordinates heard directly over LoRa in the last
12 hours. Relayed, bridged and imported locations do not count. With no usable
position, automatic mode keeps normal colors. The small integer approximation
uses UTC and longitude, so it needs neither a time zone nor daylight-saving
configuration. Screen brightness and screen-off timers still work normally.

On a new installation, display-equipped Companions, repeaters, room servers
and sensors keep the normal UI visible for two minutes after it is ready.
This applies only when a missing identity was created and saved to persistent
storage. Firmware updates and later boots with an existing identity use the
normal screen timings below, even if display settings were never saved.
The setup window does not change saved timeouts; an app connection or USB
power change does not restart or shorten it. Explicit `off` and manual screen
dismissal still apply. Button input can extend the screen using its normal
timeout.

On later boots, all display-equipped Companion builds show the screen in the default
`button` or `button-pairing` mode, on battery or USB power. It stays on for the
selected profile's timeout (15 minutes on e-paper, 15 seconds elsewhere).
Button input restarts that timer. This also applies to previously saved button modes. Saved `off` and
pairing-only modes still suppress the boot screen. Repeaters, room servers,
and sensors do not gain this Companion-only normal-UI boot wake.

On display-equipped Companions, repeaters, room servers, and sensors, a small
`Starting...` screen appears as soon as the board, primary filesystem, and
display are initialized, before radio initialization and serial-settle delays.
If a new identity is needed, it changes to `Generating key`. OLED/TFT screens
show a spinner and elapsed seconds, updating at most twice a second; very
small screens omit the elapsed time. E-paper adds dots from left to right,
row by row, preserving the text and earlier dots. New dots are at least five
seconds apart, slowing further when refreshes are expensive, and stop when
the grid is full. These indicators show activity rather than a percentage.

First-boot key generation and saving temporarily appear even in saved `off`
or pairing-only modes. The selected mode resumes afterward without changing
preferences. Normal startup with an existing key still respects those modes.
The normal UI's boot timeout starts only when that UI is ready, so key
generation does not consume it. Filesystem
recovery and the display driver's own initialization can still delay the first
visible frame; the firmware cannot draw before those operations finish.

Startup shows `Loading identity` while reading a saved identity. `Generating key`
and its spinner are used only when creating a replacement/new identity;
they end before flash persistence, which shows `Saving identity`. A normal
boot with a valid saved identity never regenerates it. An erased filesystem
does require a new identity unless one is restored.

On Full Companion screens, an unpaired Bluetooth PIN takes priority over a
USB/TCP connected label. Button wake shows the PIN banner without requiring
USB to be disconnected; a second navigation press can leave this preview.
An actual phone pairing request wakes pairing-enabled display modes and keeps
the banner visible until completion or its two-minute expiry. Bluetooth
scanning alone is not a pairing request.

- `off`: stays dark during normal boot and pairing; first-boot key generation
  has the temporary exception described above.
- `on`: stays on while the device is awake.
- `button`: a physical button, keyboard/navigation input, or supported touch
  wakes the display and restarts the timeout.
- `pairing`: wakes only for an actual BLE PIN request, until pairing ends or
  expires. The inactivity timeout does not shorten the pairing prompt.
- `button-pairing`: combines the button and pairing behaviors.
- `automatic`: shows startup briefly (at most four seconds), and wakes for
  messages while the app is disconnected. An app connection blanks the screen;
  buttons and pairing requests can still wake it. Disconnecting alone does not
  wake it. Network traffic and screen redraws do not extend the timeout.

Timeouts are whole seconds from `1` to `3600`. Use mode `on` for a permanent
display rather than timeout `0`. The timeout is retained but unused in `off`,
`on`, and `pairing` modes. Settings apply immediately, including while blanked;
changing power source applies the other profile without carrying over a wake
from the previous profile. Settings survive reboot and filesystem-preserving
updates in `/display_prefs`; erasing the filesystem restores the defaults.
The 15-minute e-paper default applies to battery and USB profiles on all roles.
Existing saved timeouts stay unchanged. To use 15 minutes on an existing node,
set both `display.timeout` and `display.usb.timeout` to `900`; choose a timed
mode such as `button` or `button-pairing` if the current mode is `on` or `off`.
An existing observer timeout that differs from its old 60-second default is
migrated once to both power profiles; an old zero timeout becomes mode `on`.

```text
set display.mode button-pairing
set display.timeout 15
set display.usb.mode on
set display.usb.timeout 60
```

USB profile selection uses the board's external-power detector, with USB-host
detection as a fallback. VBUS-capable boards detect chargers without a data
connection. Heltec V4 R8 uses either a detected USB host or a calibrated battery
voltage above 4.21 V. The voltage estimate is checked every five seconds and
clears at 4.20 V or below; host detection takes effect immediately. This estimate
can miss a charger while the battery is low, depends on ADC calibration, and
cannot distinguish USB from solar charging. Other boards without a power
detector may require a USB host to recognize USB power. Wi-Fi/MQTT connection or
BLE pairing is not evidence of USB power. E-paper panels can retain their last
image when powered off.

## Set Companion inbox behavior

The shared Companion UI (including T114 and R8) defaults to **History**: the
last 32 received text previews remain available on the radio after an app
downloads them. **Pending** separately counts frames waiting for the app,
including non-text frames. Downloading a frame clears it from Pending; it does
not mean someone read it. The phone's delivery queue is independent of the
32-entry preview history and is never drained by reading on the radio.

```text
get display.inbox
set display.inbox history
set display.inbox pending
set display.inbox unread
```

| Mode | Display behavior |
| --- | --- |
| `history` (default) | Shows **HISTORY** with the retained preview count, plus **Pending**. Downloaded previews remain browseable. |
| `pending` | Shows **INBOX** with the app queue count and limits the reader to previews still waiting for the app. Non-text frames count toward Pending but have no text preview. |
| `unread` | Shows **UNREAD** with the number of retained previews not yet displayed on the radio, plus **Pending**. Opening a preview on a lit display marks it read; app downloads do not. Read previews remain browseable. |

All modes retain up to 32 previews internally; switching from `pending` back to
`history` makes retained downloaded previews visible again. Evicted or rejected
queue entries are not shown as pending. Previews and local read state are in RAM
and reset on reboot. The mode applies immediately and survives reboot in
`/display_prefs`; existing version-1 display settings load with `history` as the
default. Legacy and tiny Companion UIs report `Error: inbox modes unsupported`.

Incoming messages automatically open their preview only when no client is
connected. While connected, new messages neither select another page nor move
the preview being read; history stays accessible by button or touch. The home
screen identifies connected USB and Bluetooth clients (or both), and TCP,
Ethernet, or serial clients where supported. Actual BLE pairing prompts retain
priority over connection status.

## Set display rotation

Supported Companion displays and new Heltec V4 R8 TFT Repeater, Room Server,
and Sensor images accept a saved rotation:

```text
get display.rotation
set display.rotation 0
set display.rotation 90
set display.rotation 180
set display.rotation 270
```

`0` restores the board's compiled orientation. On the R8 TFT, the other values
turn clockwise from that default. A quarter turn changes between portrait and
landscape, and the observer dashboard adapts to the new screen dimensions.
The change applies without rebooting. Changing rotation while the display is
off leaves it off; the next wake applies the pending panel rotation without
resetting the shared touch controller. WebConfig offers the same choices under
**Display → Rotation** when the running image supports rotation.

The stock [R8 Expansion Kit V2](https://heltec.org/project/v4-r8-ex/) does not
include an accelerometer. Its GNSS and touch controllers do not measure the
screen's physical orientation, so this firmware offers manual rotation rather
than an automatic orientation option. Automatic orientation would require a
supported motion sensor and its mounting orientation.

Companion saves this in its existing node preferences. Infrastructure roles
use a separate checked settings file, leaving existing node/MQTT layouts
compatible. Old published images retain their original supported commands.

## Set MQTT observer display flip

Supported observer displays, including the R8 OLED and ST7789 panels, can also
be turned 180 degrees relative to their compiled orientation:

```text
get display.flip
set display.flip off
set display.flip on
```

`0` and `1` are accepted aliases for `off` and `on`. This is intentionally
different from `display.rotation`: observer `display.flip` is an additional
relative 180-degree mounting choice. Use `display.rotation` to switch between
portrait and landscape on new R8 TFT images. The value is harmless on an observer display driver that does not
support flipping.

Flip survives reboot and firmware updates that preserve the filesystem;
erasing flash restores the `off` default. The boot log reports the saved
flip state on display-enabled observer builds.

---

## Logging

Non-compact builds compiled with `MESH_PACKET_LOGGING` emit a `RAW:` line
for received radio frames that reach the raw capture hook while `usb.logging`
is on, including Full Companion. Frames rejected by the early route policy
are not exposed to raw capture.
`RAW:` contains the complete received frame as uppercase hexadecimal, not only
its decoded payload. Packet records do not require verbose USB debug output.
Keep the host reader attached and draining the serial port: capture is best
effort, not durable storage. Native-USB transports use bounded queues and drop
records rather than block radio processing when the host stops reading;
`DROP:<count>` markers report capture loss when output capacity returns.

Successfully parsed received frames also emit the decoded RX summary, including signal,
timing, hash, type, route, and payload information. Frames that cannot be
decoded, or cannot obtain a packet-pool slot, can still emit their `RAW:` line.
Transmitted packets emit the decoded TX
summary. RAW and RX summary are separate records, not an atomic pair; a queue
drop or disconnect can leave a summary without its RAW bytes. USB log consumers
must not associate a stale RAW record with the next unrelated summary.
Size-constrained `MESH_PACKET_LOGGING_COMPACT` images instead use `R<hex>` and
`T<hex>` records; that compact stream is not the full ASCII RAW/summary format
expected by ordinary USB-to-MQTT parsers.

Ordinary non-OTA artifacts compile packet logging into the canonical image and
control its live USB output at runtime; no separate `-logging-` artifact is
emitted. Canonical RAK3401/RAK4631 repeater, room and sensor OTA pairs, plus
Tower SD repeater and Tower room pairs, also retain packet logging in both
Full-sensor and Reduced-sensor artifacts. Verbose debug stays off by default;
`get/set usb.logging` controls packet output. Their capability manifests must
verify both `logging.usb.packets` and `logging.usb.control` in the packaged
application. Specialized storage/transport aliases and other space-constrained
OTA-only targets retain their target-specific logging policy.

For other roles, use the separately named `-ota-` artifact when required. A
`-full-usb-wifi-ota-` artifact combines USB packet logging, direct WiFi MQTT,
LoRa OTA, and the expanded FULL feature set. A `-full-logging-ota-` artifact is
emitted only when that hardware/role has no matching WiFi MQTT environment.

### Report the USB connection mode

`get usb.mode` reports the primary serial backend compiled into the running
application, checking its actual primary `Serial` type as well as the USB
build flags. It is available on Repeaters, Room Servers, Sensors and Full
Companions, including the Companion protocol-14 CLI command. It is read-only:
no reset, settings write, logging toggle or packet-capture pause is performed.

The exact reply is one of:

- `usb.mode hardware-cdc-jtag`: ESP32 hardware USB Serial/JTAG, with CDC on boot.
- `usb.mode tinyusb`: ESP32 TinyUSB CDC on boot, or nRF52 using TinyUSB.
- `usb.mode uart`: ESP32 primary `Serial` is UART, not native USB CDC.
- `usb.mode unknown`: this build does not provide a proven backend mapping.

The result describes the application, not a connected adapter, host COM-port
name, optional second logging interface or current ROM/DFU bootloader. There
is no `set usb.mode`; changing the backend requires another firmware build.
Older firmware may reject this command. A flasher should retain its manual
connection option when the report is absent, unknown or conflicts with the
selected USB device; it must not guess from the board name or shared USB ID.

### Report optional settings support

`get config.cap` is a read-only, bounded settings-capability query shared by
the infrastructure CLI and Companion CLI. It reports no setting values or
secrets, saves nothing, and does not change radio or Bluetooth state. One
reply lets backup tools distinguish an unavailable setting from a real
getter failure without issuing unsupported requests for every board.

The exact v1 reply format is:

```text
config.cap v1 get=XXXXXXXX set=XXXXXXXX fixed=XXXXXXXX na=XXXXXXXX
```

Each field is eight hexadecimal digits, using these stable bit assignments:

| Bit | Setting |
| --- | --- |
| 0 | `system.watchdog` |
| 1 | `radio.cad` scan/retry/max timings |
| 2 | `radio.fem.rxgain` |
| 3 | `radio.fem.txgain` |
| 4 | `radio.rxps.rfrx_disabled` |
| 5 | `bluetooth` |
| 6 | `bluetooth.name` |
| 7 | `display.rotation` |
| 8 | `usb.logging.always` |
| 9 | `usb.logging` |
| 10 | `usb.debug` |
| 11 | `radio.preamble` saved primary preference |

`get` means a genuine reader exists; `set` means an existing setting command
can restore the value. For `radio.preamble`, restoration uses the fifth
`set radio` field, not a separate `set radio.preamble` command. `fixed`
means there is no user-configurable preference for that setting; a fixed
diagnostic may also have a `get` bit. `na` means the setting does not apply
to this board/build/role. `na` never overlaps another field, and `fixed`
never overlaps `set`. All four bits clear means unknown, not a successful
backup. Bits 12-31 are reserved and zero in v1. This limited manifest does
not claim that all stored preferences are exportable.

Infrastructure firmware has no mutable CAD scan/retry/max preferences, so
bit 1 is `fixed` there; its existing `get cad` enable/busy diagnostic remains
unchanged. Companion does store and export those timings through
`get radio.cad`. Companion watchdog status is diagnostic only where the
board can report it; it has no persisted Companion watchdog-control policy.
Front-end gain, RX power-saving RF-switch, display rotation and Bluetooth
support follow the actual board and role rather than the firmware filename.
The always-on USB logging preference is not applicable to a separate
dual-CDC logging interface.

Older firmware may not implement this query. Clients must keep legacy
per-setting reads and report unconfirmed/failed reads honestly instead of
treating an absent or malformed capability response as not applicable.

### Control live USB logging

**Search terms:** serial logging, USB debug log, enable logging, disable logging, debug output.


**Usage:**

```text
get usb.logging
set usb.logging on
set usb.logging off
set usb.logging on reboot
set usb.logging off reboot
get usb.logging.always
set usb.logging.always on
set usb.logging.always off
get usb.debug
set usb.debug on
set usb.debug off
```

Current source also provides `get usb.watchdog` and saved
`set usb.watchdog off|on|auto`. Missing watchdog state defaults to Auto, which
requires 14 continuous healthy logging-client days before saving On. Existing
five-minute USB stats polls confirm recent logging-client activity; a plugged
cable/open TTY alone does not. See the [USB logging watchdog](usb_logging_watchdog.md) for staged
five-minute USB recovery, physical-fault-only MCU resets, persisted backoff,
status fields, and safe shutdown deferrals. Older release binaries need an
upgrade before these commands exist.

`get usb.watchdog.last` shows the latest USB watchdog event: recovery action,
fault-reason bitmask, advisory node-clock epoch, uptime at the event, sequence,
and whether the record passed durable readback. It survives reboot on durable
storage. `reboot-requested` records intent, not proof that a physical reset
occurred; a later safety veto records `reboot-cancelled`. See the watchdog
documentation for storage and timestamp limitations.

**ESP32 1.17.1.5 USB logging procedure:** disable device sleep before enabling
the log stream, using separate text commands:

```text
set powersaving off
set usb.logging on
```

Verify with `get powersaving` (expect `off`) and `get usb.logging` (expect `on`).
Both settings are saved; disabling logging later does not restore power
saving. This works around the [released USB sleep bug](old-releases/1.17.1.5.md#g3-usb-disappearance-with-power-saving-enabled).
nRF52 does not need this ESP32 workaround.

These commands are compiled into ordinary USB-loggable artifacts and every
Full Companion. `usb.logging` is the saved USB output master and packet-log
gate. `usb.debug` is a separate saved preference for verbose diagnostics;
effective USB debug output requires both switches to be on. Packet RAW and
RX/TX summaries require only the master switch. `get usb.debug` reports saved
intent even when the master is off. Turning `usb.logging` off does not erase
that intent; turning it back on restores debug output if `usb.debug` is on.

CommonCLI roles save these settings in `/com_prefs`; their fresh USB logging
default is on. Full Companion and ordinary USB Companion start with the
master off on a fresh installation so diagnostics cannot corrupt framed
traffic. Verbose USB debug defaults to off for every role, including when
loading older preferences without a debug field. Updates retain an existing
saved `usb.logging` choice. Older binaries without the new `usb.debug`
command retain their previous behavior until upgraded.

On a shared USB console, an interactive text command pauses `RAW`, `RX`, and
`TX` packet log lines for five minutes. Each command restarts the timer;
logging then resumes automatically if its saved master is on. `get usb.logging`
still reports that saved setting. `set usb.logging on` or
`set logging.output usb|both` resumes packet logs immediately after a successful
save. No reboot is needed.

To keep packet logs running through commands on a shared USB console, use
`set usb.logging.always on`. This choice is saved, ends any active pause
immediately, and survives reboot. `get usb.logging.always` checks the setting.
`set usb.logging.always off` restores the default five-minute pause for
subsequent commands. The logging master remains separate: this option does
not enable logging while `usb.logging` is off. With continuous logging,
packet lines can appear between command replies.

The routine `stats-core`, `stats-radio`, and `stats-packets` polls do not start
or extend this pause. A separate logging USB port and explicit Companion
`usb.logging stream` mode also keep logging. The pause leaves command replies,
noise-floor sampling, saved history, LoRa telemetry, and direct WiFi MQTT
running. A collector reading USB packet logs misses those log copies during
the pause, including telemetry packets; this is not buffered capture.
The `usb.logging.always` setting is provided for shared consoles; nRF52 Full
Companion already keeps its dedicated logging port or explicit stream running.

`meshcoretomqtt` polls these three stats commands every 300 seconds and
publishes `noise_floor` from the `stats-radio` reply in its MQTT status. These
polls keep working during the pause. Its startup identity queries do trigger
a five-minute packet-capture gap on a shared console, as do ordinary commands
sent through its remote serial feature. Enable `usb.logging.always` to keep
packet capture running through these commands too.

For packet-only capture, use these separate text commands:

```text
set usb.debug off
set usb.logging on
```

Use `set usb.debug on` temporarily when troubleshooting, then turn it off
again to reduce USB traffic. This setting applies immediately and needs no
USB-interface reboot. It does not select a firmware variant or enable code
omitted from the image: verbose output still requires compiled `MESH_DEBUG`
or other relevant diagnostic support. A packet-only build can retain the
preference without gaining missing debug messages.

Debug off suppresses verbose diagnostic chatter, not functional CLI replies,
USB port identity/drop markers, or retained operational warnings. Readers
must still filter non-packet lines instead of treating every line as a packet.

On Full Companion these lines belong to its text terminal, not `meshcli`'s
Binary `get/set` parameter namespace. Open interface `00`, send
`+++MESHCORE-TERM-START`, and then issue the command. Running
`meshcli ... get usb.logging` or `get usb.debug` directly can instead return
`Unknown var ...` because that is a different protocol operation.

nRF52 Full Companion defaults logging to off and enumerates only USB interface
`00`, which carries Companion, terminal, and serial mOTA traffic. Enabling
logging adds interface `02`, its dedicated plaintext logging port, on the next
boot. Disabling it removes interface `02` on the next boot. A command without
the optional `reboot` argument saves the choice and reports that a reboot is
required when the USB interface count must change. The exact
`set usb.logging on reboot` and `set usb.logging off reboot` forms save the
choice, send their reply, and reboot one second later only when needed.
Interface `02` is logging-only, not an input-capable CLI. Send logging/debug
commands to interface `00`; USB debug uses the same log endpoint as packets.

On every ESP32 Full Companion, enter the USB text terminal and use
`set usb.logging on` (preceded by `set powersaving off` on 1.17.1.5) to turn that TTY into a logging-repeater-style plaintext
stream. Framed Binary Companion is unavailable on USB while logging is on. The
TTY remains an input-capable CLI, so `set usb.logging off` works on the same
TTY. After its reply, logging stops and the TTY remains in the normal ASCII
terminal, just as it does after a fresh Full installation. Send
`+++MESHCORE-TERM-STOP`, or let a Companion app send a valid framed probe, to
switch it to Binary Companion. No reboot is needed because the USB interface
count does not change.

Turning USB logging off does not disable CLI replies. nRF52 keeps Companion
frames active on interface `00`; ESP32 resumes the ordinary ASCII/Binary
switcher after the logging terminal turns logging off. This setting does not
change the node-storage capture controlled by `log start` and `log stop`.

For a USB-to-MQTT bridge, enable packet logging but leave `usb.debug` off.
Full Companion now supplies the ASCII RAW/RX/TX packet stream needed by such
consumers; it does not provide durable or lossless capture. A compatible host
must also support the role's identity/control commands and replies: packet
records alone do not make an existing bridge compatible. Companion now answers
`get public.key` with its node public key. ESP32 Full's logging TTY remains
input-capable and accepts that query. nRF52 Full's dedicated logging CDC is
not a command/reply console (its bounded stats-poll input only renews the
watchdog client lease):
a host integration must use interface `00` for commands and identity and
interface `02` for logs.
Existing single-port bridges that require CLI replies cannot simply point at
the dedicated log endpoint. This change does not add a two-port bridge driver.

Companion, Repeater, Room Server, and Sensor builds with both MQTT and USB
logging provide the same saved selector for both output paths:

```text
get logging.output
set logging.output off
set logging.output usb
set logging.output wifi
set logging.output both
```

`usb` emits `RAW:` packets for a USB-connected service such as
meshcoretomqtt. `wifi` enables the direct MQTT bridge configured by the
`wifi.*` and `mqtt.*` commands. `both` intentionally duplicates the radio
stream to both consumers; do not point both consumers at the same broker unless
the downstream setup deduplicates messages. Fresh unified Full infrastructure installs start
in `both` mode; Full Companion starts with USB logging off. To change only
MQTT, use `set mqtt.enabled on|off`; `get mqtt.enabled` checks the saved
switch and `get mqtt.running` checks whether the MQTT service is running.
Turning MQTT off preserves all broker slots and credentials.
The output selector changes the USB master, not saved `usb.debug` intent;
WiFi MQTT logging is independent of the USB debug preference.

On **1.17.1.5 ESP32**, run `set powersaving off` before selecting
`set logging.output usb` or `set logging.output both`, since those modes
enable USB logging. **WiFi/MQTT-only logging does not need this workaround
while the Repeater/Room Server MQTT bridge is running**; the released code
already blocks device sleep in that state. Check `get mqtt.running`, not
only `get mqtt.enabled`. WiFi modem power saving is a separate setting.

### Begin capture of rx log to node storage

**Search terms:** save logs, record received packets, RX logging, stored packet log.

The constrained `wio-e5-mini_repeater` omits node-storage packet logging:
`log start`, `log stop`, `log erase` and `log` are unavailable. Live
USB/MQTT packet logging, `set usb.logging on|off` and statistics remain.
Existing saved log files are not automatically deleted.

**Usage:** `log start`

---

### End capture of rx log to node storage
**Usage:** `log stop`

---

### Erase captured log
**Usage:** `log erase`

---

<a id="print-the-captured-log-to-the-serial-terminal"></a>
### Print the captured log to the requesting terminal
**Usage:** `log`

**Local connection only:** USB, direct network CLI, or Companion binary
command `0x42` where the role supports the command; unavailable over LoRa.

---

## Info

### Get the Version
**Usage:** `ver`

On Full Companion, the Binary device-info frame retains its legacy fixed-width
version field. Use `version` in the text terminal, or send `version` through
Binary protocol command `0x42` (`CMD_RUN_CLI_COMMAND`), to read the complete
untruncated firmware string together with protocol and build date.

---

### Show the hardware name
**Usage:** `board`

---

### Show the storage layout
**Usage:** `get storage.layout`

Reports a compact, read-only summary of the storage compiled into the running
firmware:

- ESP32 reports the detected internal flash size and the live partition table.
  Partition addresses after `@` are hexadecimal, sizes after `+` are KiB, and
  `*` marks the running application. `...` means later entries did not fit in
  the CLI reply.
- nRF52 reports the physical internal flash size, linked application range,
  InternalFS range, and any configured external store. Raw QSPI OTA storage
  includes its detected size and JEDEC ID; SD storage includes total, used, and
  free space.
- RP2040 and STM32 report their fixed internal flash and filesystem geometry.

The command does not write, format, or resize storage. A raw-QSPI or SD build
may briefly probe, wake, or mount its configured external storage to read its
capacity. Remote use follows the existing administrator-command permissions.

---

## Configuration

### Radio

#### Select LoRa receive and transmit directions

```text
get radio.mode
set radio.mode both
set radio.mode rx
set radio.mode tx
set radio.mode off
```

Supported ESP32 infrastructure images save this setting across reboot. The
default is `both`. `rx` permits LoRa reception and blocks all LoRa transmission,
including forwarding, adverts and replies. `tx` accepts no LoRa packets while
allowing bridge-fed and locally generated transmissions. Queued packets require a clear
CAD result on the selected profile before sending and keep backing off while
busy, without a timeout that bypasses CAD. `get cad` reports the effective
mandatory check in TX-only mode, even if its saved preference is off. A clear
check cannot guarantee collision-free delivery. `off` disables both packet directions. These
choices apply to the primary and second LoRa profiles; they do not change the
WiFi, ESP-NOW or serial bridge directions, or make an RX-only radio2 profile
transmit-capable.

Use USB or a direct WiFi CLI when changing direction. A receive-only node
cannot send its LoRa command reply, and a transmit-only node cannot receive a
LoRa command. Invalid or unreadable saved settings leave LoRa off rather than
silently enabling transmission. A known malformed record can be replaced
explicitly; uncertain storage requires a reboot before another save.
See [two RX-only V4s feeding one TX-only V4](split_radio_repeater.md).

#### Tune and save radio switching acceleration

Use local USB or BLE to change these settings on supported radio HALs.
SPI clock choices can be available even when the radio exposes no other
acceleration features. Supported acceleration features default to on,
including on boards awaiting hardware qualification. `get radio.fast.caps` lists the options the
current radio and SPI bus actually support.

| Command | Purpose |
| --- | --- |
| `get radio.fast` | Read feature selections, actual SPI clock and any pending saved policy |
| `get radio.fast.caps` | List supported feature names and SPI MHz values |
| `get radio.fast.<feature>` | Read an individual feature |
| `set radio.fast.<feature> on\|off` | Save one feature selection: `warm`, `fs`, `packet`, `iq`, `modem`, `batch`, or `buffer` |
| `get radio.fast.spi` | Read the selected MHz and actual clock in Hz |
| `set radio.fast.spi 0\|2\|4\|8\|9\|16` | Save a supported SPI clock choice; `0` restores the board default |
| `set radio.fast off` | Disable supported acceleration features and use 2 MHz where available |
| `set radio.fast on` | Enable supported features and restore the board default SPI clock |

The integer `9` is a rounded label for 8.889 MHz (8,888,888 Hz), rather than
9,000,000 Hz. It is available on ESP32 buses with a tunable radio HAL and a
clock limit high enough to support it; check `get radio.fast.caps`. The current
ESP32-C6 core cannot select this clock, so ESP32-C6 does not offer `9`. Current
nRF52, RP2040, and STM32 radio buses also do not offer this choice. The getter
shows the actual clock in Hz. Existing board defaults are unchanged.

The change applies safely without reboot and survives firmware updates. A busy
radio or failed apply/save leaves the previous policy in use. Remote LoRa
mutations and unsupported choices are rejected. Both radio profiles share the
settings. Changing them restarts the switching timing test; inspect
`get radio2.timing` after it completes. See [saved switching controls](radio_profiles.md#saved-switching-controls)
for what each feature does, hardware limits, and default/fallback behavior.

#### Select MCU and LoRa regulator modes

```text
get mcu.regulator
set mcu.regulator auto|dcdc|ldo
get radio.regulator
set radio.regulator auto|dcdc|ldo
reboot
```

These are independent controls. `auto` follows the board's existing default;
`dcdc` requests its switching regulator and `ldo` requests its linear regulator.
Settings are saved for the next reboot. The getter reports the saved choice,
active mode, automatic fallback, crash streak and whether recovery is available.
Setting a mode again, including the same value, clears that regulator's learned
fallback and crash streak. The other regulator keeps its settings.

Choices depend on the board's verified circuit. MCU selection is available on
nRF52 boards that already initialize their supported REG1 DC-DC regulator; it
does not control REG0, an external PMIC, or ESP32/RP2040 processor regulators.
Radio selection covers supported SX1262, LR1110/LR1121 and Meshnology W12 LR2021
implementations. A board with only verified LDO support rejects `dcdc`.
Unverified LR2021 switching-regulator circuits retain their existing mode.

On platforms with retained crash recovery, three consecutive qualifying crashes
while a domain is using DC-DC select LDO for that domain on the next boot.
Each crash requires a valid last battery sample of at least 50%, no more than
60 seconds old at the last heartbeat. Low/unknown battery readings, stale
samples, ordinary reboots, sleep wakes and unknown reset causes break the
streak. Five minutes of healthy operation also clears the streak; a later crash
starts a new one. LDO fallback stays selected until a mode is explicitly set
again; recovery never switches back to DC-DC automatically.

ESP32 WebConfig shows **Power regulators** under **Radio → Advanced** when
supported, with separate processor/radio choices and active/fallback status.
Only supported options are offered. **Clear fallback on restart** reapplies the
current choice through the normal save flow. Saving either control restarts the
node after the save succeeds. See [regulator recovery and hardware checks](power_saving_diagnostics.md#user-selectable-regulators-and-crash-recovery).

#### View or change this node's radio parameters
**Usage:**
- `get radio`
- `get radio.preamble`
- `set radio <freq>,<bw>,<sf>,<cr>[,<preamble|auto>]`

**Parameters:**
- `freq`: Frequency in MHz
- `bw`: Bandwidth in kHz. Most targets allow `7.8`, `10.4`, `15.6`, `20.8`, `31.25`, `41.7`, `62.5`, `125`, `250`, `500`. LR1110 targets allow `62.5`, `125`, `250`, `500`.
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)

**Set by build flag:** `LORA_FREQ`, `LORA_BW`, `LORA_SF`, `LORA_CR`

**Default:** `869.618,62.5,8,5`

`get radio` returns exactly four comma-separated fields for compatibility with
existing Companion apps. On dual-radio builds, use `get radio2` and
`get radio.timing` for profile and timing diagnostics.

`get radio.preamble` reads the **saved primary** preamble preference: `auto`
or an explicit symbol count from `8` through `65528`. It does not report a
temporary profile or replace `auto` with a computed recommendation. Backup
tools can restore it using the optional fifth `set radio` field. Omitting
that field retains the existing setter behavior: select `auto`.

**Note:** Companion USB, BLE and WebConfig changes apply automatically when
the radio is free. A busy radio keeps the saved target across disconnects.
Infrastructure saves immediately, waits two seconds, then applies when the radio
is free and its reply has drained. No reboot is needed; a permanent change ends
pending or active TempRadio sessions. The device screen shows the applied radio
settings during the wait. Companion LoRa CLI waits
for the exact reply copies to drain before applying; active TempRadio leases
reject this permanent change.
If RXPS is enabled and the saved minimum
level/preamble cannot safely cover the new radio timing, the command reply
reports the effective level and preamble, or `RXPS continuous-fast` when no
level through 10 is safe. Slower settings recalculate from the saved RXPS
minimum and return to it exactly when it is safe.

---

#### Save and switch named radio presets

**Search terms:** radio preset, saved radio settings, switch frequency, private mesh.

**Usage:**
- `get preset [n]`
- `add preset <name> <slot> <freq>,<bw>,<sf>,<cr>[,<preamble>]`
- `load preset <slot>`
- `del preset <slot>`

**Parameters:**
- `slot`: Preset slot (1-5)
- `name`: 1-12 printable characters, no spaces or commas
- `freq`, `bw`, `sf`, `cr`: Same values and validation as `set radio`
- `preamble`: Optional preamble length; omit for the default

**Default:** On first boot slot 1 is saved as `USA-Canada` (910.525 MHz, 62.5 kHz,
SF7, CR5), the US/Canada standard, not a copy of the current radio. If the running
radio differs, no slot shows `*` until you load one. A board that rejects those
settings (for example a non-US band radio) leaves slot 1 empty. Other slots are empty.
Builds can override the default with `RADIO_PRESET_DEFAULT_NAME`, `_FREQ`, `_BW`, `_SF` and `_CR`.

`get preset` lists every slot by name, for example
`1*USA-Canada 2 Private 3 - 4 - 5 - (get preset N)`; `*` marks the slot that
matches the running radio settings and `-` marks an empty slot. `get preset <n>`
shows one slot with its settings, for example `1*USA-Canada 910.525,62.5,7,5`.
Presets are stored in `/presets` and survive reboots.

`load preset <slot>` saves and applies that preset through the same path as
`set radio`: the change applies two seconds later, after the reply has been
sent. Loading from the local serial terminal is permanent.

**Remote loads revert unless confirmed.** A `load preset` sent over LoRa
(including the ESP-NOW bridge) is provisional. The reply is
`OK - loading <name> <settings>; log in again within 10 min to keep`. Log in
again as administrator on the new settings within 10 minutes to keep them.
Otherwise the node returns to its previous radio settings automatically. A
reboot during that window also reverts. Region and filter managers cannot use
preset commands, and their logins do not confirm a pending load. While a load is
pending, `get preset` starts with `pending, reverts in <m>m;` and another
remote load is refused. A remote load is also refused while a TempRadio session
is active. If the revert itself fails, it retries every minute and at every
boot.

**Build switch:** `MESH_ENABLE_RADIO_PRESETS`. Flash-constrained STM32WL builds
(such as `wio-e5-mini_repeater`) and ESP-NOW-only builds (`MESH_PRIMARY_ESPNOW`
without a LoRa primary radio) omit radio presets by default; there the preset
commands return the ordinary unknown-command reply. Other builds include them;
set `-D MESH_ENABLE_RADIO_PRESETS=0` or `=1` to override.

---

#### View or change this node's transmit power

**Search terms:** tx power, transmit strength, radio output power, dBm.

**Usage:**
- `get tx`
- `set tx <dbm>`

**Parameters:**
- `dbm`: Requested radio-chip power in dBm. The valid range depends on the
  radio family, selected PA path, and any board-specific external-PA limit.

**Set by build flag:** `LORA_TX_POWER`

**Default:** Varies by board

**Notes:** This setting only controls the power level of the LoRa chip. Some nodes have an additional power amplifier stage which increases the total output. Refer to the node's manual for the correct setting to use. **Setting a value too high may violate the laws in your country.**

The command strictly rejects malformed values. It applies power immediately
when the radio is free. A busy radio returns `OK - saved; pending`; firmware
then applies the saved value automatically, without another command or reboot.
A newer successful change replaces the pending value. A failed save is reported
as an error and does not replace the previous runtime intent. Radio 2 and
temporary radio profiles do not require a manual retry. Background application
waits for active Morse, beacons and carrier-wave tests to finish.

On LR2021, the chip range is
`-9` to `22` dBm below 1500 MHz and `-19` to `12` dBm above 1500 MHz. A lower
board-specific external-PA limit still takes precedence. A saved profile is
clamped to the applicable limit when its frequency changes; temporary radio
settings use a safe effective power without replacing the saved preference.

---

#### Change the radio parameters for a set duration
**Usage:** 
- `tempradio <freq>,<bw>,<sf>,<cr>,<timeout_mins>`
- `set tempradio <freq>,<bw>,<sf>,<cr>,<timeout_mins>` (alias)
- `get tempradio`
- `normalradio`

**Parameters:**
- `freq`: Frequency in MHz (150-2500)
- `bw`: Bandwidth in kHz (same allowed values as `set radio`)
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)
- `timeout_mins`: Duration in minutes (must be > 0)

**Notes:**
- `tempradio` is not saved to preferences and clears on reboot.
- Application starts after two seconds and waits for the radio/reply handoff.
  `get tempradio` shows the live temporary RF tuple when active, `pending` while
  waiting to start, and `off` when neither applies. A pending replacement keeps
  showing the old live tuple until the switch. `get tempradioat all` lists
  primary scheduled entries.
- On LR2021 boards such as W12, extra-SF receive detectors that conflict with
  the temporary SF/bandwidth pause for that session. Their saved settings return
  with the normal radio tuple. Compatible detectors keep working.
- On repeaters, remote `tempradio` and `normalradio` wait for their exact reply
  copies to finish, with at least one transmitted. This also covers the `set`
  alias, case-insensitive verbs, and client correlation prefixes. Failed replies
  cancel an unconfirmed temporary handoff; hard lease expiry still takes priority.
- `normalradio` cancels pending and active temporary-radio windows, then
  restores the saved radio tuple after its CLI reply has drained on the
  current channel. Permanent `radioat` entries are not removed.
- On repeaters, the reply includes total minutes plus `NdNhNm` (days, hours,
  minutes) and the watchdog policy for that session. For example:

```text
OK - temp params for 1440 mins (1d0h0m); applying in 2s; rx.watchdog=12hours
OK - temp params for 3075 mins (2d3h15m); applying in 2s; rx.watchdog=12hours
OK - temp params for 180 mins (0d3h0m); applying in 2s; rx.watchdog=off (temp<12hours)
```

Repeater timing overrides apply only while a temporary window is running:

| Temporary duration | No-RX reboot watchdog | Flood advert interval | Direct/local advert interval |
| --- | --- | --- | --- |
| Up to and including 3 hours | Off | Saved normal interval | 1 hour |
| More than 3, less than 12 hours | Off | 3 hours | 1 hour |
| 12 hours or longer | 12 hours | 3 hours | 1 hour |

The duration is the requested window length, not the decreasing time remaining.
The first temporary adverts are scheduled from successful radio activation;
hourly local adverts still run when a flood advert is also due. `get
advert.interval` and `get flood.advert.interval` report the effective intervals.
The web configuration form continues to show and edit the saved normal advert
settings. These include any changes made during a temporary session.
Expiry, cancellation, reboot, or power
loss removes the temporary overrides. Normal mode uses the saved advert
intervals and the 24-hour watchdog policy. An explicitly saved normal-mode
`rx.watchdog off` remains off outside temporary sessions.

---

#### Schedule radio parameter changes
**Usage:**
- `set radioat <freq>,<bw>,<sf>,<cr>,<start_time>`
- `get radioat [n|all]`
- `del radioat [n|all]`
- `set tempradioat <freq>,<bw>,<sf>,<cr>,<start_time>,<end_time>`
- `get tempradioat [n|all]`
- `del tempradioat [n|all]`

**Parameters:**
- `freq`: Frequency in MHz (150-2500)
- `bw`: Bandwidth in kHz (same allowed values as `set radio`)
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)
- `start_time`: UTC Unix epoch seconds, or `+N` for N whole minutes from now
- `end_time`: UTC Unix epoch seconds, or `+N` for N whole minutes from now
- `n`: Scheduled entry number from `get radioat` or `get tempradioat`

**Notes:**
- Both relative endpoints use the same command-time snapshot of the node clock.
  For example, `set tempradioat 910.5,500,8,5,+5,+15` starts in 5 minutes
  and reverts in 15 minutes (10 minutes active). `set radioat 910.5,500,8,5,+5`
  schedules a saved change in 5 minutes. Bare numbers remain absolute timestamps;
  absolute and relative endpoints can be mixed. Relative offsets must be positive.
  The existing scheduler and clock behavior are unchanged.
- `get radioat` and `get tempradioat` list all entries when `n` is omitted.
- `del radioat` and `del tempradioat` delete all entries when `n` is omitted.
- Each queue supports 3 entries. Scheduled entries are not saved across reboot.
- `radioat` saves the new radio preferences when it fires. `tempradioat` applies temporarily, then reverts to the saved radio preferences.
- `tempradioat` uses the repeater timing table above, with duration calculated
  from `end_time - start_time`. Its acceptance reply includes total minutes, `NdNhNm`,
  and the planned `rx.watchdog` setting. Queuing a future entry does not change
  the running watchdog or advert intervals; the overrides start only after
  the temporary radio parameters are successfully applied. If restoration
  needs retries after expiry, the timing overrides still end with the window.
- A successful scheduling reply notes any RXPS effective-level/preamble change
  required by the scheduled tuple. `RXPS continuous-fast` means the tuple is
  accepted but will use continuous receive because no safe duty-cycle level is
  available. Returning to slower settings recalculates from the saved RXPS
  minimum.

With the measured SX126x/LR1110 RXPS model and the default 1600 us TCXO startup delay
(2600 us total transition), a requested minimum level 1 gives:

| SF | BW (kHz) | Wire preamble | Effective timing preamble | Effective RXPS level | RX / sleep |
|---:|---------:|--------------:|--------------------------:|---------------------:|-----------:|
| 7 | 500 | 32 | 32 | 2 | 5594 / 3022 us |
| 6 | 250 | 32 | 32 | 2 | 5844 / 3022 us |
| 5 | 125 | 32 | 32 | 2 | 5844 / 3022 us |
| 5 | 250 | 64 | 64 | 1 | 3829 / 5100 us |
| 6 | 500 | 64 | 64 | 1 | 3829 / 5100 us |
| 5 | 500 | 128 | 128 | 1 | 2563 / 6647 us |
| 5 | 62.5 | 32 | 16 | 6 | 11922 / 2975 us |

These values include the actual transmitted preamble, explicit header, and a
one-symbol timer margin. SF5/SF6 need two more sync symbols than SF7, so their
receive windows differ even when symbol duration and sleep time match.

The wire preamble remains compatible with the existing Cascade convention:
32 symbols at SF5-SF8, extended to 64 for SF5/BW250 and SF6/BW500, or 128 for
SF5/BW500; SF9-SF12 use 16. Local RXPS settings and oscillator delays never
change the sender's preamble choice. The receiver may use a shorter catch
assumption, but its header timeout covers the full transmitted preamble.

Automatic adjustment stops at guarded level 8. If no guarded timing fits,
the radio receives continuously until the tuple changes. For example, a board
configured with the previous 5000 us TCXO delay uses continuous RX at SF7/BW500;
it cannot fit the new guard within that tuple's 32-symbol wire preamble.
Experimental levels 9 and 10 require an explicit selection.

Earlier firmware passed 16/16 packets in each direction between Heltec V4 and
WisMesh Tag at SF5/BW250/64 and SF5/BW500/128, CR5, 909.950 MHz. That result
established the existing wire-preamble convention; it does not validate the
new timing model. All senders to an RXPS receiver must use a compatible wire
preamble. LoRa OTA treats v1.17.1.5 as that capability boundary.

---

#### View or change this node's frequency

**Search terms:** radio frequency, LoRa frequency, MHz.

**Usage:**
- `get freq`
- `set freq <frequency>`

**Parameters:**
- `frequency`: Frequency in MHz

**Default:** `869.618`

**Note:** Companion local changes apply automatically when the radio is free.
Infrastructure saves immediately and switches after two seconds, once the radio
is free and its reply has drained. No reboot is needed. This ends pending or
active TempRadio sessions. Companion LoRa CLI applies after
its exact reply copies drain; an active TempRadio lease rejects the change.

On LR2021 radios, repeated unfinished preamble/header activity cannot block an
accepted primary or TempRadio change indefinitely. The driver allows one maximum
packet airtime plus a margin, capped at 30 seconds, before stopping an unfinished
reception at the next apply attempt. Completed packets, active transmission,
chip BUSY and exclusive radio tools retain priority. Normal radio2 scanning and
CAD do not use this exception.

If an LR2021 change remains pending, `get radio.apply` reports its admission or
failure stage and native error code. Its frequency field is the driver's last
accepted setting, not a measurement of the transmitted carrier.

**Connections:** USB, browser/TCP/Ethernet CLI, Companion binary command
`0x42` (USB/BLE/TCP), and authorized LoRa CLI. `set freq` saves the frequency;
bandwidth, spreading factor, and coding rate stay as configured. Infrastructure
roles also clamp transmit power to the board's limit for that frequency. Like
`set radio`, it requires permission to configure the destination node.

---

#### LR2021 frequency measurement and crystal calibration

Available on LR2021 builds, including Meshnology W12:

```text
get radio.freqerror
get radio.xtrim
set radio.xtrim <0-47>[,<0-47>]
set radio.xtrim default
```

`get radio.freqerror` reports the last successfully received LoRa packet's
frequency offset in Hz, its configured frequency and sample age. It reports
`unavailable` before a valid measurement. Failed reads and CRC errors keep the
previous sample and its age. This is the difference between two radios; it does
not establish which radio needs calibration.

`radio.xtrim` adjusts the LR2021 crystal's two load-capacitor codes. A single
number sets both codes; two numbers set XTA and XTB separately. Lower codes
generally raise frequency; each code is a capacitor step, not a fixed Hz offset.
Use a known reference to choose values. TCXO boards reject this setting.

The default leaves the chip's original trim unchanged; no shared calibration
value is imposed on boards. An explicit selection is saved first and waits two
seconds so its acknowledgement can leave with the previous calibration. It then
applies without a reboot when the radio is free, and is restored after resets
and temporary modem changes. A busy radio retries automatically for 30 seconds
after that handoff, including one final admission attempt. If protected radio
work still prevents the change, the
request fails and restores the previous saved selection; `get radio.xtrim`
shows it. A reported radio recovery failure still needs diagnosis.
`set radio.xtrim default` removes the override and resets/reinitializes the radio
to restore its original trim. Node identity and other settings remain saved.

These controls are available through local CLI and authorized LoRa CLI. They do
not change the configured channel frequency shown by `get radio` or `get freq`.
The [Semtech LR20xx driver documents the capacitor range and step size](https://github.com/Lora-net/usp/blob/main/smtc_rac_lib/radio_drivers/lr20xx_driver/inc/lr20xx_system.h).

---

#### View or change this node's rx boosted gain mode (SX12xx and LR1110, v1.14.1+)

**Search terms:** RX boost, receive gain, receiver sensitivity, boosted reception.

**Usage:**
- `get radio.rxgain`
- `set radio.rxgain <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** Target-specific. Most SX1262 and LR1110 targets default to `on`;
Station G2/G3 targets default to `off`.

**Notes:**
- The saved setting is applied when the radio is free and persists across reboots.
  A busy radio keeps the change pending and applies it automatically; do not
  repeat the command. Unsupported hardware and failed saves remain errors.
- Periodic AGC resets restore the saved runtime setting; they do not replace it
  with the target's compile-time default.
- Existing installations retain their previously saved value after an upgrade.

---

#### View or change RX duty-cycle power saving

**Search terms:** RX power saving, receiver sleep, radio power saving, RXPS.

**Usage:**
- `get radio.rxps`
- `get radio.rxps.config`
- `get radio.rxps.status`
- `get radio.rxps.rfrx_disabled`
- `get rxps.wd`
- `set radio.rxps.rfrx_disabled <state>`
- `set radio.rxps off`
- `set radio.rxps on`
- `set radio.rxps conservative`
- `set radio.rxps balanced`
- `set radio.rxps max`
- `set radio.rxps max preamble <16|32>`
- `set radio.rxps overdrive`
- `set radio.rxps overdrive preamble <16|32>`
- `set radio.rxps riskyWorkingMax`
- `set radio.rxps riskyWorkingMax preamble <16|32>`
- `set radio.rxps <1-10>`
- `set radio.rxps level <1-10>`
- `set radio.rxps level <1-10> preamble <16|32>`
- `set radio.rxps <rx_us> <sleep_us>`

**Parameters:**
- `rx_us`, `sleep_us`: Receive and sleep durations in microseconds (`1000`-`30000000`).
- `level`: Guarded levels `1`-`8`, followed by experimental levels `9` and `10`. Higher guarded levels allow more sleep.
- `preamble`: RXPS timing assumption in symbols; `16` or `32`. This does not
  change the radio's actual transmitted preamble.
- `state`: `on` or `off`.

**Notes:**
- LR1121 RXPS is supported on Waveshare ESP32-S3 LR1121 and Muzi Base Duo
  builds, using the same commands. Their existing 5000 us TCXO startup delay
  is preserved, giving a 6000 us sleep/wake transition. Fast SF/BW tuples may
  use continuous receive; `get radio.rxps.status` reports the actual mode.
- While radio2 profile scanning is enabled, RXPS is suspended so neither
  profile sleeps through its listening window. The requested RXPS setting
  is retained and restored after returning to single-profile receive.
- `get rxps.wd` reports the radio watchdogs' soft and hard recovery counts.
  See [receive calibration and recovery](test-results/radio_receive_calibration.md) for
  noise sampling, CAD handling, and the continuous-RX mode check.
- `get radio.rxps.config` adds the persisted level and preamble assumption to
  the on/off and timing values. Deployment tools use it to restore a
  level-based preference without converting it to fixed manual timings.
- `get radio.rxps.status` reports the saved `desired` state, actual `effective`
  state (`armed` or `continuous`), hardware support, last driver error (`err`),
  arm failures (`fail`), level, preamble and requested RX/sleep
  periods in microseconds. Levels `9` and `10` carry `(!)` to mark experimental
  settings. It includes `catch` for armed, level-based settings and
  `erx`/`eslp` when the driver clamps the periods and the reply has room.
  Three consecutive failed arm attempts trigger continuous receive as a
  fallback. Unsupported radios stay in continuous receive. Saved preferences
  remain intact.
- `radio.rxps.rfrx_disabled` is a runtime-only diagnostic setting and resets to `off` after reboot.
- Its default `off` state keeps the host-controlled SX1262 receive path enabled during RX duty-cycle mode. Setting it to `on` reproduces the old missing-RF_RX behavior and can significantly reduce receive sensitivity, making remote commands harder to receive.
- `radio.rxps.rfrx_disabled` is supported only on SX1262 targets with a host-controlled RX enable pin.
- `on` and `conservative` select level `3`, `balanced` selects level `6`, and
  `max` selects level `8`; each named profile assumes a 16-symbol preamble.
- The guarded catch ladders for levels 1-8 are `15,14,13,12,11,10,9,8` symbols
  for preamble 16 and `24,20,16,14,12,10,9,8` for preamble 32 or longer.
  Generated sleep leaves another 0.2 symbols of margin. The receive timer covers
  the actual wire preamble, sync, explicit header, and one extra symbol.
- `overdrive` selects level `9`; `riskyWorkingMax` selects level `10`. Both
  operate outside the guarded timer condition. Upstream bench measurements
  found packet loss at level 10; neither profile is a general guarantee of
  reliable delivery. They are never selected by automatic level adjustment.
- Named experimental profiles accept `preamble 32`; numeric levels keep the
  existing automatic-preamble selection. Manual timings bypass the level model.
- `MC_TCXO_DELAY_US` defaults to 1600 us. Initialization applies it and checks
  TCXO/calibration errors on the qualified SX126x/LR1110 paths. A board can
  override it for its oscillator. LR1121 retains RadioLib's 5000 us setting;
  it does not inherit the LR1110 hardware timing qualification.
- Fresh Cascade-profile builds start with RXPS on at level `8` and a 16-symbol preamble. Saved operator settings still take precedence after an upgrade.
- Level-based settings automatically recalculate their timings when the spreading factor or bandwidth changes. Custom `<rx_us> <sleep_us>` timings remain fixed.
- `get radio.rxps` keeps the legacy on/off, RX, and sleep reply. The new
  `get radio.rxps.config` reply adds the saved level and preamble assumption.
  Radio-change replies and the Full Companion terminal status report any
  effective level/preamble adjustment. Effective preamble 64 or 128 appears
  only when the active tuple's physical wire preamble has that length.
- The selected mode is persisted and applied when the radio is free. Busy
  requests apply automatically without another command and survive reboot.
  The runtime-only RXEN diagnostic also waits for a safe gap but is not saved.

---

#### View or change the LoRa FEM receive-path gain state on supported boards
**Usage:**
- `get radio.fem.rxgain`
- `set radio.fem.rxgain <state>`

**Parameters:**
- `state`: `on`|`off`

**Notes:**
- This controls the external LoRa FEM receive-path LNA where the board supports it.
- This is separate from `radio.rxgain`, which controls the radio chip receive gain mode.
- On Station G3, transmit temporarily bypasses the LNA. A gain change made
  during transmission is applied when receive mode resumes; `get` reports the
  configured receive preference. Startup and terminal sleep keep the LNA off.

---

#### View or change the LoRa FEM transmit-path gain state on supported boards
**Usage:**
- `get radio.fem.txgain`
- `set radio.fem.txgain <state>`

**Parameters:**
- `state`: `on`|`off`

**Notes:**
- This controls a software-selectable external LoRa FEM transmit gain where the board supports it.
- Heltec T096 routes TX through its KCT8103L PA with no populated TX bypass. `get radio.fem.txgain` reports `on (fixed TX PA)`; `set radio.fem.txgain` cannot toggle it. Use `set tx <dBm>` to adjust SX1262 drive. The PA's `CSD` control shuts down the FEM and is not a lower-gain TX mode. See the [T096 schematic](https://resource.heltec.cn/download/Mesh_Node_T096/Schematic/Mesh_Node_T096_V0.2.pdf) and [KCT8103L product description](https://kxcomtech.com/en/product/info/1774).
- On Station G3, remove the PA PL1 jumper to allow software control. `on` selects PA PL1 high/short and `off` selects PA PL1 low/open. The PA PL2 hardware jumper determines whether this switches between power levels 1/3 or 2/4.
- Select an operating level and SX1262 transmit power that comply with local RF limits and the Station G3 power-supply requirements.
- The setting is saved immediately, but on Station G3 the level is applied to the hardware at the start of the next transmit, so that the PA supply rail is never re-targeted while the PA is being driven. `get` reports the configured state, which may lead the hardware until the node next transmits.

---

#### Select the Station G3 physical radio slot

```text
get radio.slot
set radio.slot auto
set radio.slot 1
set radio.slot 2
reboot
```

Station G3 SX1262 builds support both physical RF sockets in the same image.
The default, `auto`, probes both sockets at boot without transmitting. If both
work, it prefers the last successfully used socket; on a fresh installation,
it prefers socket 1. A forced `1` or `2` never silently uses the other socket.

`get radio.slot` reports the saved selection, active socket, detected sockets,
and whether a reboot is needed. Changes are saved immediately but applied only
after reboot, never during a transmission. The selector does not change keys,
node settings, or the time-shared `radio2` profile. The legacy `_r2` build name
means physical socket 2, not a motherboard revision; both build names now
support this selector.

If no usable socket is found, startup waits with both LNAs disabled instead of
reboot-looping. A limited USB recovery console accepts `!g3 get radio.slot`,
`!g3 set radio.slot auto|1|2`, `!g3 reboot`, and `!g3 help`. The `!g3` prefix is
required in this recovery console; it is not the normal running CLI. KISS
firmware uses binary slot commands during normal operation, documented in the
[KISS protocol](kiss_modem_protocol.md).

If G2 firmware works on a G3 but the G3 image behaves differently, see the
[G2/G3 compatibility checks](test-results/station_g3_g2_compatibility.md) for
the verified pin assignments, startup fixes, and saved amplifier settings.

---

### System

#### View or change this node's name
**Usage:**
- `get name`
- `set name <name>`

**Parameters:**
- `name`: Node name

**Set by build flag:** `ADVERT_NAME`

**Default:** Varies by board

**Note:** Advertised names can use up to 23 bytes when location is included and 31 bytes otherwise. Emoji and Unicode characters may take more than one byte. Names that exceed the available advert space are truncated at a valid UTF-8 code point boundary.

---

#### Turn Companion sound and vibration on or off

`get buzzer` and `set buzzer on|off` control the Companion's buzzer. Devices
with a vibration motor also support `get vibration` and `set vibration on|off`.
Changes take effect immediately, are saved across reboots, and produce no
confirmation tone. Muting an output stops an alert already playing.

Fresh settings default to **sound off** and **vibration on**. Existing saved
choices are retained. The same preferences are used by the device buttons and
UI. Unsupported hardware reports an error.

These are the device mute controls. Programmable `notify.sound` and
`notify.vibration` rules also respect them; an enabled rule cannot bypass a
muted output. The notification output masks still select which alert outputs
a rule may use. Explicit button mute/unmute confirmations retain their existing
behavior.

Adapted from [upstream PR #3603](https://github.com/meshcore-dev/MeshCore/pull/3603).

#### Turn indicator LEDs on or off

```text
get leds
set leds off
set leds on
```

Available in Companion, repeater, room-server, and sensor firmware. The setting
is saved across reboots and defaults to `on`. `off` suppresses software-controlled
transmit, Bluetooth, status, and notification LEDs. `on` restores their normal
behavior; it does not leave every LED lit. A build with `DISABLE_BLE_LED` keeps
the Bluetooth indicator disabled.

Supported outputs include ordinary and inverted GPIO LEDs, addressable RGB LEDs,
and board-controlled expander LEDs on nRF52, ESP32, RP2040, and STM32 boards.
Screen backlights and manually controlled torches keep their own controls.
Power and charging LEDs wired directly to a charger cannot be switched off by
firmware. Bootloader and early startup indicators may still appear before the
saved setting is loaded.

This extends the SenseCAP Solar LED switch from
[upstream PR #3605](https://github.com/meshcore-dev/MeshCore/pull/3605).
With LEDs enabled, RAK-family Companions blink their Bluetooth LED while
advertising and leave it off once connected, following
[upstream PR #2694](https://github.com/meshcore-dev/MeshCore/pull/2694).

#### Turn WiFi, ESP-NOW, or all 2.4 GHz services on or off

```text
get wifi
set wifi off
set wifi on
get espnow
set espnow off
set espnow on
get 2.4ghz
set 2.4ghz off
set 2.4ghz on
set 2.4ghz on all
```

| Control | Effect |
| --- | --- |
| `wifi` | Infrastructure WiFi, setup AP, TCP/WebUI, and WiFi MQTT services. ESP-NOW remains available. |
| `espnow` | The ESP-NOW bridge or primary ESP-NOW mesh transport in this build. Infrastructure WiFi remains available. |
| `2.4ghz off` | Stop WiFi, Bluetooth, and ESP-NOW; remember which were enabled. LoRa and wired transports remain available. |
| `2.4ghz on` | Restore the states saved by the first `off`. Repeated `off` preserves that snapshot. With no previous `off`, keep the current states. |
| `2.4ghz on all` | Enable every service available in this build and boot. `2.4ghz on force` is an alias. |

These commands work in Companion, repeater, and room-server firmware where the
services are available. Changes last until reboot; saved WiFi credentials,
Bluetooth preferences, and bridge settings stay intact. `get 2.4ghz` lists each
service as `on`, `off`, or `unavailable`. An nRF52 Companion controls its Bluetooth
and reports WiFi/ESP-NOW as unavailable. Exclusive WiFi/Bluetooth builds retain
their boot transport restriction; `on all` cannot reclaim a released Bluetooth stack.

An `off` command requires a management connection that will survive it. Bluetooth
can provide the fallback for `wifi off`; USB, Ethernet, or a CLI request over LoRa
can provide it for `2.4ghz off`. WiFi association, a listening socket, and USB power
alone do not count. On USB hardware without an observable open-client session,
send the command through USB itself. To bypass the guard:

```text
set wifi off force
set espnow off force
set 2.4ghz off force
```

Shutdown starts after the reply has had time to drain (normally 250 ms, up to two
seconds for pending transport I/O). Network services stop before the shared WiFi
driver. A normal shutdown is cancelled if its fallback connection disappears
before it starts. The `get` commands report pending changes and startup/teardown
failures. After `2.4ghz off`, use `2.4ghz on` or `on all` before enabling an
individual service. The `all`/`force` suffix on **on** applies only to `2.4ghz`.

WiFi and ESP-NOW share one hardware channel. A running ESP-NOW bridge pins setup
and station connections to `bridge.channel`; starting the bridge alongside an
existing WiFi connection requires that same channel. A mismatch reports a failed
start and preserves the WiFi connection.

#### Turn Bluetooth on or off (Companion)

```text
get bluetooth
set bluetooth off
set bluetooth off force
set bluetooth on
```

`get ble` and `set ble ...` are aliases. This controls the running Bluetooth
service on ESP32 and nRF52 Companions: `off` stops advertising and disconnects
Bluetooth clients; `on` enables it again. Both choices are saved and survive
reboots. USB remains available when Bluetooth is off. Older saved preferences
without this setting retain the previous default of Bluetooth on.

Successful local commands report `(saved)`. If saving fails, the command reports
an error and leaves Bluetooth unchanged. When requested over Bluetooth, `off`
first reports `(save pending)` so its reply can drain; the firmware rechecks the
alternate connection and saves the preference before disconnecting. If that
connection disappears or the save fails, shutdown is cancelled. `get bluetooth`
reports the current runtime state. Broad `2.4ghz` controls still apply to the
current boot only and do not change this saved Bluetooth preference.

Plain `off` requires another active management connection. A connected USB
terminal or binary client, TCP client, or browser terminal can provide that
connection. WiFi association, a listening server, and USB power alone do not.
On USB hardware without an observable open-host session (HWCDC or a UART
bridge), issue the command through that USB client so the request itself proves
it is active.

Append `force` to `off` to permit disconnecting the only active connection.
Use USB or another management transport and `set bluetooth on` to regain
access afterward. Reboot alone will not re-enable saved-off Bluetooth.
`force` is accepted only as the final argument to `off`.

Commands sent through Bluetooth allow its reply to drain before shutdown
(normally 250 ms, at most two seconds). `get bluetooth` shows `off pending`
during that wait. A normal shutdown is cancelled if the other management
connection disappears before it applies; `force` bypasses that check too.

Bluetooth must be available in the current build and boot transport mode.
An exclusive-WiFi boot still requires `set companion.transport ble` and a
reboot before Bluetooth can be enabled.

#### View or change the independent Bluetooth name (Companion)

**Usage:**

- `get bluetooth.name`
- `set bluetooth.name <name|default>`

`get ble.name` and `set ble.name ...` are accepted aliases. This command is
specific to Companion firmware. The default is `MeshCore-<advert name>`;
`default` (or `clear`) removes a custom override. Names are limited to 31 valid
UTF-8 bytes without control characters and take effect after reboot.

A binary Companion client should carry the same text in command `0x42`
(`CMD_RUN_CLI_COMMAND`), which works over USB, BLE, or TCP without entering USB
terminal mode. See [Companion radio binary protocol](companion_protocol.md) for
the frame and reply format.

---

#### Set the Bluetooth pairing PIN (Companion)

**Usage:**

- `set pin <0-999999>`

Choose a six-digit code for a fixed Bluetooth pairing PIN. For example:

```text
set pin 654321
reboot
```

The command saves the PIN across reboots and replies `> pin is now 654321`.
Reboot to activate the new code; the current Bluetooth session keeps its
existing PIN until then. A failed save reports an error and retains the
previous saved setting.

`set pin 0` clears the saved override, rather than setting the pairing code to
`000000` or disabling Bluetooth. After reboot, the build's default policy
applies: normally `123456` without a display, or a generated PIN on eligible
display-equipped builds.

This command is available in the Companion text terminal, binary command
`0x42` (`CMD_RUN_CLI_COMMAND`), and authorized LoRa CLI; a Bluetooth-capable build
is needed to use the PIN. Existing bonded clients may reconnect without being
asked for a PIN.
Forget the device on the phone and pair again if you need to enter the new code.

---

#### View or change the Bluetooth address (BLE Companion)

**Usage:**

- `get bluetooth.mac`
- `set bluetooth.mac <address|random|random-every-boot|random-after-connect|default>`

`get ble.mac` and `set ble.mac ...` are accepted aliases. The setting is
available on every Companion build that includes Bluetooth.

| Value | Result after reboot |
|---|---|
| `C2:11:22:33:44:55` | Save and use that custom address. |
| `random` | Generate one random address now, save it, and reuse it on later boots. |
| `random-every-boot` | Generate a new address once at each boot. `random everyboot` is also accepted. |
| `random-after-connect` | Keep one random address through unused boots. After an authenticated connection, arm a new address for the next boot. |
| `default` or `clear` | Remove the override and use the chipset's factory Bluetooth address. |

A custom value must be a valid BLE random-static address in
`XX:XX:XX:XX:XX:XX` form. Its first byte must be from `C0` through `FF`, and
you are responsible for keeping addresses unique. Hyphens are also accepted
on input; status output uses uppercase colons.

Every change requires a reboot and invalidates the phone's previous view of
the device. Forget the old device entry and pair again. `random-every-boot`
also clears the Companion's saved peer bonds during each startup, so pairing
and PIN entry are expected again after every reboot.

`random-after-connect` keeps the same address across any number of reboots when
nobody successfully authenticated. As soon as an authenticated BLE connection
succeeds, the next boot rotates the address and clears stale bonds. The marker
is saved while connected, so a normal reboot, power switch, or battery pull
after that connection all produce the same next-boot rotation.

The address setting does not enable or disable stealth. Use the separate
`bluetooth.stealth` flag below. `default` changes only the address policy.

#### View or change Bluetooth stealth (BLE Companion)

```text
get bluetooth.stealth
set bluetooth.stealth on
set bluetooth.stealth off
```

`get ble.stealth` and `set ble.stealth on|off` are short aliases. This flag
defaults to `off` and is independent of the Bluetooth address policy. Enabling
or disabling it keeps the configured custom/random/factory address policy
unchanged and requires a reboot. Repeating `on` keeps an existing stealth
pairing; repeating `off` does not alter address settings.

For a custom address with stealth:

```text
set bluetooth.mac C2:11:22:33:44:55
set bluetooth.stealth on
reboot
```

Use `set bluetooth.mac random` instead of the first line for one saved random
address. Both combinations keep their address and bond across later boots.
Rotating policies still rotate: `random-every-boot` reopens pairing at every
boot, while `random-after-connect` reopens it on the first boot after an
authenticated connection. Stealth stays enabled; each new address begins
with a fresh pairing window so it cannot get hidden behind an unusable bond.

With stealth on, the node is normally discoverable until the first authenticated pairing.
After that, nRF52 uses directed advertising to the saved peer. ESP32 uses a
minimal unnamed advertisement and a controller allowlist because its common
Arduino BLE API does not expose the directed-advertising peer field. Both
reject connection and scan requests from other peers. BLE cannot be completely
radio silent and still accept a connection, so a passive radio sniffer may
still observe packets. To deliberately clear the saved stealth peer, send
`set bluetooth.stealth off`, then `set bluetooth.stealth on`, then reboot over
USB or an existing authenticated connection. To leave stealth, set it `off`
and reboot. If the saved node-side bond is lost, recovery reopens pairing.
The former `set bluetooth.mac stealth` command is no longer accepted.

This changes only the running MeshCore Companion application. On nRF52, the
OTAFIX bootloader still uses its own address, which is visible only while the
board is in BLE DFU/recovery mode. USB and LoRa updates are unaffected.
Bluetooth update tools must follow that separate bootloader address after the
handoff; they must not assume that a custom application address remains the
DFU address. See the [hardware observations](test-results/hardware_validation_bluetooth_stealth_2026-09-07.md)
for the tested XIAO handoff and host-cache caveats.

---

#### View or change this node's latitude
**Usage:**
- `get lat`
- `set lat <degrees>`

**Set by build flag:** `ADVERT_LAT`

**Default:** `0`

**Parameters:**
- `degrees`: Latitude in degrees

---

#### View or change this node's longitude
**Usage:**
- `get lon`
- `set lon <degrees>`

**Set by build flag:** `ADVERT_LON`

**Default:** `0`

**Parameters:**
- `degrees`: Longitude in degrees

---

#### View or change this node's identity (Private Key)
**Usage:**
- `get prv.key`
- `set prv.key <private_key>`

**Parameters:**
- `private_key`: MeshCore expanded private key in hex format (128 hex characters)

**Local connection only:** `get prv.key`. Companion also requires
`ENABLE_PRIVATE_KEY_EXPORT=1`; use binary command `0x42` or the text terminal.
Infrastructure `set prv.key` retains its administrator access rules.

Infrastructure repeaters with `ENABLE_PRIVATE_KEY_EXPORT=1` also support
`backup prv.key <16-hex-nonce>` for authenticated remote administrators. It
returns `KEYBACKUP <nonce> <128-hex-private-key>` only as an end-to-end encrypted
direct CLI reply. This separate command does not make `get prv.key` remotely
available. Treat the reply as a secret: clients must not put it in chat history
or debug logs and should encrypt any saved backup. An administrator password
alone is not a safe backup-file encryption key. Firmware does not retain this
reply in its remote CLI retry cache.
Companion firmware must advertise `get key.backup.transport` as
`ephemeral-routed-v1` before the phone requests it; that build delivers the
reply only to the live attached client that requested it, without offline
queuing or terminal echo.
Older Companion firmware is not a safe transport for this backup flow.

**Note:** Requires reboot to take effect after setting

---

<a id="change-this-nodes-admin-password"></a>
#### View or change this node's admin password
**Usage:**
- `get password`
- `password <new_password>`

**Parameters:**
- `new_password`: New admin password (on ESP32, 1–15 UTF-8 bytes without CR or LF)

**Set by build flag:** `ADMIN_PASSWORD`

**Default:** `password`

Repeaters and room servers send `CHANGE YOUR PASSWORD` as a separate CLI reply
after a successful admin radio login while the saved password still matches
the build's `ADMIN_PASSWORD`. Saving a different password stops the warning.

`get password` returns the current admin password only on a local connection:
USB, Ethernet, or the authenticated LAN browser CLI. It is rejected over LoRa.
ESP32 WiFi Companion instead reports management-password status without revealing
the secret. Its password is stored in NVS, survives a filesystem erase, and
defaults to `password`. Other Companion builds retain their existing no-admin
response. This command does not report the Bluetooth PIN.

On ESP32, empty and overlong new passwords are rejected rather than truncated.
Leading spaces are part of the password. For ESP32 setup WiFi protection, choose 8–15
printable ASCII characters; other accepted passwords use an open hotspot with
an HTTP admin login. WebConfig and runtime browser OTA share this admin
credential (OTA's HTTP Basic username is `admin`).

A confirmed password change invalidates existing browser sessions. The page
that submitted it keeps a save receipt, tries the new password once, and reads
the same result again after login; it does not resend commands. If another
setting fails, the result remains visible and reboot is withheld. An
unconfirmed password save keeps the previous runtime password active, but the
saved state after reboot may differ; the error reports this uncertainty.

Trusted TCP consoles and delegated remote CLI remain administrative paths and
can change this password. The HTTP credential does not protect those ports or
replace radio ACL permissions.

**Note:** The password setter replies with confirmation.

**Note:** Any node using this password will be added to the admin ACL list.

---

#### Enable or disable repeater guest logins

**Availability:** Repeater firmware. Local administrative consoles and authenticated
remote admins can use these commands.

```text
get guest
set guest on
set guest off
```

**Default:** `off`, including when upgrading from firmware without this setting.
The choice is saved and takes effect without a reboot. `set guest off` also
removes existing guest sessions immediately. Turning it back on requires guests
to log in again; admin and assigned ACL access are unaffected.

When enabled, guests must supply the configured `guest.password`. Its default is
blank, so enabling guests without setting a password permits passwordless guest
access. A guest can read repeater status and basic telemetry but cannot run
administrative commands. This setting does not disable ordinary packet forwarding
or public discovery queries.

For example:

```text
set guest.password a-private-code
set guest on
get guests
set guest off
```

Room-server login settings are unchanged.

---

#### View or change this node's guest password
**Usage:**
- `get guest.password`
- `set guest.password <password>`

**Parameters:**
- `password`: Guest password

**Set by build flag:** `ROOM_PASSWORD` (Room Server only)

**Default:** `<blank>`

Repeater guest logins must also be enabled with `set guest on`. Changing this
password takes effect immediately; use `set guest off` to remove existing guest
sessions. Setting the password to `off` makes it the literal password `off`.

---

#### View or change this node's owner info
**Usage:**
- `get owner.info`
- `set owner.info <text>`

**Parameters:**
- `text`: Owner information text

**Default:** `<blank>`

**Note:** `|` characters are translated to newlines

**Note:** Requires firmware 1.12+

---

#### Calibrate the battery reading against a meter

**Availability:** Repeater, Room Server, Sensor, Companion and Terminal Chat
firmware on boards with a supported battery ADC. Use an administrative CLI
connection; infrastructure roles also accept authenticated remote commands.

```text
battery calibrate 4.18
get adc.multiplier
battery calibrate reset
```

Measure the battery voltage with a voltmeter and enter that reading in volts.
The firmware calculates `new multiplier = current multiplier * meter voltage /
reported voltage`, applies it and saves it for restart. A reference is required;
the command does not assume a chemistry or a 4.2 V full battery. Enter the voltage
of the battery input the board actually measures, including pack voltage where
that input supports a pack. For example, `battery calibrate 13.2` supplies a
13.200 V reference; it does not change the board's measurement range.

Calibration supports the ESP32, nRF52, RP2040 and STM32 boards with implemented
battery-voltage ADC conversions, including the Wio Tracker L2's external ADS1115.
An unsupported board or unavailable/saturated reading returns an error. Digital
PMU/fuel-gauge and battery-module readings retain their hardware scaling; boards
without a battery-voltage input cannot be calibrated through this command.

The meter reference must be within 50% of the reported voltage. Newly supported
ADCs accept a relative multiplier from `0.500` to `1.500`, where `1.000` retains
their original conversion. Existing board-specific absolute multipliers and
saved values keep their established meaning. The five legacy absolute drivers
are Heltec V4, V4 R8, RC32, Meshnology W12 and ProMicro.

`battery calibrate reset` restores and saves the board's default conversion.
`set adc.multiplier 0` also resets it. A successful numeric calibration reply shows the effective
multiplier, including any rounding by the board. A failed save reports an error
and attempts to restore the previous runtime setting.

Calibration corrects voltage reporting and the battery percentages/protection
that use that reading. On nRF52 boards with voltage policy it also applies to
the boot reading and running cutoff. It does not adjust charger hardware or
create a current measurement. This is a single-point scale correction, not an
ADC offset or nonlinear calibration.

#### Fine-tune the battery reading
**Usage:**
- `get adc.multiplier`
- `set adc.multiplier <value>`

**Parameters:**
- `value`: ADC multiplier (0.0-10.0)

**Default:** `0.0` (value defined by board)

**Note:** On newly supported ADC boards this is a relative `0.500`-`1.500`
correction, with `0` restoring the default. Existing absolute-multiplier boards
retain their coefficient units and accept `0` or a positive value up to `10`.
On nRF52 boards with power management and a boot-voltage setting,
this is a saved *relative* calibration: `1.000` keeps the board conversion,
`1.050` raises all reported battery millivolts by 5%, and `0` resets to
`1.000`. The allowed range is `0.500`-`1.500`. This affects boot protection,
running cutoff, telemetry, battery alerts, and percentage. Calibrate against a
voltmeter before enabling a low cutoff. Boards without a supported ADC return
unsupported.

---

#### Send a repeater flood text
**Usage:**
- `send text.flood <message>`

**Parameters:**
- `message`: Text to send to the shared `#repeaters` flood channel, prefixed with this node's name. Any `:` in the node name is sent as `;` so the prefix delimiter stays unambiguous.

**Example:**
```
send text.flood checking ridge link
```

---

#### View or change battery alert state
**Usage:**
- `get battery.alert`
- `get battery.alert.region`
- `set battery.alert on [region]`
- `set battery.alert off`

**Parameters:**
- `region`: Optional named region scope. When omitted, the repeater selects the single deepest (most narrow) named region in the configured hierarchy. If multiple regions tie for deepest, specify one explicitly.

**Defaults:**
- `battery.alert`: `off`
- `battery.alert.region`: `<unset>`

**Notes:**
- Enabling fails until at least one usable named region is defined. Alerts are never sent as unscoped floods. If the selected region is later removed, alerts stop until battery alerts are enabled again with a valid region.
- Region hierarchy edits are not persistent until `region save` is run. After `region def west pnw wa w-wa sea`, run `region save` before enabling the alert if the hierarchy must survive a reboot.
- A region must have a usable transport key. Public named regions derive one automatically; a private region without an available key is rejected.
- The first alert is suppressed until the repeater has been up for at least 30 minutes. After that, the repeater checks every 30 minutes and sends low-battery warnings to the `#repeaters` channel in the selected region.
- Once an alert finishes transmitting, another battery alert is suppressed for at least 12 hours. A queue rejection, stale-queue drop, or radio send failure does not start the cooldown. Battery recovery or toggling alerts off and back on does not bypass a completed alert's cooldown during the same boot.
- With `region def west pnw wa w-wa sea`, `set battery.alert on` selects `sea`; `set battery.alert on w-wa` overrides that default.
- `get battery.alert.region` returns the selected scope, for example `> sea`.
- The battery check never requests a wake earlier than its 30-minute deadline. If the normal loop is already awake when that deadline has elapsed, the check is effectively free of an additional wake. Time in light/event sleep counts toward the startup delay, and a pending alert keeps the repeater awake until the packet is handled.

---

#### View or change battery alert thresholds
**Usage:**
- `get battery.alert.low`
- `set battery.alert.low <1-100>`
- `get battery.alert.critical`
- `set battery.alert.critical <0-99>`

**Defaults:**
- `battery.alert.low`: `25`
- `battery.alert.critical`: `15`

**Note:** The low threshold must be greater than the critical threshold. Alerts at or below the critical threshold use `CRITICAL BATTERY` in the message; both severities use the same 12-hour resend cooldown.

---

#### Pause forwarding when the battery is low

**Search terms:** low battery repeating, battery flood cutoff, save battery,
automatic repeat off.

Repeater, Room Server, and Sensor firmware can automatically pause forwarding
below a configured battery percentage. Both controls default to `off`, including
when upgrading. They are independent of battery alerts.

`wio-e5-mini_repeater` omits these controls and the adjustable battery voltage
range below to fit its application partition. Battery alerts and basic battery
readings remain available.

**Usage:**

- `get repeat.battery`
- `set repeat.battery <off|on|1-100>`
- `get flood.battery`
- `set flood.battery <off|on|1-100>`
- `get battery.forward`

`set repeat.battery on` enables the saved repeat threshold, initially **15%**.
`set flood.battery on` enables the saved flood threshold, initially **25%**.
A number both enables the control and sets its threshold. `off` disables it
while retaining that threshold for the next `on`. Changes are saved and take
effect without a reboot.

An older forwarding record with exactly the former 10% repeat / 20% flood
defaults adopts 15% / 25% on upgrade. Its enabled controls and voltage endpoints
stay unchanged. Other saved thresholds are retained; newly selected custom
10% / 20% settings also remain available.

Below the repeat threshold, the node stops LoRa retransmissions of received
traffic. Below the flood threshold, it stops LoRa retransmissions of received
flood traffic but may still
relay routed traffic if repeating is otherwise allowed. Neither control stops
the node's own transmissions or replies to administration requests. The saved
manual repeat flag, flood limits, and forwarding rules remain unchanged; a
battery recovery does not override a manual restriction.

Received packets entering from a bridge are subject to the same LoRa transmit
limits. Exporting radio reception to WiFi, ESP-NOW, or RS232 is unchanged.

Forwarding resumes automatically after the measured percentage reaches two
percentage points above the applicable threshold, capped at 100%. With the
initial thresholds, flood forwarding pauses below 25% and resumes at 27%; all
relay forwarding pauses below 15% and resumes at 17%. This recovery margin
avoids repeatedly switching forwarding on and off near a threshold.

Battery voltage is sampled every 30 seconds while either control is enabled,
and immediately when its configuration changes. `get battery.forward` reports
the voltage, estimated percentage, whether the reading is valid, and which
battery controls are currently suppressing forwarding. A missing or invalid
reading does not suppress forwarding: accepted readings are 2000-20000 mV.
Voltage alone does not establish that a battery is physically connected.

To enable both default thresholds:

```text
set repeat.battery on
set flood.battery on
get battery.forward
```

To choose different thresholds and later disable the feature:

```text
set repeat.battery 12
set flood.battery 22
set repeat.battery off
set flood.battery off
```

#### Emergency battery schedule

Supported ESP32 and nRF52 Repeater, Room Server and Sensor images enter the
final battery-saving stage at **5% or less**, independently of the optional
forwarding gates. Automatic mode returns to normal at **7% or more**. An
invalid battery reading cannot activate it, or falsely clear an existing low
battery state. Percentage uses the same configured voltage endpoints as
`battery.forward`; it does not prove that a physical battery is connected.

In emergency mode, normal work runs during the first **15 minutes of every
UTC hour** (`:00:00` through `:14:59`). For the remaining 45 minutes the LoRa
radio sleeps and normal mesh, sensor and screen work is suspended. The CPU
uses timed sleep with brief watchdog, clock, voltage-protection and command
maintenance wakes. Active firmware updates and unfinished critical operations
defer sleep. Existing hard low-voltage protection remains in force.
On ESP32, an active native USB connection uses short CPU idle waits to keep
the console usable while the radio sleeps. Timed light sleep resumes after
USB detaches and its disconnect grace expires.
On nRF52, an enumerated USB host limits each CPU idle slice to 100 ms so
reconnects and replies can progress while LoRa remains asleep. Detached
maintenance intervals remain up to ten seconds; command completion can need
several passes through the main loop.

```text
get power.emergency
set power.emergency on
set power.awake 30
set power.awake 0
set power.emergency auto
```

`on` saves manual emergency mode, including across reboots. `auto` restores
battery-controlled entry and is the default. The initial transition allows
five seconds for its reply. `power.awake` keeps the node up for the next given
number of minutes (0 cancels; maximum 10080), even outside the hourly window.
That override is held in RAM and ends on reboot; it never disables hard
low-voltage shutdown. Use it before maintenance that needs continuous uptime.
LoRa commands must arrive during an awake window. Local serial commands remain
available through maintenance wakes while the radio sleeps.

`get power.emergency` reports the mode, battery estimate, desired `window`,
actual radio ownership, scheduled wake/check delay (`wake`), and remaining
hold. Its window includes any enabled operating-hours or daylight filters.
With an implausible clock timestamp it reports `clock=uptime` and follows
the same 15/45-minute cycle from boot. Keep the clock set to UTC: a plausible
timestamp alone does not prove it is synchronized. Queued
future traffic does not keep a critically low node awake indefinitely.

This schedule uses resumable timed sleep, preserving identity and settings.
WiFi/ESP-NOW services are temporarily stopped and their previous live subset
is restored when the node wakes. Companion builds and flash-constrained
profiles that omit battery-forwarding controls do not add this feature.

#### Operating hours and daylight

The same supported infrastructure images can sleep outside daylight or a
daily time window, even when emergency mode is inactive. Both filters default
to **off** and are saved across reboots. If both are enabled, the node must be
inside the time window **and** daylight. Active emergency mode further limits
operation to the first 15 minutes of each UTC hour.

For daylight-only operation, starting from the defaults:

```text
set power.daylight on
get power.schedule
```

Daylight is calculated from the UTC clock and the node's saved `lat`/`lon`,
falling back to a fresh cached GPS fix when the saved position is unset. For
a fixed installation, saved coordinates keep the schedule independent of GPS
freshness. This does not start GPS. Sunrise and sunset are approximate; cloud
cover, panel output and charging status do not determine this schedule. Set
the node's position and synchronize its UTC clock before relying on daylight
control.

For a daily operating window:

```text
set power.hours 08:00-18:00
```

The start is included and the end excluded. Overnight windows such as
`22:00-06:00` work, and an end of `24:00` is accepted. Equal start/end times
are rejected; use `set power.hours off` for unrestricted hours.
Times use UTC by default. `set power.hours.offset -420`, for example, makes
the window use UTC-7. The offset is minutes east of UTC, from -720 to +840;
it is fixed and does not adjust for daylight saving time. It affects only the
hour window, not the UTC clock, solar calculation or emergency hourly cycle.

To combine daylight with the emergency hourly duty cycle:

```text
set power.daylight on
set power.emergency on
```

`get power.schedule` shows the combined policy. Its `check` value is the next
candidate schedule decision; another filter may keep the node asleep afterward.
The individual settings also have `get power.daylight`, `get power.hours` and
`get power.hours.offset` readbacks. `set power.schedule off` disables both
operating filters; it does not disable manual emergency mode or automatic
low-battery protection.

An unknown clock leaves the time-based filters open. A missing valid position
leaves the daylight filter open; an independently configured hours filter
still applies. Status reports the unavailable input rather than guessing.
The existing emergency fallback to a boot-relative cycle remains in force.
`set power.awake <minutes>` overrides all these schedules temporarily;
hard low-voltage protection and existing active-operation sleep deferrals
remain in force. These commands use the same USB/admin CLI and authorized
management-command paths as `power.emergency`.

#### Set the battery percentage voltage range

The forwarding thresholds use a voltage-derived percentage. On infrastructure
builds without the nRF52 voltage policy, the saved range is adjustable with:

```text
get battery.empty
get battery.full
set battery.empty 3000
set battery.full 4200
```

Values are millivolts, from 2000 to 20000, with full at least 100 mV above empty.
The default range comes from the board's compiled battery endpoints, normally
3000 mV for 0% and 4200 mV for 100%; boards with a two-cell pack retain their
compiled pack range. The percentage is interpolated between those endpoints
and limited to 0-100%. These settings describe the battery
range; they do not calibrate the voltage measurement or alter a charger.

Supported nRF52 power-management builds use their existing saved
`battery.profile`, `battery.empty`, and `battery.full` values instead. Their
endpoint range remains 2000-4200 mV, with at least 100 mV between endpoints.
See [nRF52 battery protection](#configure-nrf52-battery-protection) for profiles
and their separate boot-lock and cutoff effects. `set adc.multiplier` adjusts
voltage calibration on boards that support it; see
[battery reading calibration](#fine-tune-the-battery-reading).

Percentage remains an estimate: a voltage reading under transmit load may
differ from an idle reading. Check the reported voltage against a voltmeter
before relying on automatic forwarding limits.

---

#### Enable or disable the RX inactivity watchdog (Repeater Only)
**Usage:**
- `get rx.watchdog`
- `set rx.watchdog on`
- `set rx.watchdog off`

**Default:** `on`, with a 24-hour no-RX timeout in normal mode.

**Notes:**
- In normal mode, the repeater reboots after 24 hours without a received packet passing MeshCore parsing. Each such reception restarts this timeout; checks use a sliding deadline rather than waiting for another 24-hour checkpoint.
- While `tempradio` or `tempradioat` is running, windows of at least 12 hours use a 12-hour no-RX timeout. Shorter windows disable this reboot watchdog for the duration of the session. This temporary policy also overrides a saved normal-mode `off` setting.
- Startup, a successful temporary-radio activation/replacement, expiry, cancellation, or manually changing this setting starts a fresh observation window. Expiry is processed before the watchdog, so a temporary window ending exactly at its watchdog deadline restores normal timing first.
- `get rx.watchdog` reports both the effective on/off state and timeout or short-session disabled reason. `set rx.watchdog on|off` saves the normal-mode preference; temporary-session rules remain active until that session ends. Existing saved preferences are retained when upgrading; the new default applies to fresh settings or older preference files without this field.
- The check uses the last successful MeshCore parse time, recorded before receive delays and routing filters. It does not require the packet to be addressed to this repeater or its encrypted payload to be decrypted; duplicate receptions also count. This checks MeshCore packet structure, not sender authentication.
- It does not poll, sample, or wake the radio or CPU. A due check waits for the next normal loop/wake. With RX power saving enabled, qualifying packets received during normal listening windows count as activity; the watchdog does not alter the RX/sleep timing.
- Transmitting, radio interrupts, and raw LoRa packets rejected by the MeshCore parser do not reset the timer. Packets dropped before parsing because the packet pool is full do not count either. This whole-board reboot setting is separate from the automatic radio-only recovery watchdog, which still measures successful raw radio reads.

---

#### Enable or disable the nRF52 system watchdog
**Usage:**
- `get system.watchdog`
- `set system.watchdog on`
- `set system.watchdog off`

**Default:** `on`

**Notes:**
- This nRF52-only hardware watchdog resets the device if the application loop stops for 60 seconds, including an indefinite SoftDevice flash-write wait.
- Enabling takes effect without a reboot.
- The nRF52 hardware cannot stop a watchdog after it has started. Disabling is persisted immediately, then the current firmware stops feeding it so the board performs one watchdog restart within 60 seconds. It remains off after that restart.
- This setting is nRF52-only. The SoftDevice flash deadlock does not apply to ESP32, whose existing watchdog behavior is unchanged.

Companion supports the getter and reports the actual hardware running state.
Its application always feeds the watchdog and has no saved on/off policy;
`set system.watchdog ...` therefore reports that control is unavailable. The
persistent on/off behavior above applies to infrastructure firmware.

---

#### Estimate and correct infrastructure-node time after startup

**Usage:**
- `get clock.sync`
- `get clock.sync.status`
- `get clock.sync.status.table`
- `get clock.sync.status.<1-16>`
- `get clock.sync.mesh`
- `set clock.sync.mesh <on|off>`
- `get clock.sync.mesh.edge`
- `set clock.sync.mesh.edge <on|off>`
- `clock.sync.mesh now`
- `get clock.sync.internet`
- `set clock.sync.internet <on|off>`
- `get clock.sync.drift`
- `set clock.sync.drift <30-86400>`
- `get clock.sync.samples`
- `set clock.sync.samples <3-16>`

**Defaults:**
- `clock.sync.mesh`: `on` for all repeater, sensor, and room-server builds
- `clock.sync.mesh.edge`: `on`
- `clock.sync.internet`: `off`
- `clock.sync.drift`: `600` seconds (10 minutes)
- `clock.sync.samples`: `9`

When either source is enabled, the node makes its first clock-bootstrap
attempt after 30 minutes of uptime, or immediately when the configured number
of fresh evidence sources has been collected, whichever comes first. A
successful estimate changes the RTC only when the absolute difference is
**greater than** `clock.sync.drift`; correction can move the clock forward or
backward. A valid estimate within the threshold counts as a successful sync
without changing the clock. Seven days after each successful estimate, the
node evaluates time again; the seven-day deadline therefore starts from the
last successful estimate rather than from boot. This is a lazy uptime deadline:
the check runs on the first normal loop/wake after it becomes due and does not
wake the device by itself. If no source or consensus is available, the node
retries every 30 minutes, and newly collected evidence triggers another
immediate evaluation once the configured source count is present. Every reboot
starts with the initial bootstrap attempt. An existing saved setting always
overrides the platform default.

`clock.sync.mesh now` bypasses the startup/seven-day deadline and queues a
LoRa-only consensus evaluation on the next normal loop, even when the internet
source is also enabled. It uses any currently fresh samples without clearing the
16-slot table. If there is not yet enough evidence, mesh collection remains open
and the next attempt follows the normal 30-minute retry. The command requires
`clock.sync.mesh` to be on and does not bypass CLI, GPS, or NTP suppression; it
also retains the normal quorum, timestamp-validity, and drift checks.
The separate `clock` command only displays the current RTC and does not request
a synchronization attempt.

`clock.sync.mesh` collects signature-verified advert timestamps and MAC-valid,
decrypted Public-channel plain-text timestamps. In normal path mode, collection
occurs only after the packet passes every forwarding filter. Sources are
deduplicated by advert public key or case-insensitive Public-channel display
name. Every fresh sample must also have a different full received path; all
direct, zero-hop receptions count as the same empty path. This prevents repeated
packets or multiple names arriving over one route from increasing the vote
count.

For a node at the edge of the network where every packet arrives through one
relay path, `set clock.sync.mesh.edge on` changes the evidence requirement from
distinct receive paths to distinct sources. Signature-verified adverts are
deduplicated by public key, and Public-channel timestamps are deduplicated by
case-insensitive display name. Repeated packets from one source still count
once. Edge mode observes this verified evidence on the receive path, independently
of the forwarding decision, so `repeat off` and forwarding filters do not prevent
clock collection. Other packet types are observed normally but cannot be clock
evidence because they do not provide a suitable authenticated Unix timestamp.
Changing edge mode clears the in-memory sample table so evidence collected under
the other policy is not reused. The setting is persistent and defaults on.

At least the configured number of distinct fresh evidence sources (nine by
default) and a strict majority of all fresh samples must fall within ten minutes
of the median. In normal mode each source must use a distinct receive path. In
edge mode signed adverts are distinct by public key and Public-channel messages
are distinct by display name, but all may use the same receive path. The
effective quorum is therefore the larger of
`clock.sync.samples` and half the fresh sample count plus one. For example, a
9-vs-7 split can succeed but an 8-vs-8 split cannot. The median is used.
`clock.sync.samples` accepts `3` through `16`; samples older than two hours are
ignored. Status reports `mode=paths` or `mode=edge` and labels the collected
evidence as `paths` or `sources`. It reports `reason=need-more-paths` or
`reason=need-more-sources` when fewer than the configured number exist, and
`reason=no-consensus` when enough evidence exists but the effective quorum does
not agree. Mesh collection begins immediately after boot. Following a
successful estimate, it resumes two hours before the next seven-day deadline so
only evidence that can still be fresh at evaluation time is processed.

`get clock.sync.status` reports whether the clock was set and a `reason` when it
was not. Common reasons include `waiting-deadline`, `need-more-sources`,
`need-more-paths`, `no-consensus`, `within-drift`, `mesh-off`, and suppression
by CLI, GPS, or internet time. It also reports whether collection is active, the
fresh evidence count, the number of occupied table slots, and the next
evaluation deadline.

`get clock.sync.status.table` shows the active sample table in compact form.
Each item is `slot:type:id-prefix:age`, where type `A` is a signed advert and
type `P` is a Public-channel message. A trailing `!` marks a stale sample. If
the compact reply is truncated, query any slot with
`get clock.sync.status.<1-16>`. The detail view reports the full source and path
hashes, age-adjusted epoch, difference from the local clock, and freshness.

A timestamp is eligible for a clock-sync sample only when it falls between the
UTC build epoch embedded by `build.sh` and that time plus ten calendar years.
Direct developer builds that bypass `build.sh` fall back to the compiler
timestamp. Validation happens before a slot is selected or written, so an
advert or Public-channel timestamp outside that window is not recorded as a
clock sample.

Before voting, the node advances each packet timestamp by an estimated transit
time. The estimate sums the radio airtime at the original packet length and at
each progressively longer relay length, plus the expected midpoint of the
random flood-forward delay at every prior hop. The normal elapsed time since
the radio recorded local receipt is then added when consensus is evaluated, so
local signature/decryption/filter processing time is included too. This is
better than using hop count alone because LoRa airtime changes with packet
length and radio settings. Transit compensation is capped at the ten-minute
consensus window. It cannot know sender queueing, channel contention, or a
remote relay's non-matching `txdelay`, so the consensus window and median still
absorb residual error.

If a `clock sync` or `time <epoch>` CLI command successfully sets the clock, or
a GPS provider writes a valid GPS time, LoRa-derived clock collection and
correction are suppressed for the rest of that boot. Turning
`clock.sync.mesh` off and back on does not clear this safety latch; only a
reboot does. `get clock.sync.mesh` and `get clock.sync.status` report whether
CLI or GPS time caused the suppression. On a WiFi MQTT build, a successful NTP
sync is authoritative and also suppresses LoRa correction for the rest of that
boot. Source selection starts fresh after a reboot, so LoRa remains the fallback
when NTP cannot obtain internet time during that boot.

Public-channel display names are not authenticated and can be spoofed. Received
path hashes are also truncated, unauthenticated routing hints; requiring unique
paths prevents ordinary duplicate-route inflation but is not a cryptographic
identity check. Signed adverts authenticate the advert contents but do not
prove that the advertising node's own clock is correct. Mesh time is therefore
a consensus estimate, not an authoritative time service. Edge mode intentionally
gives up receive-path diversity. Public-channel display names can be spoofed, so
one sender can claim multiple names and inflate the edge-mode vote count.

`clock.sync.internet` is available on WiFi MQTT repeater-observer builds. Its
initial and seven-day queries run on the MQTT/WiFi task and are read-only until
the repeater applies the configured drift test. Failed queries retry after 30
minutes. On other infrastructure-node builds, the preference can be stored but
status reports that internet time is unavailable. MQTT builds retain their
existing startup NTP behavior required for MQTT/TLS/JWT operation; this setting
controls the additional delayed drift checks. Startup NTP is always preferred when it
succeeds, regardless of this setting.

Sensor and room-server builds support mesh clock consensus and report
`clock.sync.internet` as unavailable. Changing any `clock.sync.*` setting starts
a new attempt for the current boot.
Settings are persistent in `/clock_sync`; samples and schedule state are not.

A backward correction is intentionally allowed, but peers that already recorded
a later timestamp from this node may temporarily reject its lower timestamps as
replays until corrected time passes the previously observed value.

**Example:**
```text
set clock.sync.drift 600
set clock.sync.samples 3
set clock.sync.mesh on
set clock.sync.mesh.edge on
clock.sync.mesh now
get clock.sync.status
```

---

#### View this node's public key
**Usage:** `get public.key`

Returns this node's identity public key without changing it or exposing its
private key. Companion supports the query through its text terminal and
framed CLI command `0x42` (`CMD_RUN_CLI_COMMAND`); its reply is `> ` followed
by exactly 64 uppercase hexadecimal characters. Private-key export support
is not required. On nRF52 Full Companion, send the query to control interface
`00`, not the output-only logging interface `02`.

---

#### View this node's firmware version
**Usage:** `ver`

---

#### View this node's configured role
**Usage:** `get role`

---

#### View or change this node's power saving flag

**Search terms:** battery saver, device power saving, low power mode.

**Usage:**
- `get powersaving`
- `set powersaving on`
- `set powersaving off`

**Parameters:** 
- `on`: enable power saving
- `off`: disable power saving

**Default:** `on` for fresh Cascade-profile builds and Companion firmware; `off` for other infrastructure profiles

**Note:** `get powersaving`, `set powersaving on`, and `set powersaving off`
are shared by Companion and infrastructure. The saved preference controls
whether power saving is allowed; active USB, logging and network services may
keep the hardware awake. Actual sleep depends on the board.

On ESP32 infrastructure boards using an external USB-to-UART chip, such as the
Heltec V3's CP2102, the native USB host guard cannot detect an attached computer.
Current source blocks MCU light sleep while the raw UART console driver is
enabled, preserving the first web-console command after idle even with all
bridges and USB logging stopped. This guard has no idle timeout and also covers
a computer's first connection after boot. These builds enable that console at
startup, so MCU light sleep is unavailable while it remains enabled, including
on battery. `set powersaving on` still enables sensor/GPS power-saving policies;
the CPU continues normal idle yielding while keeping the console available.
A running MQTT, RS232 or ESP-NOW bridge keeps infrastructure awake even when
`get powersaving` reports `on`; that readback alone does not prove MCU sleep.
Negotiated RS232 standby can stop an unresponsive UART owner without changing
its saved enabled flag; see `rs232.autosleep` below. Other sleep blockers still apply.
Adding UART wake alone would still discard the characters that trigger wake,
so it cannot preserve an unmodified web console's first command. See the
[ESP-IDF UART wake documentation](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-reference/system/sleep_modes.html#uart-wakeup-light-sleep-only).

For the **1.17.1.5 G3 USB-disconnect report**, use `set powersaving off` as the
workaround. The released ESP32 sleep code can lose native USB after two
minutes when the terminal is closed, even with a computer attached. The
source fix blocks ESP32 light sleep while a native USB host is attached or
live USB logging is enabled; ordinary CPU idle/yield remains available.
The G3 button also wakes the device for at least two minutes. See the
[G3 release note](old-releases/1.17.1.5.md#g3-usb-disappearance-with-power-saving-enabled)
for verification steps and the distinction between the fix and the published
binaries.

ESP32 builds with button wake stay awake for at least 30 seconds after a press;
the G3 retains its longer two-minute hold. Repeaters wait five minutes after
startup before allowing sleep.

Companion firmware defaults this setting to `on`. Full Companion accepts the command from its local USB terminal and exposes the same setting in WebConfig. On ESP32 with processor speed set to Auto, it normally selects 80 MHz, enables idle yielding, and enables the configured GPS duty cycle. While a second radio profile is active, Auto holds the highest supported CPU speed for faster switching; SenseCAP Indicator retains its existing policy. Explicit processor speeds remain selected. USB and each active wireless transport remain available; SenseCAP Indicator Full keeps only its selected BLE or infrastructure-WiFi secondary transport active. `set powersaving off` restores the board's normal CPU clock and disables the GPS duty cycle. This device setting is separate from LoRa RXPS (`radio.rxps`) and WiFi modem power save (`wifi.powersave`). Infrastructure uses the same commands; its hardware and active-service sleep guards determine when the node can sleep.

#### Diagnose what prevents power saving

Companion, Repeater, Room Server and Sensor provide on-demand diagnostics:

```text
set powersaving.diag on
get powersaving.diag
get powersaving.diag calls
get powersaving.diag time
get powersaving.diag usb.session
get powersaving.diag bridge
get powersaving.diag 3
set powersaving.diag off
clear powersaving.diag
```

Diagnostics default to off and use only RAM. They do not change the saved
power-saving flag, USB/DTR behavior, radio settings, logging or bridge state,
and are lost on reboot. `on` starts a new window after `off`; repeated `on`
keeps the current window. `off` freezes the counters for later inspection.
`clear` resets them without changing whether collection is enabled. There is
no automatic diagnostic log stream or flash write.

The default/state page shows the last actual sleep decision (`why`, `mask`),
its age, reasons seen, total blocked time and eligible time in milliseconds.
Before the first observed decision it reports `unobserved`. Pages `3` through
`8` list every reason's transition count and blocked milliseconds; use an
exact reason name for a separate readback. Role guards preserve their normal
short-circuit order, so they report the first guard actually checked that
prevented sleep, not an exhaustive simultaneous inventory. Inner board vetoes
can add another reason. Reason times can overlap; `blocked_ms` on the state
page counts their union instead of adding them twice.

Reasons include `policy.off`, `startup`, `usb.session`, `usb.logging`,
`usb.recovery`, `wireless`, `button`, `morse`, `radio2`, `ota`, `calibration`,
`radio.watchdog`, `queue`, `retry`, `scheduled`, `serial`, `bridge`,
`bridge.cleanup`, `bridge.wake`, `network`, `mesh`, `tracker`, `board` and
`no.wake`. Not every board/role has every guard. `mesh` includes pending
protocol/storage work; `scheduled` can include a deadline too close for the
existing whole-second sleep policy. RS232 merely being compiled or saved as
enabled is not a blocker while its UART is suspended; running, cleanup and
due wake are distinguished where those guards apply. USB power from a charger
is not a host session. Logging enabled alone is not an nRF52 MCU sleep veto;
on ESP32, the existing logging and raw-console guards can veto light sleep.

ESP32 pages `9` and `10` count SDK call entries in sixteen 16-second buckets
starting at `start_ms`. They also report entries outside the first 256 seconds
and flags for outside entries (1), saturation (2) or missing timestamps (4).
Use a whole bucket contained within an independently timed interval to prove
SDK calls occurred during that interval; whole-window totals are insufficient.
See [power-saving diagnostics](power_saving_diagnostics.md) for the timing limits.

The `calls` page counts actual instrumented wait calls, returns, short returns,
board vetoes and SDK errors. `time` reports time spent inside those wait calls
in microseconds, including SDK and interrupt work, **not CPU sleep residency
or electrical current**. Returns under 100 us are marked short, not failed.
An eligible decision with many short calls can indicate wake/event activity;
an eligible ESP32 automatic-sleep path may yield without a direct instrumented
wait, so zero calls alone is not proof of a fault. `detail` records the last
board veto/error detail (SDK error code, or board-specific guard detail).
Counters saturate instead of wrapping; observation timestamps must be sampled
within one `millis()` wrap (about 49 days).

Opening USB to read the results can itself add a host/serial blocker. For a
battery test, enable collection, close the terminal, wait, then read the
accumulated pages. Stop collection promptly if preserving that window. A meter
is still needed to diagnose radio RX current, RXPS duty cycle, FEM/LNA gain,
GPS, displays, regulator losses and other loads that can consume power even
when the MCU sleeps correctly. See [power-saving diagnostics](power_saving_diagnostics.md).

Native USB ESP32 Full Companions can also use short light-sleep intervals when
the BLE/WiFi drivers, ESP-NOW, GPS UART, logging, and USB host sessions are
inactive and no work is pending. Compiling those transports into Full does not
itself prevent sleep. Turning off BLE advertising alone leaves its controller
running and therefore retains idle behavior rather than manual light sleep.

An active radio2 requires continuous software scanning. With power saving on,
ESP32 Companion, Repeater, Room and Sensor builds do not insert the ordinary
1 ms idle delay while dual-radio scanning is active. This avoids delaying
sub-millisecond scan visits; it does not make active dual-radio scanning a sleep mode.

The verified Meshnology W12 layout enables the LR2021 radio's DC-DC regulator
after LoRa and Morse receive initialization, including recovery. This setting
is independent of `powersaving` and does not remove radio features. Other
LR2021 layouts retain their existing regulator behavior unless their recipe
explicitly enables `LR2021_USE_DCDC` after confirming the required inductor;
the X1 is not opted in without that hardware confirmation.

---

#### View or set the reboot interval (Repeater, Room Server and Sensor)
**Usage:**
- `get reboot.interval`
- `set reboot.interval <hours>`

**Parameters:**
- `hours`: `0-255`; `0` disables scheduled reboots.

**Default:** `0` (disabled)

---

#### Control an exposed GPIO

**Availability:** ESP32 Repeater, Room Server, Bridge, and Sensor firmware. Companion firmware does not expose these commands. On nRF52, the commands are enabled only for Sensor builds on the Heltec T096, ProMicro, RAK3401, and RAK4631. GPIO expanders are not supported.

**Usage:**

- `get gpio` - list the Arduino pin numbers this firmware build permits
- `get gpio state`, `get gpio states`, or `get gpio status` - list every available pin currently controlled by the user (anything not in `reset`)
- `get gpio state <pin>` - show one pin's state; `states` and `status` are accepted here too
- `get gpio <pin>` - show `on`, `off`, or `reset`, plus any pending timed transition
- `set gpio <pin> on`
- `set gpio <pin> off`
- `set gpio <pin> reset`
- `set gpio <pin> <on|off> <duration> <on|off|reset>`

**Examples:**

- `set gpio 16 on 30 off` - drive GPIO16 high for 30 seconds, then drive it low
- `set gpio 16 on 5ms off` - drive GPIO16 high for 5 milliseconds, then drive it low
- `set gpio 16 off 5 reset` - drive GPIO16 low for 5 seconds, then return it to high impedance

An integer duration has seconds as its default unit, so `5` means 5 seconds. Add `ms` for milliseconds (`5ms`); an explicit `s` suffix is also accepted (`5s`). The maximum duration is 24 hours (86,400 seconds or 86,400,000 milliseconds). `on` or `off` without a duration remains in that state until another command, reset, or reboot.

`reset` changes the pin to an input with no pull resistor (high impedance). It does not reboot the node. It also cancels any pending timer for that pin. A new timed command for the same pin cancels and replaces the previous timer. GPIOs begin in `reset`; states and timers are not saved and are lost on reboot.

The immediate reply confirms the applied state and any pending transition. When a timed transition finishes, a command issued over the authenticated remote CLI receives a second report such as `> GPIO 16 timer complete: off`. Retries of the same authenticated timed command are recognized and do not restart its countdown.

The pin number is the Arduino pin number used by that target (the normal GPIO number on ESP32 and the board's `D`/pin index on nRF52). The available-pin list is build-specific. Radio, flash/PSRAM, USB, serial console, display, GPS, I2C, buttons, LEDs, battery measurement, power control, bridge, Ethernet, watchdog, and other pins claimed by the firmware are rejected. A pin must also be physically broken out on your board; `get gpio` cannot detect wiring or an attached peripheral that is not represented by the firmware configuration.

**Electrical warning:** GPIOs use 3.3 V logic and have limited drive current. Do not power a relay, motor, solenoid, or other load directly from a GPIO. Use a suitable transistor, MOSFET, optocoupler, or driver with the required protection components.

---

### Routing

#### View or set the direct path override for the current remote client
**Usage:**
- `get outpath`
- `get outpath path`
- `set outpath <hop1_hex,hop2_hex,...>`
- `set outpath path`
- `set outpath direct`
- `set outpath clear`
- `set outpath flood`

**Parameters:**
- `hopN_hex`: Hop hash with `2`, `4`, or `6` hexadecimal characters. Every hop must use the same width.

**Notes:**
- These commands require remote client context and update the caller's ACL entry.
- Room Server readers and writers may manage their own `outpath` and `altpath`
  without administrator permission. See [room return routes](room_services.md#your-own-return-routes)
  for the command list, session persistence and alternate delivery behavior.
- `get outpath path` reports the reciprocal `PAYLOAD_TYPE_PATH` received after
  the caller's latest flood login without changing the selected output route.
  Because that packet is asynchronous, an immediate query can report
  `> path pending`; retry shortly. The observation window expires after one
  minute, and the captured login path is not automatically selected.
- `set outpath path` copies that observed route to `outpath` and saves it. It
  returns an error without changing `outpath` if no route was received.
- `direct` selects a zero-hop route for a directly reachable caller.
- `clear` forgets the override, replies `> outpath cleared`, and allows normal
  path discovery to repopulate it.
- `flood` forces replies to use flood packets until the client logs in again.

---

#### View or change this node's repeat flag

**Search terms:** enable repeating, disable repeating, packet forwarding, stop relaying.

**Usage:**
- `get repeat`
- `set repeat <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** `repeat on`.

`repeat off` disables relay forwarding, not locally generated traffic or
configuration replies. Repeaters can separately opt in to the routed-trace
exception below.

#### Allow routed traces while repeating is disabled (repeater only)

```text
get repeat.trace
set repeat.trace on
set repeat.trace off
```

Default: `off`, including upgrades from older preference files. The setting is
saved and takes effect without reboot. Failed saves leave the active choice
unchanged. Room Server, Sensor, and Companion do not gain this exception.

To keep ordinary forwarding disabled while allowing routed trace diagnostics:

```text
set repeat off
set repeat.trace on
```

Only direct TRACE packets with a matching next hop may be relayed. Ordinary
messages, adverts, group data, ACKs, and control packets remain subject to
`repeat off`; flood traces remain invalid. Existing trace length, path-capacity,
duplicate, radio-profile, and channel/duty-cycle checks still apply. This is
a diagnostics opt-in, not authenticated-admin-only forwarding: trace packets
retain the protocol's existing authentication behavior.

`get repeat` and the disabled status bit continue to report repeating off.
With `repeat on`, traces use the ordinary forwarding policy regardless of
`repeat.trace`. Changing the exception to off while repeating is off blocks
subsequent relayed trace transmissions, including queued copies and retries;
a trace already on air is allowed to finish. Locally initiated traffic and
management replies are not cancelled.

The dynamic configuration field is `repeat.trace_off` (`0` or `1`). It is
appended to `/com_prefs`; all field offsets in the published upstream layout
stay unchanged.

---

#### View or change this node's advert path hash size
**1.17.1.5 infrastructure bug:** affected Repeater, Room Server, and Sensor
images accept `get path.hash.mode` but report `unknown config` for the setter.
The command was not renamed. The source correction restores the setter;
installed images need an updated build. See the [setting-dispatch audit](test-results/cli_settings_audit.md).

**Usage:**
- `get path.hash.mode`
- `set path.hash.mode <value>`

**Parameters:**
- `value`: Path hash size (0-2)
  - `0`: 1 Byte hash size (256 unique ids)[64 max flood]
  - `1`: 2 Byte hash size (65,536 unique ids)[32 max flood]
  - `2`: 3 Byte hash size (16,777,216 unique ids)[21 max flood]
  - `3`: DO NOT USE (Reserved) 

**Default:** `0` (1 byte). Cascade Companions use `1` (2 bytes), and Cascade
Repeaters use `2` (3 bytes). Existing saved values are preserved.

**Note:** the 'path.hash.mode' sets the low-level ID/hash encoding size used when the repeater adverts. This setting has no impact on what packet ID/hash size this repeater forwards, all sizes should be forwarded on firmware >= 1.14. This feature was added in firmware 1.14

**Temporary Note:** adverts with ID/hash sizes of 2 or 3 bytes may have limited flood propagation in your network while this feature is new as v1.13.0 firmware and older will drop packets with multibyte path ID/hashes as only 1-byte hashes are supported. Consider your install base of firmware >=1.14 has reached a criticality for effective network flooding before implementing higher ID/hash sizes. 

---

#### View or change this node's loop detection
**Usage:**
- `get loop.detect`
- `set loop.detect <state>`

**Parameters:**
- `state`: 
  - `off`: no loop detection is performed
  - `minimal`: packets are dropped if repeater's ID/hash appears 4 or more times (1-byte), 2 or more (2-byte), 1 or more (3-byte)
  - `moderate`: packets are dropped if repeater's ID/hash appears 2 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  - `strict`: packets are dropped if repeater's ID/hash appears 1 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  
**Default:** `off`

**Note:** When it is enabled, repeaters will now reject flood packets which look like they are in a loop. This has been happening recently in some meshes when there is just a single 'bad' repeater firmware out there (probably some forked or custom firmware). If the payload is messed with, then forwarded, the same packet ends up causing a packet storm, repeated up to the max 64 hops. This feature was added in firmware 1.14

**Example:** If preference is `loop.detect minimal`, and a 1-byte path size packet is received, the repeater will see if its own ID/hash is already in the path. If it's already encoded 4 times, it will reject the packet.  If the packet uses 2-byte path size, and repeater's own ID/hash is already encoded 2 times, it rejects. If the packet uses 3-byte path size, and the repeater's own ID/hash is already encoded 1 time, it rejects. 

---

#### View or change the retransmit delay factor for flood traffic

**Search terms:** flood forwarding delay, flood retransmit delay, flood TX delay.

**Usage:**
- `get txdelay`
- `set txdelay <value>`

**Parameters:**
- `value`: Transmit delay factor (0-2)

**Default:** `0.5`

**Note:** When multiple nearby repeaters all hear the same flood packet, each waits a random amount of time before retransmitting to avoid simultaneous collisions. This factor scales the size of that random window. Higher values reduce collision risk at the cost of added latency. `0` disables the window entirely.

---

#### View or change the retransmit delay factor for direct traffic

**Search terms:** direct forwarding delay, direct retransmit delay, direct TX delay.

**Usage:**
- `get direct.txdelay`
- `set direct.txdelay <value>`

**Parameters:**
- `value`: Direct transmit delay factor (0-2)

**Default:** `0.3` (Repeater) - `0.2` (Room Server, Sensor)

**Note:** Same collision-avoidance random window as `txdelay`, but applied to direct (non-flood, routed) traffic. The default is lower because direct packets are addressed to a specific next hop, so far fewer nodes compete to retransmit them.

---

#### [Experimental] View or change the processing delay for received traffic
**Usage:**
- `get rxdelay`
- `set rxdelay <value>`

**Parameters:**
- `value`: Receive delay base (0-20)

**Default:** `0.0`

**Note:** When enabled, repeaters that received a flood packet with a weak signal are held in a delay queue before processing, while those that received it with a strong signal process it immediately. This gives strong-signal paths forwarding priority. By the time weak-signal nodes process their copy, the packet may have already propagated and will be suppressed as a duplicate, reducing redundant retransmissions.

---

#### View or change the duty cycle limit
**Usage:**
- `get dutycycle`
- `set dutycycle <value>`

**Parameters:**
- `value`: Duty cycle percentage (1-100)

**Default:** `50%` (equivalent to airtime factor 1.0)

**Examples:**
- `set dutycycle 100` - no duty cycle limit
- `set dutycycle 50` - 50% duty cycle (default)
- `set dutycycle 10` - 10% duty cycle
- `set dutycycle 1` - 1% duty cycle (strictest EU requirement)

> **Note:** Added in firmware v1.15.0

---

#### View or change the airtime factor (duty cycle limit)
> **Deprecated** as of firmware v1.15.0. Use [`get/set dutycycle`](#view-or-change-the-duty-cycle-limit) instead.

**Usage:**
- `get af`
- `set af <value>`

**Parameters:**
- `value`: Airtime factor (0-9). After each transmission, the repeater enforces a silent period of approximately the on-air transmission time multiplied by the value. This results in a long-term duty cycle of roughly 1 divided by (1 plus the value). For example:
  - `af = 1` -> ~50% duty
  - `af = 2` -> ~33% duty
  - `af = 3` -> ~25% duty
  - `af = 9` -> ~10% duty
  You are responsible for choosing a value that is appropriate for your jurisdiction and channel plan (for example EU 868 Mhz 10% duty cycle regulation).

**Default:** `1.0`

---

#### View or change the local interference threshold
**Usage:**
- `get int.thresh`
- `set int.thresh <value>`

**Parameters:**
- `value`: Interference threshold value

**Default:** `0.0`

---

#### Enable or disable hardware Channel Activity Detection (CAD)
**Usage:**
- `get cad`
- `set cad <on|off>`

**Description:** When enabled, the radio performs a hardware Channel Activity Detection scan before transmitting and defers if the channel is busy. Runs independently of `int.thresh` - either, both, or none may be active.

Fresh Cascade-profile builds default CAD to `off` across all roles, including
Companion. Saved CAD settings take precedence after an upgrade. Use `set cad off`
to disable it on an existing node or `set cad on` to enable it explicitly.
The repeater applies the saved toggle to the radio during its periodic
noise-floor service. With one runnable packet, a busy result uses the normal
CAD retry delay and allows roughly four seconds of continuous busy results.
As the runnable transmit queue grows, both delays are divided by its depth:
retry spacing will not fall below 50 ms and the busy ceiling will not fall
below 500 ms. Reaching the busy ceiling records a CAD-timeout error and
attempts the next queued transmission rather than waiting indefinitely.
Future-scheduled packets do not accelerate CAD. `get cad` includes the
hardware busy-result count.

**Parameters:**
- `on|off`: Enable or disable hardware CAD

**Default:** `off`

---

#### View or change the AGC Reset Interval
**Usage:**
- `get agc.reset.interval`
- `set agc.reset.interval <value>`

**Parameters:**
- `value`: Interval in seconds, 0-1020, rounded down to a multiple of 4 (17 becomes 16). 0 to disable.

**Default:** `0.0`

With device `powersaving` on and a nonzero AGC interval, Companion, Repeater,
Room Server, and Sensor firmware use adaptive reset scheduling. The saved
interval is the starting/minimum interval. After every two admitted maintenance
attempts without an observed radio recovery, the interval doubles, capped at
60 seconds (or the saved interval if it is already longer). For example,
8 -> 16 -> 32 -> 60 seconds, with two attempts at each step. An observed radio
recovery returns the schedule to the saved interval. Changing the interval or
power-saving mode starts a fresh schedule; adaptive history is not persisted.

`get agc.reset.interval` still reports the saved starting interval, not the
current adaptive interval. `0` disables periodic AGC maintenance even with
power saving on. With power saving off, fixed-interval behavior is retained.
This follows device `powersaving`, independently of LoRa `radio.rxps`.

Adaptive scheduling does not treat a quiet mesh, strong packets, a stable
noise floor, or a successful transmission as proof of full receiver sensitivity.
TX cannot continually postpone adaptive maintenance. Pending RX/TX, a BUSY
chip outside known RX duty cycling, carrier/Morse ownership, and an active
noise-floor block can defer it; deferred attempts do not advance backoff and
normally retry after 50 ms. When a failed receive start already has a later
retry deadline, adaptive AGC waits at least until that deadline too, allowing
sleep during persistent receive-start failures. The
60-second bound is a scheduling interval, not permission to interrupt a packet.
An armed RXPS radio uses the existing explicit wake/reset path, avoiding retry
polling that could miss every short listen window.
Existing radio recovery watchdogs remain active. These conservative intervals
have host regression coverage, but are not hardware-qualified sensitivity or
battery-life results; noisy sites may require a short fixed interval with
device power saving off.

---

#### Periodic transmit-only text beacons

See [TX beacons](tx_beacons.md) for the 12 independent MeshCore Public or
Meshtext slots. `set beacon.interval 12|24|36|48` selects the shared cycle;
the default is 24 hours. Slots are staggered and disabled until configured.

#### View or change the radio watchdog interval (MQTT observer only)
**Usage:**
- `get radio.watchdog`
- `set radio.watchdog <minutes>`

**Parameters:**
- `minutes`: `0` to use the standard 30-minute soft recovery interval, or `1-120` minutes to override it

**Default:** `5`

**Note:** This setting belongs to the MQTT observer runtime and is not
available on a standalone FULL repeater. It sets the interval without a
successfully received packet before soft radio recovery. Transmissions and
interrupts alone do not restart the timer. Setting it to `0` removes the
observer override; automatic radio recovery remains active. The normal hard
recovery threshold is 12 hours, where the radio supports it, and recovery does
not reboot the whole board. On quiet meshes, increasing the soft interval can
reduce unnecessary recoveries when no traffic is expected.

---

#### Enable or disable Multi-Acks support
**Usage:**
- `get multi.acks`
- `set multi.acks <state>`

**Parameters:**
- `state`: `0` (disable) or `1` (enable)

**Default:** `0`

---

#### View or change the flood advert interval
**Usage:**
- `get flood.advert.interval`
- `set flood.advert.interval <hours>`

**Parameters:**
- `hours`: Interval in hours (3-168)

**Default:** `47` (Repeater, Room Server) - `0`, disabled (Sensor)

---

#### View or change the zero-hop advert interval
**Usage:**
- `get advert.interval`
- `set advert.interval <minutes>`

**Parameters:**
- `minutes`: Interval in minutes rounded down to the nearest multiple of 2 (61 becomes 60) (60-240)

**Default:** `2` on a factory-fresh node, then `0` (disabled) once configured.

**Note:** A new install ships with a 2 minute zero-hop advert interval. Saving
any setting resets an interval below the 60 minute minimum to `0`, on the
assumption that the node has now been deliberately configured. To keep zero-hop
adverts running, set an explicit value in the 60-240 range.

---

#### Limit the number of hops for a flood message

**Search terms:** hop limit, maximum hops, max hops, flood distance.

**Usage:**
- `get flood.max`
- `set flood.max <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64)

**Default:** `64`

---

#### Limit the number of hops for an unscoped flood message
**Usage:**
- `get flood.max.unscoped`
- `set flood.max.unscoped <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for a packet without a scope (no region set)

**Default:** `0xFF` - indicates it hasn't been set, will track flood.max until it is.

**Note:** An alternative to `region denyf *`, setting `flood.max.unscoped` to a lower value such as `3` would allow for local unscoped messages to propagate, while preventing noisy neighbors from flooding a local region.

---

#### Limit the number of hops for an advert flood message
**Usage:**
- `get flood.max.advert`
- `set flood.max.advert <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for an advert packet

**Default:** `8`

---

#### Forward flood group data packets on repeaters
**Usage:**
- `get flood.channel.data`
- `get flood.channel.data.hops`
- `set flood.channel.data <on|off>`
- `set flood.channel.data.hops <all|1-7>`

**Parameters:**
- `on`: Retransmit received flood `GRP_DATA` channel packets.
- `off`: Do not retransmit received flood `GRP_DATA` channel packets.
- `all`: When `flood.channel.data` is `off`, block `GRP_DATA` at any received flood hop count.
- `1-7`: When `flood.channel.data` is `off`, repeat `GRP_DATA` at this hop count or lower and block longer paths.

**Default:** `flood.channel.data on`; `flood.channel.data.hops h=all`

**Forwarding behavior:** Repeater firmware only. The repeater still receives and
logs the packet when logging is enabled; this only blocks retransmission.
On generalized repeaters these commands manage an ordinary visible FPF7
`type=grp_data` drop row. `off` with `all` maps to `hops=all`; `off` with `N`
maps to `hops=N+1+`. The 240 KB compact FPF6 profiles retain the legacy hard
gate. Because it is an ordinary FPF7 row, a matching higher-priority `stop`
rule can exempt traffic from it. The compact `get flood.filter` list marks the
managed row with `~data`. Flood group text (`GRP_TXT`) is unaffected by this
setting.

`get flood.channel.data` includes the active hop gate as `h=all` or `h>N`.

---

#### Bridge and radio crossover filters

`mode=` selects where a generalized `flood.rule` / `flood.filter` row applies:

| Mode | Where the rule applies |
| --- | --- |
| `radio` | Ordinary LoRa forwarding; flood-only unless `route=direct` or `route=any` is selected. This is the default mode. |
| `bridge` | Packets entering or leaving an RS232 or ESP-NOW bridge, and MeshCore RX/TX packet publication through MQTT. |
| `cross` | Transmissions copied to the other radio profile, including a copy's retries. The same-profile transmission remains eligible. |
| `bridge,cross` | Both transport paths using one rule. `cross,bridge` is equivalent. |

The third built-in repeater rule blocks authenticated `#wardriving` text and
data at all hops on bridge and crossover paths, including temporary-radio
sessions. It leaves same-profile traffic subject to the separate four-hop
rule. `set radio2.cross on` does not override this filter.

```text
# Factory slot: inspect before replacing an existing rule.
get flood.rule.3
set flood.rule.3 type=any channel=#wardriving hops=all mode=bridge,cross drop
# On an existing device, add it without overwriting an occupied slot.
set flood.rule type=any channel=#wardriving hops=all mode=bridge,cross drop
```

Saved tables are preserved on upgrade; new defaults are seeded only when the
table is first initialized. Bridge/cross modes accept drop, rate, priority and
stop actions; scope rewrites, retry actions and TX timing are rejected.
These modes cover direct as well as flood packets, in both crossing directions.
The default authenticates the channel key, avoiding false drops from short
channel-hash collisions. FULL room servers support explicit transport rules;
companions and compact FPF6 profiles do not expose this table.

See [filtering documentation](flood_filtering.md#filter-bridge-and-radio-crossover-traffic)
for persistence, queue/rate semantics, exceptions, and how to remove one or
both blocks.

#### Block selected flood channels with FPF7

The separate `flood.channel.block` command and 15-row table have been retired.
Generalized repeaters use the 63-row FPF7 forward phase for authenticated channel
blocks:

```text
set flood.rule type=any channel=#test hops=all drop
set flood.rule.2 type=any channel=#wardriving hops=5+ drop
set flood.rule type=any channel=9cd8fcf22a47333b591d96a2b848b73f hops=4+ drop
get flood.rule
del flood.rule.2
```

`type=any` with a channel condition can authenticate only `GRP_TXT` and
`GRP_DATA`, so it does not match other payload types. Use `hops=all` to block
at every received hop count. To preserve the old `h=N` meaning of repeating
through `N` hops and blocking longer paths, use `hops=N+1+`; old `h=4` is
therefore `hops=5+`.

New generalized repeater tables seed slot 2 with the second example. Existing
FCB2 rows are imported once into free FPF7 slots and the retired file is then
removed. The fixed-size STM32WL FPF6 build cannot match authenticated channels.

---

#### Force a transport scope onto floods
**Usage:**
- `get flood.channel.scope`
- `get flood.channel.scope.<n>`
- `set flood.channel.scope <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]`
- `set flood.channel.scope.<n> <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]`
- `del flood.channel.scope.<n>`
- `del flood.channel.scope all`

`fs` is the short family name for all these commands. Short options are
`p=bl` (`path=blacklist`), `p=b1` through `p=b6` (`path=bucket:1` through
`path=bucket:6`), and `tx=s`/`tx=f` (`tx=slow`/`tx=fast`).

**Parameters:**
- `n`: Slot number within the table compiled for the target. Roomy ESP32 builds
  provide `1-255`; classic ESP32 repeaters and nRF52/other
  normal constrained builds provide `1-31`; very-tight STM32WL repeaters provide
  `1-15`; the no-PSRAM LilyGo T-LoRa V2.1 repeater/observer provides `1-4`.
- `channel`: `public`, a public `#channel`, or a 128/256-bit channel key in hex.
- `txt:*`: Unauthenticated fallback for otherwise-unmatched `GRP_TXT` and
  `GRP_DATA`. Plain `*` is an alias for `txt:*`.
- `login:*`: Type-based wildcard for the remote-login/admin family: `REQ`,
  `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH` (`0x00`, `0x01`, `0x02`,
  `0x07`, and `0x08`). It classifies the outer type; a transit repeater cannot
  authenticate whether a packet is actually part of a login session.
- `other:*`: Type-based wildcard for every remaining flood payload type,
  including flood-form TRACE, ACK, advert, multipart, control, OTA, reserved
  types, and raw custom.
- `region`: Existing named region with a usable transport key. A unique region
  name prefix is accepted; wildcard region `*` is not a scope target.
- `scope=<name>`: Regionless alternative to `region`. The public name is
  normalized with a leading `#`, and its 128-bit transport key is derived
  directly from that hashtag exactly as for `flood.filter scope=<name>`. It
  does not need to exist in the region list. Public names up to 30 characters
  are accepted; private `$` scopes are not.
- `path=blacklist`: Optional. Require the received path to match the passive
  `flood.filter.blacklist` ID table. No `flood.filter` drop row needs to be
  enabled. One exact listed ID qualifies a 3-byte path. A 2-byte path requires
  two matching received path entries, while a 1-byte path never qualifies.
- `path=bucket:<1-6>`: Optional alternative to `path=blacklist`. Match IDs in
  the selected persistent `flood.retry.bucket`. Each bucket holds up to 17
  three-byte IDs and remains usable when `flood.retry.bridge` is off. It uses
  the same 3-byte, 2-byte, and 1-byte thresholds as `path=blacklist`.
  `recent.repeater` freshness and `flood.retry.ignore` do not affect this
  passive match.
- `tx=slow`: Optional. Use an effective inbound `rxdelay` base of
  `max(2, configured rxdelay * 2)`, keep normal outbound queue priority, and
  schedule retransmission with the maximum supported `txdelay` factor of
  `2.0` after changing the scope. The default is fast; `tx=fast` may be
  supplied explicitly when replacing a slow row.

**Default:** No forced scopes.

Remote ACL permission `4` (region/scope manager) can use all `get`, `set`, and
`del flood.channel.scope` forms. Filter managers and other non-admin roles
cannot change this table.

Without `.n`, `set` updates the row for the same exact channel key or wildcard
class with the same path selector, otherwise it uses the first empty slot.
This permits an ordinary fallback and separate blacklist or bridge-bucket
rows for the same channel. With `.n`, it replaces that slot. The three
wildcard classes are independent and consume one slot each.
`get flood.channel.scope` reports active/total slot counts; use the numbered
form for row detail. Keyed rows are displayed by the first four bytes of their
derived channel hash because channel secrets are never returned. Regionless
targets are displayed with their normalized leading `#`.

This acts on received `ROUTE_TYPE_FLOOD` and
`ROUTE_TYPE_TRANSPORT_FLOOD` packets. An unscoped packet gains the configured
scope; an already-scoped packet has its existing transport codes replaced. For
`GRP_TXT` and `GRP_DATA`, all exact channel-key rows are tried first and must
validate the packet MAC/decryption. Matching path-qualified exact rows are
  tried before ordinary exact fallback rows. A region-backed row whose target
  is missing or unusable is skipped; later exact rows and then `txt:*` are tried.
Exact keyed rows with a usable target therefore beat `txt:*` regardless of
slot number. Within each wildcard class, path-qualified rows similarly precede
ordinary fallback rows. `login:*` and `other:*` select their non-overlapping
outer-type families without decrypting the payload. The lowest usable slot
wins within each priority tier.

Standard traceroute is direct-routed and is therefore outside this flood-only
table. A custom flood-form `TRACE` is treated like every other flood: an
applicable wildcard may rewrite it and region/unknown-code gates still apply.

On a match, the repeater sets the route to `ROUTE_TYPE_TRANSPORT_FLOOD`,
computes transport code 0 from the selected region or direct hashtag key and
packet payload, and sets transport code 1 to zero. This occurs before region
enforcement, forwarding filters, and the seen-packet lookup. For an
already-scoped packet, the selected code replaces both incoming transport-code
fields. Direct routes are never rewritten. A packet converted from unscoped is
no longer subject to `flood.max.unscoped`; all rewritten packets remain subject
to normal payload handling, `flood.max`, `flood.filter`,
loop detection, and moderation. Assigning a scope does
not make a packet type forwardable if the core would otherwise reject it. By
default, if the selected scope differs and the rewritten packet is accepted
for forwarding, its initial retransmission uses zero `txdelay` and the highest
outbound queue priority so the newly scoped copy can win at the next hop.
Adding `tx=slow` uses an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, keeps the ordinary queue priority, and uses
the maximum `txdelay` factor of `2.0` for the retransmission. As with ordinary
`txdelay`, the actual transmit delay is randomized from zero through the
resulting window; factor `2.0` gives a maximum of ten packet airtimes.
Neither mode preempts an active radio transmission or bypasses CAD and
airtime-budget limits. Selecting the scope already carried by the packet is a
no-op and does not grant special transmit treatment.

A region-backed target must be locally flood-allowed and remains subject to
the normal region gate. A `scope=<name>` target is trusted for this matched
receive pass even though it has no region-list entry, matching the behavior of
`flood.filter scope=<name>`. It does not create a region, consume a region
slot, or change the allow/deny state for unrelated packets carrying the same
transport code.

If a region-backed row's target has been removed or has no usable key, the repeater
tries the next applicable row. For group packets this means later authenticated
exact rows followed by `txt:*`; wildcard duplicates likewise fall through to
the next usable slot. When no usable mapping exists, the packet retains its
original unscoped or scoped route.

LoRa OTA remains functional when `other:*` is configured. OTA packets are
given that target's transport code, replacing an existing code when necessary,
but the OTA handler still accepts and re-floods them during the temporary-radio
window. A region target must allow flooding; a direct target follows the
regionless trust behavior above. The OTA core itself is dormant outside that
window; no default flood filter row is needed for that behavior. Forced scope
does not make OTA operate outside the window.

**Capacity cost:** Each rule slot retains its 36-byte runtime and persistent
record. A separate 32-byte-name table holds up to the smaller of the rule count
or 32 distinct regionless targets; very-tight STM32WL builds hold one reusable
direct target. Region-backed targets do not consume this table.
The four-slot minimum uses 272 bytes RAM and a 278-byte file; it has room for
the three wildcard classes plus one exact channel mapping. Very-tight 15-slot
builds use 572 bytes RAM and a 578-byte file. The 31-slot table uses 2,108
bytes RAM and a 2,114-byte file. Roomy ESP32 builds use 255 rule slots and 32
direct-target slots: 10,204 bytes RAM and a 10,210-byte file. Classic ESP32
LoRa-OTA builds that cannot afford the 255-rule table use 31 slots instead.
Both configured regions and regionless targets can be reused by any number of
rules.

**Duplicate behavior:** Mesh dedup hashes payload type and payload bytes; it
does not hash route type, transport codes, or the ordinary flood path. Adding
a transport scope therefore does not create a new duplicate identity. If the
same payload later arrives scoped, unscoped, or through a different region, it
is still the same seen packet. `TRACE` is the exception only in that its
encoded `path_len` byte is also hashed.

While equivalent non-TRACE flood copies are waiting in `rxdelay`, the normal
receive-quality timing still selects the packet to process, but that winner
receives a scope from the queued scoped copies with the same dedupe identity. If the copies
carry different locally allowed scopes, the scope from the shortest received
path wins. Unknown and denied transport codes are not candidates and therefore
cannot overwrite an unscoped winner. With equal path lengths, the deeper child
region wins because it is narrower. A remaining tie keeps queue order. The
winner's own path, SNR reading, and scheduled time are not changed, and an
already-scoped winner may have its code replaced by the better queued scope.

The comparison is deferred until dequeue so each copy retains its original
scope and path for arbitration. It can only use copies still present in
`rxdelay`; it cannot replace a packet that already won the dedupe race.
Flood-form TRACE participates in this arbitration; direct traceroute does not
enter the flood queue.

A packet that matches a fast `flood.channel.scope` or `flood.filter scope=`
action and needs its scope changed bypasses the inbound `rxdelay` queue. A
`tx=slow` row remains in that queue with twice the configured base, floored at
`2.0`, and participates in normal queued-copy scope arbitration.

**Examples:**
```text
region put west
region save
set flood.channel.scope #local west
set flood.channel.scope.2 txt:* west tx=slow
set flood.channel.scope.3 login:* west
set flood.channel.scope.4 other:* west
get flood.channel.scope
get flood.channel.scope.1
del flood.channel.scope.2
```

A regionless exact mapping needs no `region` command:

```text
set flood.channel.scope #rgdata scope=BlackHole86
get flood.channel.scope
get flood.channel.scope.1
```

For example, if an authenticated `#rgdata` packet arrives carrying scope
`#usa`, that rule replaces `#usa` with `#BlackHole86` before forwarding. The
rule also assigns `#BlackHole86` when the packet is unscoped or carries any
other scope; it is a channel-to-target mapping, not an incoming-scope filter.

To use bridge bucket 1 to assign `east` to `public` packets whose received
3-byte path contains `7576FB`, while assigning `west` to every other
authenticated `public` packet:

```text
set flood.retry.bucket 1 7576FB
set flood.channel.scope public west
set flood.channel.scope public east path=bucket:1
```

Additional 3-byte IDs may be added to bucket 1 later; any one exact hit
qualifies the `east` row. This use is passive and does not require
`flood.retry.bridge` to be enabled. The separate blacklist selector remains
available for tables shared with `flood.filter path=blacklist` rules.

---

#### Require valid incoming scopes only on selected channels

**Usage:**
- `get flood.channel.scope.require`
- `get flood.channel.scope.require.<n>`
- `set flood.channel.scope.require <public|#channel|128/256-bit-key>`
- `set flood.channel.scope.require.<n> <public|#channel|128/256-bit-key>`
- `del flood.channel.scope.require.<n>`
- `del flood.channel.scope.require all`

`fsr` is the short family name, with the same arguments and region/scope-manager
permissions.

**Default:** Empty; normal global region enforcement remains active.

Once this table contains a row, received flood `GRP_TXT` and `GRP_DATA`
packets use selective region enforcement. A packet authenticating against a
listed channel key must already carry a transport scope matching a locally
flood-allowed region. Listed channels arriving unscoped, with an unknown code,
or with a denied region are not retransmitted. This tests the original
incoming scope before any `flood.channel.scope` or `flood.filter scope=`
rewrite. Those rewrite actions are skipped for a rejected listed channel, so
they cannot rescue it or grant special receive/transmit timing.

Other group channels bypass the region/unknown-code gate while the table is
active. They remain subject to every other forwarding control, including
`repeat`, `flood.max*`, packet filters, loop detection, payload
validation, and moderation. Non-channel flood payload types retain normal
global region enforcement.

Channel matching validates the packet MAC/decryption with the configured key;
the visible one-byte channel hash is only a prefilter. Public hashtag channels
use their derived public key. Without `.n`, setting an existing key updates it
and a new key uses the first empty slot. Numbered `set` replaces that slot.
`get ...<n>` reports a four-byte derived prefix and key size without exposing
the key. The table uses the same build-dependent slot count as
`flood.channel.scope`.

Remote ACL permission `4` can manage this table. Deleting its final row
restores normal global region enforcement for group channels.

**Example:**
```text
set flood.channel.scope.require #bot
get flood.channel.scope.require
get flood.channel.scope.require.1
```

---

#### Change persistent flood rules in the field

For setup guidance, interactions with the existing forwarding controls, and
worked moderation examples, see [Flood Filtering and Moderation](flood_filtering.md).

**Usage:**

- `get flood.rule`
- `get flood.rule.<n>`
- `get fr[.<n>]` (numbered form returns a complete short setter for copying)
- `set fr[.<n>] <type> [h=<range>] [m=r|b|c|bc] [c=<channel>] [pb=any|1|2|2+|3] <d|s=<scope>|r=<region>|q=<N>|r|s> [...]`
- `del fr.<n>` or `del fr all`
- `set flood.rule[.<n>] type=<type> [mode=radio|bridge|cross|bridge,cross] [hops=<range>] [channel=<channel>] [hashbytes=any|1|2|2+|3] [prefix=<path-prefix>] [in=<input-scope>] <drop|scope=<name>|region=<name>|rate=<N>/min|retry|stop> [priority=<0-255>] [tx=slow] [suspend=tempradio]`
- `del flood.rule.<n>`
- `del flood.rule all`
- `get flood.filter`
- `get flood.filter.<n>`
- `get flood.filter.blacklist`
- `get flood.filter.blacklist.<n>`
- `set flood.filter.blacklist <ID[,ID...]>`
- `set flood.filter.blacklist.<n> <ID[,ID...]>`
- `del flood.filter.blacklist`
- `del flood.filter.blacklist.<n>`
- `set flood.filter <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]`
- `set flood.filter.<n> <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]`
- `del flood.filter.<n>`
- `del flood.filter all`

Generalized rows also accept `sender=`, `text=`, `len=`, `route=`, `prob=`,
`enabled=`, and anchored `path=` chains. These use the same table and ordering;
see the parameters and short forms below.

The extended table is available on repeaters with the rule engine enabled and
on FULL-profile ESP32 room servers. A FULL room server exposes
`fr`, `flood.rule`, and `flood.filter`, has 31 slots, and requires an administrator
for remote changes. It does not have the repeater's passive path blacklist, so
`flood.filter.blacklist*` and `path=blacklist` are repeater-only; use the
ordered `prefix=` match on a room server. Standard room-server profiles do not
compile this table.

**Parameters:**

- `n`: Forward-rule slot in the build's compiled table (`1-63` on generalized
  repeaters and `1-31` on FULL room servers; compact profiles may use fewer).
- `mode`: Optional; omitted means `radio`. `bridge` filters bridge admission,
  `cross` filters copies to the other radio profile, and `bridge,cross` covers
  both with one row. `cross,bridge` is an alias. See the
  [mode matrix](#bridge-and-radio-crossover-filters) for scope and supported actions.
- `type`: Payload type name, full `PAYLOAD_TYPE_*` name, decimal value `0-15`,
  hexadecimal value `0x00-0x0F`, or `any`.
- `hops`: Optional; omitted means `all`.
  - `N`: Match only at received hop count `N`.
  - `N+`: Match at received hop count `N` and higher.
  - `N-M`: Match the inclusive received-hop range.
  - `all`: Match every received hop count (`0-63`).
  - `0+`, `all`, and an omitted hop expression are equivalent. The CLI displays
    the saved range as `all`.
- `channel=*|public|#name|hash:XX|128-bit-key|256-bit-key`: Optional channel match.
  `channel=*` means no channel condition at all, so the row matches everything
  selected by `type=` (including all flood payload types with `type=any`). It
  does not authenticate a packet. `public`, `#name`, and raw keys authenticate
  one channel and therefore narrow the row to `GRP_TXT`/`GRP_DATA`.
  `hash:XX` matches only the visible one-byte group-channel hash; `short:XX`
  and a bare two-digit byte are accepted aliases and are displayed as
  `hash:XX`. This form is deliberately unauthenticated. It can collide with
  another channel once in 256 hash values, and a sender can choose the byte,
  so use an exact channel name or key whenever it is available. In particular,
  `channel=hash:11` does not mean Public: it matches Public plus every collision
  or deliberately selected `0x11` value. `channel=public` performs the deeper
  MAC/decrypt check with the Public channel key. That distinguishes an ordinary
  `0x11` collision, but it is channel authentication rather than sender
  authentication: the group MAC is two bytes and the Public key is shared.
- `hashbytes=any|1|2|2+|3`: Optional encoded path-hash width, independent of hop
  count and hash value. Omitted, `any`, or `*` matches every supported width.
  Width-specific rules also match zero-hop packets. Four-byte hashes are
  reserved and unsupported. `2+` selects both 2- and 3-byte paths in one row,
  sharing any rate limit across both. A simultaneous `prefix=` must satisfy the
  selector; a 2- or 3-byte prefix narrows `2+` to its own exact width.
  Example: `set fr any pb=1 d` drops all 1-byte floods from forwarding, including
  relayed login/admin traffic, but not direct packets or local delivery.
- `prefix=<ID[,ID...]>`: Optional ordered source-path prefix of one to three
  pbyte IDs. IDs must all be 2, 4, or 6 hex characters, matching a packet's
  1-, 2-, or 3-byte pbyte width. `p=` is the short form.
- `path=[^]ID>ID...[$]` or `pa=...`: Ordered chain of up to four IDs, matched
  anywhere in a flood path. `^` anchors the earliest relay and `$` the most
  recent; both require exactly the chain. Commas may replace `>`.
  Each ID must use the packet's complete hash width. Direct paths and TRACE
  SNR bytes never match. `path=blacklist` remains the separate unordered table.
- `sender=<pattern>` (`n=`), `text=<pattern>` (`x=`): Patterns on the decrypted
  sender name and message body. Matching is case-sensitive by default; a
  leading `(?i)` ignores ASCII letter case in literals and character classes.
  Non-ASCII bytes remain unchanged. Require an explicit
  authenticated channel (`public`, `#name`, or key); `channel=*` and raw hashes
  are insufficient. These match only plain group text. Patterns search
  anywhere unless anchored with `^`/`$`, and support `.`, classes such as
  `[A-Z]` or `[^0-9]`, `*`, `+`, `?`, groups, `|`, and escaped literals.
  Use `[0-9]` for digits and quote values containing spaces. The maximum is
  63 bytes per pattern, including `(?i)`. Matching uses complete fields, removes
  AES zero padding, and shares a 131,072-operation work budget across one rule
  evaluation. Exhaustion rejects the packet; it cannot grant an allow,
  rewrite, or retry action.
  A message without `:` has no sender field but can match a text rule.
- `len=N|N+|N-M` (`l=`): Inclusive range of payload bytes, excluding route/path
  metadata. Ciphertext padding counts toward payload length.
- `route=flood|direct|any` (`rt=f|d|a`): Select route classes. Omitted radio
  rules retain flood-only behavior. Direct/any rows support drop, rate, and
  stop/allow; scope/region, retry, and TX timing require flood-only radio rules.
- `prob=1-100` (`pr=`): Apply the row to approximately this percentage of
  otherwise-matching packets. Omitted means `100`; a failed roll leaves later
  rows eligible. It is a sampling rule, not a forwarding quota.
- `enabled=on|off` (`e=1|0`): Keep a row while enabling or disabling it. To
  change only this state, use `set fr.<n> e=off` or `set fr.<n> e=on`; its
  conditions and actions stay saved.
- `in=any|none|scoped|allowed|unknown|scope:<name>|region:<name>`: Optional
  condition on the original incoming scope, before any rule rewrites it.
  `none` is an unscoped flood. `scope:name` is the exact public
  hashtag-derived scope. `region:name` is an exact allowed region match.
- `drop`: Explicit drop action. The `flood.rule` form requires an explicit
  action. For compatibility, a legacy `flood.filter` row with no rewrite,
  rate, or stop action is treated as drop.
- `scope=<name>`: Direct public-name scope rewrite. It derives a transport key
  from the name and does not require a configured region. For example,
  `scope=BlackHole86` is a regionless sink scope; `region=BlackHole86` would
  instead require a real configured, flood-allowed region with that name.
- `region=<name>`: Rewrite using an existing locally allowed region and one of
  that region's transport keys.
- `rate=N/min`: Per-node, per-row fixed one-minute forwarding limit. It can be
  the only action or accompany `scope=`/`region=`. Counters are charged only
  for packets that pass all forwarding gates.
- `retry`, `retry=on`, `retry=allow`, or `action=retry`: Allow a matching
  received flood packet to enter the configured flood-retry sequence. With no
  active `retry` rows, retry eligibility remains backward compatible and is
  controlled by the global retry settings. Once any active `retry` row exists,
  the matching rows become a retry allow-list: a received flood must match at
  least one surviving `retry` row. `flood.retry.bridge` still selects ordinary
  or bridge-bucket completion for the allowed packet. This action can accompany
  rewrite, rate, or stop, but not `drop`; compact syntax uses `f=r`.
- `priority=0-255`: Optional primary processing order. Higher values run first.
  At the same numeric priority, authenticated channel matches run before raw
  `hash:XX` matches, which run before `channel=*`; lower slot number breaks the
  remaining tie. The default is `0`; `pri=` is an alias. An explicitly higher
  numeric priority still overrides this automatic specificity ordering.
- `stop` or `action=stop`: Apply this matching row, then stop lower-order FPF7
  rows from processing. It can stand alone or accompany drop, rewrite, or
  rate. A stop-only row acts as an exception to lower-priority FPF7 rules.
  If the same row uses `region=` and that configured region is missing, denied,
  wildcard, or has no usable transport key, both the rewrite and its `stop`
  are inert so lower-order safety rows still run. A direct `scope=` target does
  not depend on region configuration.
  `allow`, `forward`, and `a` are aliases for `stop`; they do not override
  earlier drops, normal forwarding gates, or separate moderation rules.
- `suspend=tempradio`: Optional. Skip this row while either `tempradio` or
  `tempradio2` is active, including scheduled temporary sessions.
- `require=region`: Legacy alias for `in=allowed`. Apply the row only if the
  original incoming packet already passes this repeater's
  region gate. An incoming transport scope must resolve to a locally allowed
  region; an unscoped flood must be allowed by the wildcard region. The check
  occurs before any scope rewrite during this receive pass.
- `tx=slow`: Optional and valid with `scope=` or `region=`. Use an effective inbound
  `rxdelay` base of `max(2, configured rxdelay * 2)`, keep normal outbound
  queue priority, and retransmit with the maximum supported `txdelay` factor
  of `2.0`. Scope rows default to fast; `tx=fast` explicitly restores that
  default when replacing a slow row.
- `path=blacklist`: Optional unordered path condition. The persistent blacklist
  is repeater-only. It contains up to 255 unique 3-byte repeater IDs on ESP32
  builds and 18 on other builds, each written as six hexadecimal digits. A
  packet with 3-byte path hashes matches after one exact ID hit. A packet with
  2-byte path hashes matches after two path entries match the first two bytes
  of listed IDs. Packets with 1-byte path hashes never match this condition.
  Each received path entry is counted at most once.

The payload names follow the [MeshCore packet-format allocation](https://docs.meshcore.io/packet_format/):

| Value | Short name | Full name |
| --- | --- | --- |
| `0x00` | `req` | `PAYLOAD_TYPE_REQ` |
| `0x01` | `response` | `PAYLOAD_TYPE_RESPONSE` |
| `0x02` | `txt_msg` | `PAYLOAD_TYPE_TXT_MSG` |
| `0x03` | `ack` | `PAYLOAD_TYPE_ACK` |
| `0x04` | `advert` | `PAYLOAD_TYPE_ADVERT` |
| `0x05` | `grp_txt` | `PAYLOAD_TYPE_GRP_TXT` |
| `0x06` | `grp_data` | `PAYLOAD_TYPE_GRP_DATA` |
| `0x07` | `anon_req` | `PAYLOAD_TYPE_ANON_REQ` |
| `0x08` | `path` | `PAYLOAD_TYPE_PATH` |
| `0x09` | `trace` | `PAYLOAD_TYPE_TRACE` |
| `0x0A` | `multipart` | `PAYLOAD_TYPE_MULTIPART` |
| `0x0B` | `control` | `PAYLOAD_TYPE_CONTROL` |
| `0x0C` | `ota` | `PAYLOAD_TYPE_OTA` (this fork's LoRa OTA extension; reserved upstream) |
| `0x0D` | `13` | reserved |
| `0x0E` | `14` | reserved |
| `0x0F` | `raw_custom` | `PAYLOAD_TYPE_RAW_CUSTOM` |

**Route scope:** `mode=radio` defaults to the two flood route values:
`ROUTE_TYPE_TRANSPORT_FLOOD` (`0x00`, flood plus transport codes) and
`ROUTE_TYPE_FLOOD` (`0x01`, unscoped flood). Direct routes `0x02` and `0x03`
require an explicit `route=direct` or `route=any` on a radio row. Bridge/cross
rules cover both route classes by default at their transport boundaries.

**Behavior:** Match fields within one row are ANDed. Radio rows are matched
against the same immutable receive-time packet, before any rule changes its
scope. Bridge/cross rows match the packet at their admission boundary; a TX
packet can already have an appended hop or rewritten scope. Each mode evaluates
only its applicable rows, processed in descending `priority`. At equal numeric
priority, authenticated channel matches precede raw hashes, which precede an
unrestricted channel matcher; lower slot wins after that. The first matching
`stop` row is included and all lower-order FPF7 matches are discarded. A stop
cannot undo an earlier drop or
bypass hard forwarding gates or the other policy phases. A row with
`path=blacklist` must meet the path condition as well as its other conditions;
blacklist IDs can occur anywhere in the received path and their configured
order is irrelevant. In contrast, `prefix=` begins at the first received path
entry and preserves order. A matching drop row prevents retransmission. The
highest-order remaining matching scope/region row wins; matching drop and rate
rows remain independent and can still block the rewritten packet. Scope rewriting
happens before region enforcement and is trusted even
when its name is absent from the local region list. It does not bypass
`repeat`, `flood.max`, other drop rows, loop detection, or
moderation.

Retry selection uses that same ordered, stop-truncated match set. With at least
one active `retry` row, a received flood starts a retry sequence only when one
of those matching rows includes `retry`. This selector cannot override
`flood.retry.count`, path/type attempt caps, `flood.retry.advert`, disabled
forwarding, a drop decision, or any other hard forwarding gate. Locally
originated floods retain the normal global retry behavior.

With `require=region`, a failed check makes that scope row ineligible. It leaves
the packet unchanged and does not set the filter-scope trust bypass, so an
unknown or denied incoming region is rejected normally unless another
independent scope rule rewrites it. Later eligible filter scope rows may still
match.

By default, when a scope row will change the packet's transport codes, the
packet bypasses inbound `rxdelay`; its retransmission then uses zero `txdelay`
and the highest outbound queue priority. With `tx=slow`, the rewrite instead
uses an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, normal queue priority, and the maximum
`txdelay` factor of `2.0`. The actual randomized transmit wait ranges from zero
to ten packet airtimes. Selecting the scope already carried is a no-op and
does not grant special treatment. An active radio transmission is not
preempted, and CAD and airtime-budget limits still apply.

The packet is still received and can still be logged. Rules are persistent
data and can be changed over serial or authenticated remote CLI without an OTA
or reboot. `flood.rule` and `flood.filter` address the same table on extended
builds; FPF6 files are migrated in memory and the next save writes FPF7.
New sender/text, length, route, probability, enabled-state, or extended-path
fields use FPF8 instead. Old firmware without FPF8 support cannot load that
table; remove the new fields before downgrading and keep a copy of the rules.
FPF1-FPF5 files are rejected and filtering fails open. FPF7/FPF8 store canonical
region names rather than transient numeric region
IDs. Removing, reordering, or reusing a region ID therefore cannot silently
retarget a rule. If a saved input or target region name is absent, that input
match or rewrite is inert; restoring the same region name reactivates it.
While either `tempradio` or `tempradio2` is active, only rows explicitly marked
`suspend=tempradio` are skipped. Temporary radio is a radio state, not an OTA
mode; normal payload types can also use the temporary channel. Other rows
remain in force, and suspended rows resume after both temporary sessions end.
A malformed persisted table fails open (no general rules are applied).

Within one receive evaluation, rows using the same channel key share the
initial authentication verdict; content patterns also decrypt into temporary
scratch for matching. The cache and plaintext are discarded after the packet.
Raw `hash:XX` rows skip authentication and compare only the visible byte.

**Per-channel retry examples:**

```text
# Use bridge-bucket retry only for authenticated Public and #hamradio traffic.
set flood.retry.bridge on
set flood.rule.2 type=any channel=public retry
set flood.rule.3 type=any channel=#hamradio retry

# If the channel key/name is unavailable, select visible channel hash A7.
# This is an unauthenticated 1-byte hint, not a channel identity.
set flood.rule.4 type=any channel=hash:A7 retry
```

Because Public's visible hash is `0x11`, a bare `hash:11 drop` row also matches
Public. To exempt authenticated Public while dropping other packets that carry
the same visible byte, put the exact Public rule first and stop lower-priority
rules after it:

```text
# Retry and preserve authenticated Public; drop other channel-hash 11 packets.
set flood.rule.2 type=any channel=public retry stop
set flood.rule.3 type=any channel=hash:11 drop
```

The Public row matches only after its MAC/decrypt check succeeds. A colliding
channel therefore misses that `stop` and reaches the raw-hash drop row. Both
rows use the default numeric priority, but authenticated-channel specificity
automatically orders Public first even if its slot number is higher. Without
`stop`, both rows match Public and the sticky `drop` action wins. Omit `retry`
from the Public row when only the forwarding exemption is wanted. An operator
can deliberately reverse this order by assigning the hash row a higher numeric
`priority`.

Deleting or replacing the last active `retry` row restores the legacy global
retry eligibility. Firmware that predates the `retry`/`hash:XX` FPF7 extension
cannot preserve tables containing those rows; remove them before downgrading.

**Default rows:** Repeater firmware and FULL ESP32 room-server firmware seed a
new flood-filter table with
`ota all suspend=tempradio` in slot 1. This blocks repeated LoRa OTA (`0x0C`)
floods at every received hop unless either temporary radio profile is active. The OTA
core independently refuses OTA receive, relay, and transmit outside temporary
radio. The row is editable and deletable; once the table is saved, deletion is
persistent. Restore the exact seeded row with:

```text
set flood.filter.1 0x0C all suspend=tempradio
```

Omitting `all` is equivalent. Omit `.1` as well to reuse an identical rule or
the first empty slot instead of replacing slot 1.

Generalized repeaters also seed slot 2 with the authenticated `#wardriving`
radio hop limit and slot 3 with the bridge/crossover block:

```text
set flood.rule.2 type=any channel=#wardriving hops=5+ drop
set flood.rule.3 type=any channel=#wardriving hops=all mode=bridge,cross drop
```

These numbered commands replace those slots. Saved tables are preserved on
upgrade; use the unnumbered third command to add the new rule without replacing
an occupied slot. See [restoring the three defaults](flood_filtering.md#restore-the-factory-seeded-rows).

**Remote-admin lockout warning:** There are no hidden payload-type or short-hop
exceptions. FPF7 drop and rate rows may block `req`, `response`, `txt_msg`,
`anon_req`, `path`, ACK, and multipart traffic beginning at hop `0` when their
match fields say so. Transit repeaters cannot decrypt these outer types to
distinguish an admin exchange from ordinary peer traffic. Keep a serial or
other recovery path and stage broad deny/rate rules carefully.

Without `.n`, `set` reuses an identical rule or uses the first empty slot. With
`.n`, it replaces that slot, which is the intended way to change a row's match
or action.
`get flood.filter` or `get flood.rule` gives a compact list. Use the numbered
form for full details, including channel, prefix, original-scope condition,
action, timing, rate, and temporary-radio suspension.

`fr` is a shorter alias for `flood.rule`, with the same explicit-action
requirement. Short and long field names can be mixed in either command:

```text
set flood.rule.3 type=any channel=#wardriving hops=all mode=bridge,cross drop
set fr.3 any c=#wardriving m=bc d
get fr.3
```

The two setters above save the same row. `get fr.3` returns the complete
short setter; `get fr` lists the table. Unnumbered `set fr ...` reuses an
identical row or adds one to the first empty slot. `del fr.N` and `del fr all`
have the same behavior as their long forms.

Content, exceptions, and state changes use that same setter:

```text
set fr grp_txt c=#local x="^BEACON" d
set fr grp_txt c=public n="(?i)^(Alice|Bob)$" d
set fr grp_txt c=#local n="^Alice$" pri=200 a
set fr grp_txt c=#local d
set fr.4 e=off
set fr.4 e=on
```

The Alice rule stops lower-order general drops; separate moderation and hard
forwarding gates still apply. Sender names remain unverified display text.
Saved older prefix rules keep their start-anchored semantics; when retyping an
old `path=A1,B2`, use `prefix=A1,B2` or `pa=^A1>B2`.

| Full token | Short token |
| --- | --- |
| `type=...` | Positional type or `t=...`; `*` means `any` |
| `hops=...` | Positional range or `h=...`; `*` means `all` |
| `mode=radio/bridge/cross/bridge,cross` | `m=r/b/c/bc` (choose one value) |
| `channel=...`, `prefix=...` | `c=...`, `p=...` |
| `path=[^]ID>ID...[$]` | `pa=...` |
| `sender=...`, `text=...` | `n=...`, `x=...` |
| `len=N/N+/N-M` | `l=...` |
| `route=flood/direct/any` | `rt=f/d/a` |
| `prob=N`, `enabled=on/off` | `pr=N`, `e=1/0` |
| `hashbytes=any/1/2/2+/3` | `pb=*/1/2/2+/3` (choose one value) |
| `path=blacklist` | `p=bl` (repeater only) |
| `in=any/none/scoped/allowed/unknown` | `i=*/n/s/a/u` (choose one value) |
| `in=scope:name`, `in=region:name` | `i=s:name`, `i=r:name` |
| `drop`, `stop`, `retry` | `d`, `s`, `r` |
| `allow`, `forward` | `a` (same action as `stop`) |
| `scope=name`, `region=name` | `s=name`, `r=name` |
| `rate=N/min`, `priority=N` | `q=N`, `pri=N` |
| `tx=slow`, `suspend=tempradio`, `retry` | `f=s`, `f=t`, `f=r`; combine as `f=str` |

All existing validation and permissions apply. `m=cb`, `m=b,c`, and `m=c,b`
also mean bridge and crossover.

| Full family | Short family | Commands |
| --- | --- | --- |
| `flood.moderation` | `fm` | `get`, `set`, `del` |
| `flood.channel.scope` | `fs` | `get`, `set`, `del` |
| `flood.channel.scope.require` | `fsr` | `get`, `set`, `del` |
| `flood.filter.blacklist` | `fb` | `get`, `set`, `del` |
| `flood.advert` | `fa` | `get`, `clear` |

Slot arguments, output, validation, and permissions are shared with the full
family names. For example, `get fa key 1` inspects the full advert key and
`clear fa all` clears advert history.

The numbered long getters use this complete short setter when descriptive
fields would exceed a reply. Defaults are omitted and specific payload types
are printed numerically. If it still cannot fit, the reply is
`Err - compact command exceeds reply size`, never a partial setter.
Private channel keys are shown as `key:XXXXXXXX`; that reference can be
reused while an active rule on the same device holds the key. Copying it to
another device requires the original key, unless that device already has a
matching rule. Unknown or ambiguous references are rejected. See the
[shorthand guide](flood_filtering.md#shorthand-for-copying-rules) for examples.

On generalized repeaters, filter rows, scope-rewrite rows, the shared
blacklist, and `flood.channel.data` compatibility state are committed in one
atomic FPF7/FPF8 image. Compact FPF6 profiles retain separate files. Replacing or deleting
the blacklist does not delete rows containing `path=blacklist`; such rows
remain dormant while the list is empty. Path hashes are truncated routing
identifiers, not authenticated identities, so this is a forwarding signal
rather than proof that a particular repeater handled a packet.

A common use is containment of bulk internet-to-mesh dumping: list the path
IDs associated with the offending gateways, then add a broad
`type=any hops=all path=blacklist drop` row. This prevents this repeater from
retransmitting matching floods; it does not delete them from local logs or
prove who originated them.

The unnumbered blacklist `set` replaces the whole list and accepts up to 18 IDs
so it fits every CLI transport. Numbered `set` writes up to 18 consecutive
entries beginning at an existing slot or exactly the next slot, allowing an
ESP32 list to grow to 255 entries in batches. Numbered deletion compacts
subsequent slots. The unnumbered `get` reports the total and as many leading
IDs as fit in one reply; use numbered `get` to inspect entries beyond that
reply.

Standard traceroute is direct-routed and therefore outside `flood.filter`
entirely. A custom flood-form trace participates normally: `type=any`, explicit
`trace`, rewrite, rate, drop, and stop rows can all apply.

**Examples:**
```text
set flood.rule.2 type=grp_data hops=4+ channel=#rgdata in=none scope=BlackHole86
set flood.rule.3 type=grp_data channel=#rgdata in=scope:usa scope=BlackHole86
set flood.rule.4 type=any prefix=860C rate=10/min
set flood.rule.5 type=grp_data hops=0-2 channel=#rgdata priority=200 stop
get flood.rule.2
set flood.filter grp_data 4+
set flood.filter.2 PAYLOAD_TYPE_ADVERT 6+
set flood.filter ota 2-4
set flood.filter.1 0x0C all suspend=tempradio
set flood.filter grp_data all suspend=tempradio
set flood.filter grp_txt all scope=local
set flood.filter grp_data all scope=local require=region
set flood.filter grp_data all path=blacklist scope=local tx=slow
set flood.filter.blacklist A1B2C3,D4E5F6,112233
set flood.filter.blacklist.4 445566
set flood.filter.blacklist.19 778899,AABBCC,DDEEFF
set flood.filter any all path=blacklist
get flood.filter.blacklist
get flood.filter.blacklist.4
set flood.filter any 12+
get flood.filter
get flood.filter.2
del flood.filter.2
```

The first rule authenticates `#rgdata`, requires more than three received hops,
and adds `#BlackHole86` only when no scope was present. The second rewrites the
exact incoming `#usa` scope. The third demonstrates a two-byte pbyte source
prefix and a global per-row rate cap. The fourth authenticates `#rgdata` at
zero through two hops, applies no FPF7 action of its own, and stops lower-order
FPF7 forward rows; hard gates and the rewrite/moderation phases still apply.

The fixed 240 KB STM32WL profiles leave
`MESH_ENABLE_FLOOD_RULE_ENGINE=0` and retain the compact, persistent FPF6
`flood.filter` and blacklist commands. They still filter floods, but do not
expose the `flood.rule` alias or its extended channel, prefix, input-scope,
region-action, or rate fields, nor sender/text patterns, length, route,
probability, enabled-state, or anchored chains. Their compiled legacy
families still accept the `fm`/`fs`/`fsr`/`fb`/`fa` aliases; `fm` requires
moderation support in that build. No partition size changes are required.

---

#### Moderate flood group text by channel, sender, and source path

**Usage:**
- `get flood.moderation`
- `get flood.moderation.<n>`
- `set flood.moderation <channel> <sender> <action> [action...]`
- `set flood.moderation.<n> <channel> <sender> <action> [action...]`
- `del flood.moderation.<n>`
- `del flood.moderation all`

`fm` is the short family name. Options can use `d` (`drop`), `q=N`
(`rate=N/min`), `h=N` (`hops=N`), and `p=...` (`path=...`). The short rate
also accepts `q=N/min` or `q=N/m`; duplicate rate/drop options remain invalid.

**Parameters:**
- `n`: Moderation slot from `1` to `16`.
- `channel`:
  - `public`: Built-in Public channel.
  - `#channel`: Derive the well-known hashtag-channel key.
  - A 128-bit or 256-bit channel key in hex, for any other/private channel.
- `sender`: Exact group-text display name. Matching is ASCII case-insensitive.
  Quote names containing spaces, for example `"Field User"`.
- `drop`: Do not forward matching messages. Equivalent to `rate=0/min`.
- `rate=X/min`: Forward at most `X` matching messages per 60-second local
  window. This option requires an exact sender rather than `*`.
- `hops=N`: Do not forward a matching message whose received flood path count
  is `N` or higher. `hops=all` removes this constraint.
- `path=H1[,H2,H3]`: Match the start of the flood path. One to three hashes are
  accepted; every hash must have the same 1-, 2-, or 3-byte width.
- `path=*`: Match any source path (the default).

At least one of `drop`, `rate=X/min`, or `hops=N` is required. Rate and hop
limits can be combined. Rate counters are local to this repeater and rule, use a
60-second window beginning with the first matching message, and reset on reboot.

Configured exact names are limited to 31 bytes. A wildcard `*` with a hop
restriction also matches longer valid sender names. The FGM1 storage format
is unchanged; saves use staging, readback, and backup recovery. A save reported
as unconfirmed may already have published, so rebooted settings can differ
from the restored live table.

**Decode and identity behavior:** Moderation applies only to flood
`PAYLOAD_TYPE_GRP_TXT`. The repeater first checks the packet's channel-hash byte,
then validates and decrypts with the configured key. It extracts the text before
the first `:` from the standard `<sender>: <message>` plaintext. The channel key
is stored locally but is never printed by `get`.

The group-text sender is an **unverified display name**, not a public key. It can
be spoofed. Combining it with the first one to three path hashes makes a more
useful moderation signal, but path hashes are truncated and are not proof of the
originating user. A path-qualified rule begins matching only after the packet
contains all configured starting hops; it cannot identify a first hop on a
zero-hop packet.

As with general filtering, matching messages are still received/logged; only
retransmission is denied. There are no moderation rules by default.

**Examples:**
```text
set flood.moderation public "Noisy User" rate=5/min
set flood.moderation #local bot drop path=A1B2C3,D4E5F6
set flood.moderation.3 00112233445566778899AABBCCDDEEFF alice rate=10/min hops=4 path=71CE82
set fm public "Noisy User" q=5
get flood.moderation
get flood.moderation.3
del flood.moderation.3
```

---

### ACL

#### Add, update or remove permissions for a companion
**Usage:** 
- `setperm <pubkey> <permissions>`

**Parameters:**
- `pubkey`: Companion public key
- `permissions`: 
  - `0`: Guest
  - `1`: Read-only
  - `2`: Read-write
  - `3`: Admin
  - `4`: Region/scope manager (repeater delegated region and forced-scope management)
  - `5`: Filter manager (repeater delegated forwarding-filter management)

**Filter manager scope:** Permission `5` can use an explicit allowlist of
non-secret operational/filter status commands and can change the forwarding
controls `repeat`, `loop.detect`, `flood.max*`, `flood.channel.data*`,
`flood.filter*`, `flood.rule*`, `fr[.N]`, and
`flood.moderation*`, including the `fm`, `fb`, and `fa` aliases. It cannot read
guest, WiFi, MQTT, bridge, or other credentials, and it cannot change regions,
ACL entries, radio settings, or other admin configuration. Permission `4`
is limited to region commands, `flood.channel.scope*` (including `fs` and
`fsr`), and the same non-secret
status allowlist.
Both delegated manager roles are protected from least-recently-active ACL
eviction like administrators.

The same filter aliases work inside enrolled
[signed fleet commands](fleet_management.md), including quoted sender/text
patterns. Fleet signature, expiry, replay, and command-size checks still apply;
ordinary channel text cannot authorize a change.

**Note:** Removes the entry when `permissions` is omitted

---

#### View the current ACL

**Usage:**

- `get acl` - full listing locally; first page over LoRa
- `get acl <page>` - a numbered page, starting at 1, locally or over LoRa

Available on repeaters, room servers, and sensors. Over LoRa, an authenticated
admin session is required; guests, read-only/read-write clients, and delegated
region/filter managers cannot read the ACL.

Each page starts with `ACL <page>/<pages>` and contains up to two entries.
Each entry is a two-digit hexadecimal permission byte followed by the full
64-character public key. For example, use `get acl 2` for the second page.
An empty list returns `ACL: empty`. Deleted/guest entries with permission byte
`00` are omitted, matching the local listing. Pages reflect the current live
ACL, so entries can move between pages if permissions or clients change.

Bare `get acl` keeps the full streamed listing on USB and direct network CLI,
or via Companion binary command `0x42` where the role supports the command.
The listing contains public keys and permissions, not passwords or shared
secrets, and does not change the ACL.

---

#### View repeater guest sessions

**Usage:**

- `get guests` - first page
- `get guests <page>` - a numbered page, starting at 1

Available on repeaters through local administrative consoles or an authenticated
remote admin session. Delegated region/filter managers and guest clients cannot
read this list.

Each page starts with `Guests <page>/<pages> (<total>)` and shows one guest's full
public key followed by `last=<UTC epoch seconds>`. An empty list returns
`Guests: empty`. A `last=0` value can occur before the node's clock is set.

These are live RAM entries, separate from the persistent ACL shown by `get acl`.
They indicate an accepted session, not whether the guest is currently online.
Entries can move between pages as clients change. A reboot or `set guest off`
clears them.

---

#### View repeater login attempts

**Usage:**

- `get logins` - first page
- `get logins <page>` - a numbered page, starting at 1

Available on repeaters through local administrative consoles, an authenticated
remote admin session, or enrolled [signed fleet commands](fleet_management.md).
Guests and delegated region/filter managers cannot read this list.

The repeater keeps the 16 most recently attempted public keys in RAM, with the
newest attempt first. Each row shows only the first **12 hexadecimal characters**
of the key, the latest attempt's UTC epoch time, its result, and an attempt count.
There are up to two rows per page. Attempts for the same full key update its
existing row and count; the oldest key is replaced when the list is full.
The count stops at 4,294,967,295. Two different keys can share
the displayed prefix; their records and counts remain separate.

```text
Logins 1/1 (2)
ABCDEF123456 n=3 last=1792600000 accepted
123456ABCDEF n=1 last=1792599900 guests-disabled
```

Results are `accepted`, `wrong-password`, `guests-disabled`, `replay/state`, and
`acl-unavailable`. An accepted login can be an admin, an assigned ACL client,
or a guest. This history records
actual login requests after packet authentication; anonymous owner, region, and
clock requests, incomplete requests, and packets that fail authentication do not
appear. Passwords and shared secrets are never recorded.

This is recent attempt history, not a list of online users or a complete audit
log. Pages can change as attempts arrive. A reboot clears it; `set guest off`
does not. A zero timestamp means the clock was not set when the attempt arrived.

---

#### Recover a repeater's future-dated replay timestamp

This is an explicit recovery operation after correcting a bad clock, not a
contact deletion. It lowers selected replay timestamps to the repeater's
current UTC epoch only when they are later than that epoch. Earlier values,
public keys, permissions, stored paths, and historical identity records remain.
The replay file is committed before the live table changes. No extra 60-second
login reservation is added by this command.

First set/synchronize and verify the repeater's clock (`clock`); a build-default
clock without a manual or observed synchronization is not accepted. Also fix
the companion's clock before its next login.

USB console:

```text
replay reset <full-64-hex-public-key>
replay reset all CONFIRM
```

Authenticated LoRa admin, including a resumed admin session:

```text
replay reset <full-64-hex-public-key>
```

The reply shows `now=<epoch>`, `ttl=<remaining-seconds>s`, and a confirmation command containing the same
full key and a one-use 32-hex-character token. Verify the displayed time and send
that command before the original 300-second deadline. During the first 120
seconds, repeated requests for the same key by the same admin return the same
token without restarting either timer. From 120 through 300 seconds, the token
is retained for confirmation only: requests do not resend or replace it. This
leaves at least 180 seconds to deliver a confirmation after the last permitted
token response is generated (radio transit time still counts toward expiry).
At 300 seconds it expires and a new request can receive a new token. Confirmation
can succeed immediately; there is no requirement to wait for the resend window
to close. The displayed TTL decreases on retries and is measured when the reply
is generated, not when it reaches the companion.

It can target the caller's own key or another
exact key; prefixes, `self`, wildcards, and `all` are not allowed over LoRa.
Tokens are bound to both the requesting admin and target, expire on reboot,
and are invalid after use or a clock correction outside the five-second
confirmation tolerance. A failed write requires a new confirmation. Challenge
requests consult live token state instead of replaying cached challenge text;
completed confirmation results remain cacheable without executing the reset
again. Normal packet freshness checks still apply to retries.

Only the physical serial console grants `all` access. Ethernet, browser and
internal command callbacks do not count as USB. Guest, read-only, region-manager
and filter-manager roles cannot reset replay state. This command is implemented
in repeater firmware; room-server and sensor CLI are unchanged.

Normal login and command admission checks remain in force: a fully locked-out
caller that cannot send an accepted admin command needs another working admin
or USB access. A corrupt/unreadable replay file fails closed and is not erased
or formatted by this operation. Unknown keys do not create records.

Security trade-off: lowering a replay boundary can admit previously captured
future-dated login/command packets above the new boundary, including packets
that could raise it again. The one-use token prevents the recovery command
itself from being repeatedly executed; it does not replace the protocol's
timestamp-based replay protection. Use recovery only after verifying clocks.
Setting a clock alone never automatically resets this table.

---

#### View or change this room server's 'read-only' flag
**Usage:**
- `get allow.read.only`
- `set allow.read.only <state>`

**Parameters:**
- `state`: `on` (enable) or `off` (disable)

**Default:** `off`

**Make a room read-only while allowing selected writers:**

```text
set allow.read.only on
setperm <PUBKEY_USER_1> 2
```

With read-only mode on, a client without a valid room password can join as a
read-only guest, including with a blank password. Empty configured admin and
room passwords disable those password credentials; they never grant Admin or
Read-Write access. Permission `2` gives the specified public key read-write
access. Replace `<PUBKEY_USER_1>` with the user's full public key and repeat
`setperm` for every approved writer.

An existing ACL identity keeps its assigned role when logging in with a blank,
stale, or room password. A valid nonempty admin password can explicitly upgrade
it to Admin. This prevents a guest-password login from demoting an admin, and
prevents a room password from overriding an explicit read-only assignment.
Only Read-Write (`2`) and Admin (`3`) clients can post.

This does not remove write access from clients that already know the room
password. Keep that password private, or replace it, if write access should be
limited to the explicitly permitted users.

---

#### View or change the room topic

**Availability:** Room Server firmware. Local consoles and authenticated remote
admins can use these commands.

```text
set topic Welcome to the Bellevue room. Please keep posts local.
get topic
set topic
```

`set topic <text>` saves an announcement of up to 151 UTF-8 bytes. Individual
command transports can impose a smaller input limit. `get topic` returns the
current text prefixed with `> `. `set topic` with no text clears it.

The topic survives reboot and appears as a normal room post authored by the
server, using the existing app message format. It does not require an app
update or provide a separate pinned-header display. Older unread retained posts
are delivered before the topic, so the companion's automatic catch-up cursor
cannot jump past them. A topic ACK updates only topic delivery state, leaving
the server's message-history cursor unchanged.

The server waits six seconds before delivering a new topic and attempts an
unacknowledged announcement up to three times per client. It then continues
ordinary post delivery; reconnecting or changing the topic renews that budget.
Changing a topic while its predecessor is awaiting an ACK does not mark the
replacement as received. Clearing the topic stops further announcement pushes;
a packet already queued for transmission can still arrive.

Topic saves are verified before publication. A failed save reports an error
and retains the previous live topic. Corrupt, unreadable, or unsupported stored
topic files are preserved and block replacement until their storage issue is
repaired. Ordinary room history uses the bounded RAM ring by default. Enable
optional retention of the newest 32 posts with `set room.history on`.

See [Room history, moderation, and information board](room_services.md) for
persistent history, saved ACL roles, full-key bans, per-user post/request
limits, the ESP32 `/room` browser page, and versioned article downloads.

---

### Region Management (v1.10.+)

#### Bulk-load region lists
**Usage:** 
- `region load`
- `region load <name> [flood_flag]`

**Parameters:**
- `name`: A name of a region. `*` represents the wildcard region

**Note:** `flood_flag`: Optional `F` to allow flooding

**Note:** Indentation creates parent-child relationships (max 8 levels)

**Note:** `region load` with an empty name will not work remotely (it's interactive)

---

#### Save any changes to regions made since reboot
**Usage:** 
- `region save`

---

#### Allow a region
**Usage:** 
- `region allowf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Note:** Setting on wildcard `*` allows packets without region transport codes

---

#### Block a region
**Usage:** 
- `region denyf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Note:** Setting on wildcard `*` drops packets without region transport codes

---

#### Show information for a region
**Usage:** 
- `region get <name>`

**Parameters:**
- `name`: Region name (or `*` for wildcard)

---

#### View or change the home region for this node
**Usage:** 
- `region home`
- `region home <name>`

**Parameters:**
- `name`: Region name

---

#### View or change the default scope region for this node
**Usage:** 
- `region default`
- `region default {name|<null>}`

**Parameters:**
- `name`: Region name,  or <null> to reset/clear

---

#### Create a new region
**Usage:** 
- `region put <name> [parent_name]`

**Parameters:**
- `name`: Region name
- `parent_name`: Parent region name (optional, defaults to wildcard)

**Note:** In firmware **v1.15.0** and later, `region put` enables flooding for that region by default (you do not need a separate `region allowf <name>` after each `put`). On **v1.14.0** and earlier, new regions may still require `region allowf` for flooding-see [`region allowf`](#allow-a-region).

---

#### Define region hierarchy (single line)
**Usage:**
- `region def <token> [<token> ...]`

**Parameters (tokens):** Space-separated. A logical **cursor** starts at the wildcard `*`.

- **`name`** - Create `name` as a child of the current cursor (equivalent to `region put name` with the cursor as parent). Cursor moves to `name`.
- **`name|jump`** *(or `name,jump`)* - Create `name` as a child of the current cursor, then move the cursor to `jump` (must already exist on the node, or have been created earlier in this command). `jump` is **not** the parent of `name`; use this form to pop back up and start another branch.

**Behavior:** Each created region defaults to flood-allowed (same as `region put`). The reply is the resulting region tree (same format as bare `region`); review it before running `region save` to persist. The command is transactional: invalid names, unknown or ambiguous jumps, table overflow, and hierarchy cycles return `Err - ...` without changing the existing tree.

**Existing regions:** `region def` does not clear the existing tree - if a name already exists, its parent is updated to the current cursor; otherwise a new region is created. To start from scratch, `region remove` the unwanted regions first.

**Limits:** Repeater serial accepts one line up to **160 characters**. For larger trees, split across multiple `region def` commands; the cursor resets to `*` between commands, so lead the next command with `child|ancestor` to reposition. Each token splits at most once on `|` - `region def a|b|c|d` is not a flat-list shorthand; see the flat-list example below.

**Example - linear chain** (each token becomes a child of the previous):
```
region def a b c d e
region save
```

**Example - branched tree** (equivalent to `region put a`, `region put b a`, `region put c b`, `region put d c`, `region put e b`, `region put f e`):
```
region def a b c d|b e f
region save
```

**Example - transactional error:**
```
region def a b c|nope d
```
The reply is `Err - unknown or ambiguous jump: nope`. The existing tree is unchanged; re-run with a corrected jump.

**Example - flat list** (each region a child of `*`). Use `|*` after each token to pop the cursor back to the root before the next token:
```
region def a|* b|* c|* d|* e|* f
region save
```

---

#### Remove a region
**Usage:** 
- `region remove <name>`

**Parameters:**
- `name`: Region name

**Note:** Must remove all child regions before the region can be removed 

---

#### View all regions
**Usage:** 
- `region list <filter>`

**Serial Only:** Yes

**Parameters:**
- `filter`: `allowed`|`denied`

**Note:** Requires firmware 1.12+

---

#### Dump all defined regions and flood permissions
**Usage:** 
- `region`

**Serial Only:** For firmware older than 1.12.0

---

### Region Examples

**Example 1: Using F Flag with Named Public Region**
```
region load
#Europe F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a region named `#Europe` with flooding enabled
- Packets from this region will be flooded to other nodes

---

**Example 2: Using Wildcard with F Flag**
```
region load 
* F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a wildcard region `*` with flooding enabled
- Enables flooding for all regions automatically
- Applies only to packets without transport codes

---

**Example 3: Using Wildcard Without F Flag**
```
region load 
*
<blank line to end region load>
region save
```
**Explanation:**
- Creates a wildcard region `*` without flooding
- This region exists but doesn't affect packet distribution
- Used as a default/empty region

---

**Example 4: Nested Public Region with F Flag**
```
region load 
#Europe F
  #UK
    #London
    #Manchester
  #France
    #Paris
    #Lyon
<blank line to end region load>
region save
```

**Explanation:**
- Creates `#Europe` region with flooding enabled
- Adds nested child regions (`#UK`, `#France`)
- All nested regions inherit the flooding flag from parent

---

**Example 5: Wildcard with Nested Public Regions**
```
region load 
* F
  #NorthAmerica
    #USA
      #NewYork
      #California
    #Canada
      #Ontario
      #Quebec
<blank line to end region load>
region save
```

**Explanation:**
- Creates wildcard region `*` with flooding enabled
- Adds nested `#NorthAmerica` hierarchy
- Enables flooding for all child regions automatically
- Useful for global networks with specific regional rules

---
### Direct Retry

Direct retry resends direct-routed packets when the downstream echo is not heard.
It applies to direct messages, ACK packets, multipart packets carrying ACK
payloads, TRACE packets, and recognized RAW_CUSTOM telemetry (`TTB1`, `TVB1`,
`IVB1`, `N1B1`, `N2B1`) with a non-empty hop path.

The shared state, count, base, and step controls work on repeater, room-server,
and sensor firmware. Recent-repeater/SNR controls are repeater-only because the
other roles do not keep the repeater reachability table they require.

#### View or change direct retry state

**Search terms:** enable tx retries, disable tx retries, stop direct retries, turn off retransmissions.

**Usage:**
- `get direct.retry`
- `set direct.retry <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Notes:**
- New installs and older preference files without direct retry settings default to `on` with the `rooftop` preset.

**Examples:**
```
get direct.retry
set direct.retry on
set direct.retry off
```

---

#### View or change direct retry heard-table gate
**Usage:**
- `get direct.retry.heard`
- `set direct.retry.heard <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Note:** This command is repeater-only. When enabled, the recent repeater table is the direct retry eligibility
gate. Prefixes missing from the table are assumed reachable; prefixes in the
table below the active SNR gate are blocked.

**Examples:**
```
get direct.retry.heard
set direct.retry.heard on
set direct.retry.heard off
```

---

#### View or apply a retry preset

**Search terms:** retry profile, retry defaults, rooftop retries, mobile retries, infrastructure retries.

**Usage:**
- `get retry.preset`
- `set retry.preset <preset>`

**Parameters:**
- `preset`: `infra`|`rooftop`|`mobile`

**Notes:**
- Applies shared direct retry and flood retry defaults.
- `infra`: fewer, slower retries for stable fixed infrastructure.
- `rooftop`: default long retry window for weak rooftop links.
- `mobile`: long retry count with shorter spacing for moving or changing links; flood retry count is `15`.
- Changing `direct.retry.count`, `direct.retry.base`, `direct.retry.step`, `direct.retry.margin`, `flood.retry.count`, `flood.retry.path`, or `flood.retry.group.path` makes the preset report as `custom`.

**Examples:**
```
get retry.preset
set retry.preset infra
set retry.preset rooftop
set retry.preset mobile
```

---

### Flood Retry

Flood retry resends flood-routed packets when the same packet is not heard from
another qualifying repeater.

The count, path, group-data path, and advert controls work on repeater,
room-server, sensor, and Companion firmware. Infrastructure forwarding must
also be enabled for retries to run. Prefix, ignore, bridge, and bucket controls
are repeater-only.

#### Hop-zero flood retry coding rate

Flood packets with no path hops recorded yet use the same CR escalation ladder
as direct retries. The starting point is the active radio CR, since a flood has
no single next-hop repeater whose SNR can select a starting CR. Each retry uses
its attempt number and the active radio setting; it does not restart the ladder
from the previous retry's override. Forwarded packets with one or more recorded
hops use the active radio CR on every retry, including bridge retries whose
effective path length discounts ignored hops.

The initial transmission uses the active radio CR. This table lists retries
after that initial transmission; any later retries remain at CR8:

| Starting CR | Retry 1 | Retry 2 | Retry 3 | Retry 4 | Retry 5+ |
| --- | --- | --- | --- | --- | --- |
| CR4 | CR4 | CR5 | CR7 | CR7 | CR8 |
| CR5 | CR5 | CR7 | CR7 | CR8 | CR8 |
| CR6 | CR6 | CR7 | CR7 | CR8 | CR8 |
| CR7 | CR7 | CR7 | CR8 | CR8 | CR8 |
| CR8 | CR8 | CR8 | CR8 | CR8 | CR8 |

The ladder is shared across presets. Presets change the retry budget, not the
CR steps. At CR5, the maximum hop-zero schedules are:

| Preset | Configured flood count | Hop-zero retries | CR schedule after the initial send |
| --- | ---: | ---: | --- |
| `infra` | 1 | 2 | CR5, CR7 |
| `rooftop` | 3 | 6 | CR5, CR7, CR7, CR8, CR8, CR8 |
| `mobile` | 15 | 15 | CR5, CR7, CR7, then CR8 for retries 4-15 |

These budgets apply to group text and the origin login/message family
(`RESPONSE`, `TXT_MSG`, `ANON_REQ`, `PATH`). Existing payload caps, echo
cancellation, and retry eligibility still apply: `REQ` and OTA do not use
generic flood retries; other payloads allow at most one retry. An eligible
advert at CR5 therefore gets only its first CR5 retry.

`set flood.retry.count 0` continues to disable all automatic flood retries,
including hop zero. It leaves the initial transmission and all direct-retry
settings and CR schedules unchanged. Direct retry's `direct.retry.cr` switch
and SNR thresholds continue to control direct retries only. Per-packet CR
overrides are restored after transmission and do not change the saved radio
setting or physical preamble.

#### View or change flood retry count

Companion firmware supports `flood.retry.count`, `flood.retry.path`,
`flood.retry.group.path`, and `flood.retry.advert` through its text terminal and
framed CLI. These settings persist across reboot. Companion retains its
pre-existing defaults when upgrading an older preferences file: count `15`,
general path gate `1`, group path gate `off`, and self-origin advert retries
`on`. Companion uses the configured count directly, without the infrastructure
role's hop-zero and one-hop multipliers. The shared payload caps below still
apply. Companion only retries its **own** adverts; neighbor adverts remain
ineligible even with `flood.retry.advert on`.

The `infra`, `rooftop`, and `mobile` defaults and `retry.preset` command in this
section apply to infrastructure roles. Companion does not expose repeater
bridge or prefix retry filters.

On builds with [Meshtext channel chat](radio_profiles.md#heard-repeat-retries),
`flood.retry.count` also limits retries of the node's own MT channel text,
with a cap of 15 total attempts (the original plus 14 retries).
Retries reuse the original encoded packet and stop after hearing a matching
forwarded copy with a lower remaining hop limit. This is not a recipient ACK
or a delivery guarantee. NodeInfo permits at most one additional transmission,
only with nonzero retry count and `flood.retry.advert on`, and waits an extra
minute beyond its airtime-based delay. These settings never enable relaying of
received MT traffic or apply MeshCore prefix/bridge rules to MT packets.
`set mt.hops 0` disables heard-repeat retries, without suppressing the original
chat or its first-use NodeInfo. `get mt.retry` reports retry/heard/exhausted
counters, pending work and the configured count without changing settings.
Scheduled beacons always send once per occurrence.

**Search terms:** flood tx retries, flood retry attempts, flood retransmissions, broadcast retries.

**Usage:**
- `get flood.retry.count`
- `set flood.retry.count <count>`

**Parameters:**
- `count`: Base retry attempts after the original send, from `0` to `15`. `0` disables flood retry.

**Note:** Infrastructure roles first calculate their retry count: path count 0 uses `count * 2`, path count 1 uses `count * 1.5` rounded up, and path count 2 and higher uses the configured base count, with a hard cap of `15`. Companion uses `count` directly. A shared payload policy then applies to every build: `REQ` never retries; `GRP_TXT` keeps the role-calculated count; remote-login-critical `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH` packets keep up to `15` at the originating node (path count 0) and cap at `2` after entering the path; all other flood payload types cap at `1`. These caps never raise a lower role-calculated count. Setting `count` to `0` immediately removes queued and future flood retries; a packet already transmitting is allowed to finish.

Forwarded neighbor adverts have an additional loop guard independent of the advert retry setting. After this node completes an advert transmission and hears a downstream copy with a longer path, it does not forward that same advert again while the advert's signed timestamp is less than six hours old. Self-originated adverts, adverts without a heard echo, and adverts six hours old or older are unaffected.

An enabled self-originated advert retry waits at least one additional minute beyond the normal airtime-aware retry delay. Once a newer self advert has successfully entered the outbound queue, its retry sequence replaces queued or future retries for older self adverts; an older advert already transmitting is allowed to finish. Companion firmware permits this single slow retry for its own adverts while continuing to block retry attempts for neighbor adverts it forwards.

**Defaults:**
- `infra`: `1`
- `rooftop`: `3`
- `mobile`: `15`

**Examples:**
```
get flood.retry.count
set flood.retry.count 0
set flood.retry.count 15
```

---

#### View or change flood retry path gate
**Usage:**
- `get flood.retry.path`
- `set flood.retry.path <count|off>`

**Parameters:**
- `count`: Maximum flood path hash count eligible for retry, from `0` to `63`.
- `off`: Disable the path-length gate.

**Defaults:**
- `infra`: `1`
- `rooftop`: `2`
- `mobile`: `1`

**Examples:**
```
get flood.retry.path
set flood.retry.path 1
set flood.retry.path off
```

---

#### View or change the group-data flood retry path gate
**Usage:**
- `get flood.retry.group.path`
- `set flood.retry.group.path <count|off>`

**Parameters:**
- `count`: Maximum flood path hash count eligible for retry for group data packets (`PAYLOAD_TYPE_GRP_DATA`/type 6), from `0` to `63`.
- `off`: Disable only the group-data-specific gate. The general `flood.retry.path` gate still applies.

**Default:** `1` for `infra`, `rooftop`, and `mobile` presets.

**Note:** The stricter of `flood.retry.path` and `flood.retry.group.path` is used. A value of `1` allows retry sequences at path counts `0` and `1`; group data at path count `2` or higher is still forwarded normally but does not start a flood retry sequence. A value of `0` allows retries only at the originating sender.

On infrastructure roles, setting `flood.retry.path` to `0` also sets `flood.retry.group.path` to `off` because the general zero-hop gate is already stricter. While the general gate remains `0`, attempts to set the group-data gate keep it `off`. Applying a named retry preset restores the group-data default of `1`. Companion retains the saved group gate, but the stricter general gate still wins.

**Examples:**
```
get flood.retry.group.path
set flood.retry.group.path 1
set flood.retry.group.path off
```

---

#### View or change flood retry advert handling
**Usage:**
- `get flood.retry.advert`
- `set flood.retry.advert <on|off>`

**Parameters:**
- `on`: Retry node advert floods.
- `off`: Do not retry node advert floods.

**Default:** `off`

**Examples:**
```
get flood.retry.advert
set flood.retry.advert off
```

---

#### View or change flood retry target prefixes
**Usage:**
- `get flood.retry.prefixes`
- `set flood.retry.prefixes <prefixes|none|off>`

**Parameters:**
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 8 entries.
- `none` or `off`: Clear the list.

**Note:** When set, non-bridge flood retry only accepts same-packet echoes whose
last hop matches one of these prefixes. When unset, any non-ignored last hop can
cancel the retry.

**Examples:**
```
get flood.retry.prefixes
set flood.retry.prefixes A58296,860CCA,425E5C
set flood.retry.prefixes none
```

---

#### View or change flood retry ignored prefixes
**Usage:**
- `get flood.retry.ignore`
- `set flood.retry.ignore <prefixes|none|off>`

**Parameters:**
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 8 entries.
- `none` or `off`: Clear the list.

**Note:** Non-bridge flood retry does not cancel on same-packet echoes whose
last hop matches this list. Bridge mode also excludes these prefixes from bucket
and `other` hits.

**Examples:**
```
get flood.retry.ignore
set flood.retry.ignore 71CE82,C7618C
set flood.retry.ignore none
```

---

#### View or change flood retry bridge mode
**Usage:**
- `get flood.retry.bridge`
- `set flood.retry.bridge <on|off>`

**Note:** Bridge mode retries until each configured fresh bucket, plus the non-source `other` bucket, has been heard or the retry count is exhausted. If prefixes in different buckets share their first byte, configuration commands return a warning because a 1-byte path cannot distinguish those buckets. The configuration remains valid: an ambiguous source is treated as belonging to every matching source bucket, and an ambiguous echo credits every matching target bucket so it cannot force retry exhaustion.

Flood retry timing retains its fixed maximum-frame plus 20 packet-airtime wait, then adds a random `0-200%` of one additional packet airtime on every attempt. This de-synchronizes repeaters that may have missed the same echo while capping the added wait at two frames.

Only one active retry sequence is kept for a given logical flood packet. An identical flood can still transmit normally, but it does not create a second sequence of extra attempts. Retry state is released if a queued packet is evicted, and the final echo window retains metadata without reserving a packet-pool entry.

Bridge reachability learned from earlier hops in a successful echo is cached separately from `recent.repeater`. Only the final RF hop updates `recent.repeater` and its SNR, so indirect path entries cannot affect direct-retry SNR gating or coding-rate selection.

**Examples:**
```
get flood.retry.bridge
set flood.retry.bridge on
```

---

#### View or change flood retry bridge buckets
**Usage:**
- `get flood.retry.bucket.<n>`
- `set flood.retry.bucket <n> <prefixes|none|off>`

**Parameters:**
- `n`: Bucket number from `1` to `6`.
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 17 entries per bucket.
- `none` or `off`: Clear the bucket.

**Examples:**
```
get flood.retry.bucket.1
set flood.retry.bucket 1 71CE82,C7618C
set flood.retry.bucket 2 none
```

---

#### View or change direct retry count

**Search terms:** tx retries, transmit retries, direct retries, retry count, retry attempts, retransmission count, message retries, DM retries.

**Usage:**
- `get direct.retry.count`
- `set direct.retry.count <count>`

**Parameters:**
- `count`: Maximum retry attempts after the original send, from `1` to `15`.

**Default:** `15` with the `rooftop` preset

**Note:** This setting limits retries for eligible direct-routed packets such as
traces, requests, responses, ACKs, and recognized telemetry. Direct-routed
**text messages (type 2)** are an exception: they allow **up to 21 retries after the original send**,
regardless of `direct.retry.count` or the repeater short-path cap. For example,
`set direct.retry.count 1` limits eligible non-text packets to one retry, but
does not reduce the text-message limit.

These are maximums, not a fixed number of transmissions. Retries stop early
when the node hears the next hop forward the packet. The special final-hop
retry sends only one duplicate because the destination does not forward the
packet. Repeater non-text retries are also capped at 8 for a retry path of up
to 3 hops, 12 for 4 hops, and 15 for longer paths; these caps never increase a
lower configured count.

Use `set direct.retry off` to disable this node's direct retries, including
text-message retries, and `set direct.retry on` to enable them again. A count
of `0` is not supported. Sending apps and other nodes may have their own retry
behavior; this setting does not change it.

**Examples:**
```
get direct.retry.count
set direct.retry.count 1
set direct.retry.count 4
set direct.retry.count 15
```

---

#### View or change direct retry base delay

**Search terms:** retry delay, retry timeout, retransmission timeout, wait between retries.

**Usage:**
- `get direct.retry.base`
- `set direct.retry.base <ms>`

**Parameters:**
- `ms`: First retry wait in milliseconds, from `10` to `5000`.

**Default:** `175` with the `rooftop` preset

**Explanation:**
- The first retry waits for `base + packet-length add-on + random forwarding jitter`
  after the preceding transmission completes.
- TRACE and
  ANON_REQ/type 7 packets use a 3x line-time add-on. TXT_MSG/type 2
  packets use 7x. Other direct retry packets use 6x.
- Room-server and sensor firmware use this configured base with the same
  packet-type add-ons.
- Larger values reduce channel pressure and give slow repeaters more time.
- Smaller values recover faster but create tighter retry bursts.

**Examples:**
```
get direct.retry.base
set direct.retry.base 175
set direct.retry.base 275
set direct.retry.base 500
```

---

#### View or change direct retry step delay

**Search terms:** retry backoff, increasing retry delay, retry interval step.

**Usage:**
- `get direct.retry.step`
- `set direct.retry.step <ms>`

**Parameters:**
- `ms`: Extra milliseconds added for each subsequent retry, from `0` to `5000`.

**Default:** `100` with the `rooftop` preset

**Explanation:**
- Retry delay is `base + packet-length add-on + random forwarding jitter + attempt_index * step`.
- TRACE and ANON_REQ/type 7 packets
  use a 3x packet-length add-on. TXT_MSG/type 2 packets use 7x.
  Other direct retry packets use 6x.
- Room-server and sensor firmware use this configured step with the same
  packet-type add-ons.
- With `base=175` and `step=100`, the fixed portion is `175`, `275`, `375`,
  `475` ms, and so on, before the packet-length add-on and random jitter.
- `step=0` keeps every retry at the same delay.
- Larger steps spread retries over time and are safer on busy channels.

**Examples:**
```
get direct.retry.step
set direct.retry.step 0
set direct.retry.step 50
set direct.retry.step 100
set direct.retry.step 250
```

---

#### View or change direct retry SNR margin

**Search terms:** retry signal threshold, retry SNR threshold, retry signal margin.

**Usage:**
- `get direct.retry.margin`
- `set direct.retry.margin <snr_db>`

**Parameters:**
- `snr_db`: Extra SNR margin above the SF receive floor, from `0` to `40`.

**Default:** `5.00` with the `rooftop` preset

**Notes:**
- This command is repeater-only.
- Unknown repeaters are still retried.
- Known repeaters below the receive floor plus this margin are skipped.
- Failed attempts lower the recent repeater SNR estimate by `0.25 dB`.

**Examples:**
```
get direct.retry.margin
set direct.retry.margin 0
set direct.retry.margin 2.5
set direct.retry.margin 5
set direct.retry.margin 10
```

---

#### View or change adaptive direct retry coding rate
**Usage:**
- `get direct.retry.cr`
- `set direct.retry.cr off`
- `set direct.retry.cr on` (room-server and sensor)
- `set direct.retry.cr <cr4_min>,<cr5_min>,<cr7_min>,<cr8_max>`

**Parameters:**
- `cr4_min`: Minimum SNR in dB to retry at CR4.
- `cr5_min`: Minimum SNR in dB to retry at CR5.
- `cr7_min`: Minimum SNR in dB to retry at CR7.
- `cr8_max`: Maximum SNR in dB that forces CR8.

**Default:** `10.00,7.50,2.50,2.50`

**Explanation:**
- Higher SNR uses faster coding rates.
- Lower SNR uses more robust coding rates.
- Repeater retry attempts escalate from the adaptive starting CR. CR4 starts as CR4, CR5, CR7, CR7, then CR8. CR5 starts as CR5, CR7, CR7, then CR8. CR7 gets two attempts, then CR8.
- Repeater adaptive CR selection intentionally skips CR6.
- Non-repeater retry packets start at the current radio CR and follow the same escalation pattern, clamped at CR8. With the normal CR5 radio setting this is CR5, CR7, CR7, then CR8.
- Room-server and sensor firmware accept `on` or `off`; numeric SNR thresholds
  are repeater-only because those roles do not keep recent-repeater SNR data.
- `off` disables per-packet direct-retry CR overrides and uses the current radio CR.
- Retry packets may use a different coding rate, but they keep the radio's normal physical preamble.
- Unknown repeaters start at `+3.00 dB` for adaptive CR selection.
- A failed unknown repeater is seeded at `+2.75 dB`.
- Each later failure lowers the SNR estimate by `0.25 dB`.

**Examples:**
```
get direct.retry.cr
set direct.retry.cr off
set direct.retry.cr on
set direct.retry.cr 10.0,7.5,2.5,2.5
set direct.retry.cr 12.0,8.0,4.0,1.0
set direct.retry.cr 8.0,5.0,1.5,0
set direct.retry.cr 6.0,3.0,0,-2.0
set direct.retry.cr 20.0,12.0,6.0,2.0
set direct.retry.cr 4.0,2.0,0,-4.0
```

**Example profiles:**
- Conservative weak-link profile:
```
set direct.retry.cr 12.0,8.0,4.0,1.0
```
- Balanced rooftop profile:
```
set direct.retry.cr 10.0,7.5,2.5,2.5
```
- Faster strong-link profile:
```
set direct.retry.cr 6.0,3.0,0,-2.0
```
- Very cautious noisy-link profile:
```
set direct.retry.cr 20.0,12.0,6.0,2.0
```

---

#### View, seed, or clear the recent repeater table
**Usage:**
- `get recent.repeater`
- `get recent.repeater <page>`
- `get recent.repeaters <page>`
- `get recent.repeaters search <prefix> [page]`
- `set recent.repeater <prefix> [snr_db]`
- `clear recent.repeater`

**Parameters:**
- `prefix`: Repeater path-hash prefix as 2, 4, or 6 hex characters.
- `snr_db`: Optional SNR in dB. If omitted or invalid, defaults to `3.0`.
- `page`: 1-based result page.

**Note:** These commands are repeater-only.

The default capacity is 256 entries on classic ESP32, 2,048 on other ESP32
chips, 512 on nRF52, and 64 on other platforms. Builds can override it with
`MAX_RECENT_REPEATERS`. Classic ESP32's history uses 3,072 bytes of startup
heap instead of 24,576 bytes, leaving more memory for the packet pool and Wi-Fi.

**Output order:**
- `get recent.repeater` lists 3-byte prefixes first, then 2-byte prefixes, then 1-byte prefixes.
- Within each prefix length, entries are sorted from highest SNR to lowest SNR.
- `search` returns every overlapping path-hash entry. For example, searching
  `860C` can return `86`, `860C`, and `860CCA`; it does not return a different
  branch such as `86D0`.
- Search rows include the monotonic age of the entry's most recent recording,
  compacted to a whole `s`, `m`, or `h` field. Search pages contain up to six
  rows so the result remains within the remote CLI reply limit.

**SNR details:**
- Recent repeater SNR is stored internally in quarter-dB units.
- Heard repeater samples update an existing table entry with a weighted blend: `75%` existing SNR and `25%` new heard SNR, rounded up.
- Direct retry success also feeds the heard echo SNR back into the same weighted table.
- Direct retry failure is not weighted: each final echo-timeout failure lowers that repeater's SNR by `0.25 dB`.
- Unknown repeaters start at `+3.00 dB` for adaptive CR selection.
- If an unknown repeater fails, it is seeded into the table at `+2.75 dB`.
- `set recent.repeater <prefix> [snr_db]` seeds a missing prefix or adds another weighted sample for an existing prefix.
- Successful `set recent.repeater` replies include the stored prefix and SNR, for example `OK - set A1B2C3 at 3.0 SNR`.
- Entries strictly older than 24 hours are removed during a sweep every three hours, so an entry can remain for at most approximately 27 hours.

**Examples:**
```
get recent.repeater
get recent.repeater 2
get recent.repeaters search 86
get recent.repeaters search 860C page 2
set recent.repeater A1B2C3 8.5
set recent.repeater 71CE82 -3.25
set recent.repeater A1B2C3
clear recent.repeater
```

---
### GPS (When GPS support is compiled in)

#### View or change GPS state

**Search terms:** enable GPS, disable GPS, turn GPS on, turn GPS off.

**Usage:**
- `get gps`
- `set gps <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `off`

**Note:** These command names are shared with GPS-capable Companion builds.
Companion reports `on` or `off`; infrastructure includes receiver details:
- `off, {powered|unpowered}` when GPS is manually disabled
- `on, {powered|unpowered}, {fix|no fix}, {sat count} sats` when GPS is enabled

`powered` describes the supply, not proof that the receiver is tracking or
asleep. Shared-power hardware can remain powered while GPS processing is off.

On repeaters, `gps off` cancels any acquisition already in progress. A later
authorized location-telemetry request may wake the receiver, but only until its
first valid fix is cached; GPS processing then stops again. Whether its supply
can also turn off depends on the board's power policy below.

---

#### RAK4631 shared peripheral power

**Usage:**

```text
get peripherals.mode
set peripherals.mode gps-only
set peripherals.mode shared
reboot
```

**Default:** `gps-only` on RAK4631. The saved mode takes effect only after
reboot; setting it does not switch power immediately. `get peripherals.mode`
reports the saved choice, which may differ from the current boot's mode.

- `gps-only`: when no protected power owner exists, GPS-off and GPS sleep can
  turn off the shared WisBlock `3V3_S` supply. Select this only when other
  attached peripherals do not need that supply.
- `shared`: keeps `3V3_S` powered for other peripherals. Save this mode and
  reboot when using other WisBlock modules; firmware does not infer this choice
  from an I2C scan.

Ethernet/PoE builds and detected external NOR storage automatically keep the
rail on, even in `gps-only` mode or when the network service is disabled.
RAK3401 permanently protects the same rail because its radio frontend needs
it; `peripherals.mode` is a RAK4631 setting, not an override of that protection.

On a powered shared rail, RAK12501/L76K cannot be independently switched off:
GPS-off stops MCU parsing, not physical GNSS operation. Other receivers may use
internal power-saving commands, but `shared` does not promise physical sleep.
UART1 bridge ownership remains protected; changing this mode does not grant
permission to connect two transmitters to the GPS UART.

---

#### Sync this node's clock with GPS time
**Usage:** 
- `gps sync`

The GPS must be enabled. When GPS power saving has put an enabled receiver to
sleep, this command schedules a sync and wakes it; after `set gps off`, it reports
`gps is off` without scheduling work.

On repeaters with device power saving enabled, the automatic GPS cycle wakes
for up to 10 minutes and repeats seven days after GPS sleeps. A valid GPS time
can correct the RTC either forward or backward. With device power saving off,
an enabled GPS refreshes the RTC every 30 minutes.
These are the legacy defaults; `gps.sync.interval` below overrides them.

---

#### Set the automatic GPS clock-sync interval

```text
get gps.sync.interval
set gps.sync.interval 1
set gps.sync.interval 24
set gps.sync.interval 336
```

The interval is a whole number of **hours**, from **1 to 336** (two weeks).
Larger numbers are capped, not rejected. For example:

```text
set gps.sync.interval 999
OK - GPS sync interval 336 hours (saved)
```

Zero, negative numbers, fractional hours, and nonnumeric values are rejected.
The setting is saved across reboots and supported by GPS-equipped repeater,
room-server, sensor, and Companion builds. Until it is explicitly configured,
the getter reports `default (board GPS sync policy)` and existing board timings
are retained.

The selected interval controls periodic clock acquisitions both with GPS power
saving and with a continuously powered receiver. Changing it reschedules a
sleeping receiver without immediately powering it on. A GPS fix can take time;
an unsuccessful acquisition retains the existing bounded awake window, then
backs off before trying again. Device GPS/power switches still apply.

This does not change position-update `gps_interval`, location telemetry/cache
timings, or mesh/NTP time synchronization. Location requests can still power GPS
for a fix, but do not bypass the configured clock-sync interval. Explicit
`gps sync` remains an immediate acquisition request where that command exists.

---

#### Set this node's location based on the GPS coordinates
**Usage:** 
- `gps setloc`

---

#### View or change the GPS advert policy
**Usage:**
- `gps advert`
- `gps advert <policy>`

**Parameters:** 
- `policy`: `none`|`share`|`prefs` 
  - `none`: don't include location in adverts
  - `share`: share gps location (from SensorManager)
  - `prefs`: location stored in node's lat and lon settings

**Default:** `prefs` on every repeater, room-server, and sensor build that uses
the common advert policy. A previously saved `none`, `share`, or `prefs` choice
still overrides the first-boot default after an update.

---

### Sensors (When sensor support is compiled in)

#### View or change telemetry access mode
**Usage:**
- `get telemetry.access`
- `set telemetry.access <mode>`

**Parameters:**
- `mode`: `all`|`acl`
  - `all`: allow telemetry requests using the requester-provided telemetry mask
  - `acl`: require ACL read-only or higher for telemetry, including GPS

**Default:** `all`

**Note:** `all` matches the previous sensor telemetry behavior.

---

#### View the list of sensors on this node
**Usage:** `sensor list [start]`

**Parameters:**
- `start`: Optional starting index (defaults to 0)

**Note:** Output format: `<var_name>=<value>\n`

---

#### View or change the value of a sensor
**Usage:** 
- `sensor get <key>`
- `sensor set <key> <value>`

**Parameters:**
- `key`: Sensor setting name
- `value`: The value to set the sensor to

---

### Bridge (When bridge support is compiled in)

For a two-repeater setup and diagnostic flow, see the
[ESP-NOW bridge setup guide](espnow_bridge_setup.md) or
[RS-232/UART bridge guide](rs232_bridge_setup.md). For an existing WiFi network,
see the [eight-peer UDP bridge guide](udp_bridge_setup.md).

#### WiFi UDP bridge

Available on supported ESP32 repeater images with `WITH_UDP_BRIDGE`. UDP starts
off on new/upgraded installs. It requires an existing station WiFi connection,
and pauses during AP/AP+STA operation, disconnection, browser OTA and ownership
transitions. It does not start a network or require an MQTT broker.

```text
get udp.enabled
set udp.enabled on
set udp.enabled off
get udp.port
set udp.port 4404
get udp.peer.1
set udp.peer.1 192.168.1.51:4404
set udp.peer.1 off
get udp.status
get udp.stats
get udp.stats rx
get udp.stats tx
get udp.stats drops
get udp.stats test
```

Peer slots are `udp.peer.1` through `udp.peer.8`. Each destination is one
numeric unicast IPv4 address plus a port `1..65535`. The listening port defaults
to `4404`. Multicast, loopback, unspecified/broadcast and duplicate endpoints
are rejected. Both boards must list each other's listening address/port.
Reserve DHCP addresses so the stored destinations remain valid.

The transport reuses `bridge.secret` for authenticated sessions and packet
integrity; no additional password is introduced. UDP payloads are not
encrypted. Counter/status output does not reveal the secret. Config writes
use the existing authenticated CLI policy and a separate checked preferences
file; a successful save reports intent rather than reachability.

Local-only diagnostic actions:

```text
udp test 1
udp test 1 A1B2C3D4
udp resetstats
```

The peer number is `1..8`; the optional ID is exactly eight hexadecimal digits
without `0x`. A queued/sent probe is not a delivery acknowledgement. On the
destination, check `get udp.stats test` for an increased `rx` and matching
`rxid`, then reverse the test. An authenticated peer session must be ready.
The status `ready` count records established sessions, not current liveness;
use a fresh received probe to confirm reachability. Incoming UDP enters the
local LoRa mesh and is not relayed to other UDP peers.
Probes do not echo, retry, allocate mesh packets or transmit on LoRa. Resetting
statistics does not write preferences or restart the bridge.

Both `set usb.logging on` and `set usb.debug on` enable bounded runtime USB
summaries without requiring a special debug build. Restore both previous
settings after capture. CLI pages/probes work with logging disabled.

WiFi client isolation and firewall rules must allow peer-to-peer UDP; the
firmware cannot bypass an isolating guest network. Isolation is an optional
network policy, not an intrinsic WiFi restriction. See the
[setup and troubleshooting guide](udp_bridge_setup.md).

---

#### View the compiled bridge type
**Usage:** `get bridge.type`

---

#### Diagnose the ESP-NOW bridge

Available when the optional ESP-NOW bridge is compiled in. These are runtime
diagnostics; no `BRIDGE_DEBUG` build flag is required.

**Read-only commands:**

```text
get espnow.status
get espnow.stats
get espnow.stats rx
get espnow.stats tx
get espnow.stats drops
get espnow.stats test
get espnow.peer
```

`set espnow.peer AA:BB:CC:DD:EE:FF` saves a fixed unicast destination and
restarts an enabled bridge. Unicast uses MAC acknowledgements and up to three
retries after an explicit failed send, with unchanged bridge packet bytes.
`set espnow.peer broadcast` restores the default broadcast destination.
Successful MAC delivery does not confirm subsequent LoRa forwarding.

Status reports local state (`off`, `teardown-error`, `driver-error`,
`channel-mismatch` or `ready`), then these compact fields:

| Field | Meaning |
| --- | --- |
| `ch` | Configured/actual 2.4 GHz channel; actual `0` means unavailable. |
| `fmt`, `src` | Packet format and configured LoRa packet source (`rx` or `tx`). |
| `wifi` | Shared WiFi enable setting, not proof of WiFi association. |
| `mode`, `ps`, `proto` | WiFi SDK mode and power-save integers, and protocol bitmask in hexadecimal. An unavailable mode or power-save query returns `-1`. |
| `q` | Current receive/transmit queue depths. |
| `last` | Last recorded SDK error stage/code, which may remain after recovery. |

`espnow.running on` means the local bridge initialized; it does not prove a
peer is reachable. The statistics pages use these keys:

| Page | Keys and meaning |
| --- | --- |
| Default | `rx`: received callback frames; `mesh`: inbound MeshCore packets queued; `tx`: outbound MeshCore packets queued; `sent`: successful frame callbacks. |
| `rx` | `frames`: received callback frames; `decoded`: parsed MeshCore packets; `mesh`: queued for mesh processing; `crc`: checksum failures; `bad`: malformed frames; `parse`: packet parse failures. |
| `tx` | `req`: mesh TX requests; `queued`: mesh packets queued; `start`: SDK-accepted frames; `ok`: successful frame callbacks; `err`: SDK start failures; `cbfail`: failed frame callbacks; `timeout`: callback timeouts. |
| `drops` | `rxq`: RX queue drops; `foreign`: foreign-group packets; `filter`/`dupe`: inbound filter/duplicate drops; `txq`: recorded local mesh TX rejection/final-failure decisions; `txfilter`/`txdupe`: outbound filter/duplicate drops; `big`: oversize TX drops. |
| `test` | `queued`, `start`, `ok`, `fail`, `timeout`: probe TX stages; `rx`, `bad`: valid/invalid probes received; `txid`, `rxid`: last queued/received IDs in hexadecimal. |

Mesh admission means queued for normal processing, not confirmed LoRa
forwarding or end-to-end delivery. Packet and frame counters have different
units: fragmentation can produce several frames for one mesh packet. Counters
survive bridge stop/start; reboot or the explicit reset starts a new window.
Stopping or restarting the bridge clears pending queue work without classifying
its delivery. These counters are not a complete end-to-end delivery ledger.

**Local-only actions:**

```text
espnow test
espnow test A1B2C3D4
espnow resetstats
```

The optional test ID is exactly eight hexadecimal digits, case-insensitive,
without `0x`, spaces or trailing arguments. Omitting it uses uptime in
milliseconds. The reply `OK - test queued id=...; confirm on peer` confirms
local queueing only. Read the other updated board's `get espnow.stats test`
and match `rxid`, with an increased `rx`, to establish one-way probe receipt. Send a
different ID from the other board to test the reverse direction.

A probe is unencrypted, CRC-checked transport diagnostic data, not a MeshCore
packet or a security token. It does not verify packet-format compatibility or
`bridge.secret`, authenticate the peer or establish normal packet delivery.
There is no automatic echo,
retry or LoRa forwarding; older firmware ignores it. Resetting statistics
does not restart the bridge or write preferences. Test/reset commands are
rejected over LoRa; use a local console. Every reply fits the bounded CLI
frame, and diagnostics do not print from WiFi callbacks.

For a short USB debug capture, record both `get usb.logging` and
`get usb.debug`, then enable both `set usb.logging on` and `set usb.debug on`.
Run the test and restore both original settings. The ordinary image emits
bounded `ESPNOW:` updates from its main loop at most once per second when
diagnostic state changes, even with `BRIDGE_DEBUG=0`. Updates can be skipped
when USB is busy; read the counter pages for exact values. The CLI counter
pages and probes do not require either logging gate.

See the [two-board diagnostic flow](espnow_bridge_setup.md#test-the-24-ghz-path-without-lora-forwarding).
Do not include `get bridge.secret` or WiFi credentials in shared debug logs.

---

#### View or change the bridge enabled flag

**Search terms:** enable bridge, disable bridge, serial bridge, RS232 bridge.

**Usage:**
- `get bridge.enabled`
- `get bridge.running`
- `set bridge.enabled <state>`

**Parameters:**
- `state`: `on`|`off`

`bridge.enabled` is the saved intent. `bridge.running` reports whether the
bridge actually started in this boot; they can differ after a hardware conflict,
missing credentials, or a transient initialization failure. Normal merged
repeater images default to `off`; dedicated bridge images may default to `on`.

Full MQTT repeaters with RS-232 expose three independent transports. Use
`set rs232.enabled on|off` and `get rs232.enabled` for saved UART intent;
`get rs232.running` reports the actual UART state. `mqtt.enabled` and
`espnow.enabled` select the other two transports. In MQTT images the historical
`bridge.enabled` alias controls ESP-NOW; in non-MQTT UART images it controls
RS-232. `bridge.baud` and `bridge.uart` change only UART configuration, without
restarting MQTT or ESP-NOW. The new UART mode starts off when upgrading old
MQTT preferences and retains explicit settings across reboots.

---

#### Diagnose the RS232 bridge

Available on repeater builds containing the optional RS-232 bridge. These
runtime diagnostics require no `BRIDGE_DEBUG` build flag. Builds without the
transport do not gain UART support from these commands.

**Read-only commands:**

```text
get rs232.transport
get rs232.stats
get rs232.stats rx
get rs232.stats tx
get rs232.stats drops
get rs232.stats test
```

The existing `get rs232.status` keeps its heartbeat/automatic-standby report.
If no UART bridge object exists, such as after explicitly disabling it,
the transport and counter commands report `RS232 diagnostics unavailable`;
use `rs232.enabled`, `rs232.running` and `rs232.status` to check saved intent
and power state.
`get rs232.transport` reports the local transport state (`ready`, `off`,
`standby`, `probing` or `retrying`) and these fields:

| Field | Meaning |
| --- | --- |
| `baud` | Configured serial baud rate. |
| `pins` | Selected RX/TX pin numbers. |
| `src` | Configured LoRa packet source, `rx` or `tx`. |
| `rxpart`, `rxready` | Bytes held in the current RX frame and whether a complete frame awaits mesh-pool space. |
| `tx` | Bytes remaining in the owned TX frame. |
| `heartbeat` | `on` initiates heartbeats; `passive` can still answer a valid ping. |
| `last` | Last recorded diagnostic error: `none`, `pins`, `driver`, `test.stopped`, `test.busy` or `test.timeout`. It can remain after recovery. |

A running UART means local initialization succeeded, not that a peer is
reachable. Counter pages separate ordinary mesh traffic from explicit probes:

| Page | Keys and meaning |
| --- | --- |
| Default | `rx`: received data frames; `mesh`: inbound packets admitted to mesh processing; `tx`: outbound mesh packets queued; `sent`: mesh frames fully accepted by the local UART. |
| `rx` | `bytes`: all consumed UART bytes; `frames`: checksum-valid data frames; `crc`: checksum failures; `length`: invalid lengths; `parse`: mesh-packet parse failures; `timeout`: incomplete-frame expiry; `pool`: complete frames held for mesh-pool space. |
| `tx` | `req`: mesh TX requests; `queued`: mesh frames accepted into the owned buffer; `sent`: fully UART-accepted mesh frames; `bytes`: all UART-accepted bytes; `stall`, `short`: blocked drain attempts or short-write events. |
| `drops` | `noise`: synchronization rejects; `filter`/`dupe`: inbound filter/duplicate drops; `busy`/`stopped`: mesh TX rejected by transport state; `txfilter`/`txdupe`: outbound filter/duplicate drops; `big`: invalid or oversize TX packets. |
| `test` | `queued`, `sent`, `fail`: probe TX stages and recorded failures; a late write can count as both failed and sent; `rx`, `bad`: valid/invalid probes received; `txid`, `rxid`: last queued/received IDs in hexadecimal. |

Mesh admission means queued for normal processing, not confirmed LoRa
forwarding or end-to-end delivery. Local write acceptance does not prove the
bytes reached the peer. Byte totals include data, heartbeat and test traffic;
RX bytes also include discarded noise. A frame can cause several write stalls.
Counters survive same-object UART stop/start, including
automatic standby. Disabling or reconfiguring the bridge can delete and replace
that object, starting a new window; reboot and explicit reset also clear the
counters. Stopping a transport can discard unfinished work without determining
delivery, so the pages are not a complete delivery ledger.

**Local-only actions:**

```text
rs232 test
rs232 test A1B2C3D4
rs232 resetstats
```

The optional ID is exactly eight hexadecimal digits, case-insensitive, without
`0x`, spaces or trailing arguments. Omitting it uses uptime in milliseconds.
`OK - test queued id=...; confirm on peer` confirms local queueing only. On
the other updated board, `get rs232.stats test` must show an increased `rx`
and matching `rxid`. Use a different ID in the reverse direction.

Probes use a reserved UART-local control frame. They do not allocate a mesh
packet, enter duplicate tracking, refresh peer liveness, authenticate a peer,
or get forwarded onto LoRa. There is no automatic echo or retransmission.
A probe that misses its write deadline records one failure. If no byte was
accepted by the UART, it is cancelled. If a prefix was already accepted, the
bridge retains and drains only the original suffix, with bounded work per
main-loop pass, before another frame can start. This preserves framing for
older receivers. A later completion also increments `sent`; the historical
failure is retained and neither result proves peer receipt.
A stopped, transitioning
or busy UART, including a due heartbeat, rejects the test rather than waking or
taking over the port. Older
firmware ignores the extension. The test and counter reset do not write
preferences and are rejected over LoRa; use a local console.

For a short USB capture, record both `get usb.logging` and `get usb.debug`,
then enable both `set usb.logging on` and `set usb.debug on`. The ordinary
image emits bounded `RS232:` updates from its main loop at most once per
second when diagnostic state changes. Busy USB output can skip updates; use
the counter pages for exact values. Restore both original settings afterward.
The CLI pages and test probes need neither logging gate.

See the [two-board serial diagnostic flow](rs232_bridge_setup.md#test-each-serial-direction-without-lora-forwarding).

---

#### RS232 heartbeat and automatic standby

Available on repeater builds with an RS232 bridge. This is a UART-local control
exchange, not an advert or a LoRa packet. Room builds without RS232 are unaffected.

**Usage:**

- `get rs232.status`
- `get rs232.heartbeat`
- `set rs232.heartbeat on|off`
- `get rs232.autosleep`
- `set rs232.autosleep on|off|force`
- `get rs232.idle`
- `set rs232.idle <seconds>` (60 through 3600; default 300)

Heartbeat and negotiated automatic standby are **on by default**, including
upgrades. An active UART pings every 30 seconds (52 bytes per ping).
Stock bridges ignore that traffic and still receive the original data frames;
they are not automatically put to sleep: standby is armed only after a fresh
heartbeat reply proves support. Fresh, session/challenge-matched replies
prove a bidirectional peer;
ordinary valid incoming data can keep a legacy link awake but cannot prove
heartbeat support. The status separates saved intent, actual UART state,
standby/probe phase, and confirmed versus legacy/unknown peer liveness.

Enable on both updated peers:

```text
set rs232.enabled on
set rs232.heartbeat on
set powersaving on
set rs232.idle 300
set rs232.autosleep on
get rs232.status
```

With heartbeat, autosleep, and global powersaving all on and the peer's heartbeat
support confirmed, when no peer responds for the idle interval the UART is soft-stopped while
`rs232.enabled` stays `on`. It wakes after 30 seconds for a 2-second probe;
every eighth probe listens for 40 seconds. That longer window spans the other
side's sleep interval, so two sleeping updated peers can reconnect without
manual intervention (normally within about five minutes with working wiring
and timely loop servicing). Healthy peers remain running; this is dead-link
power saving, not an idle-data duty cycle.

Stock receivers ignore these control frames; they are deliberately invalid
zero-length stock frames with an ASCII-only remainder. They do not allocate a
mesh packet or enter LoRa forwarding. Stock peers do **not** answer heartbeats,
so default negotiated standby never arms on a stock-only link. Valid legacy
data after heartbeat replies cease also disarms negotiated standby, allowing
a replacement stock peer to stay awake. Use `set rs232.autosleep force` only
to sleep an absent/unconfirmed UART peer; this explicitly bypasses stock
protection and can miss one-off stock packets.
If a previously confirmed updated peer is silently replaced with stock while
the local UART is already sleeping, its first packet can be missed before
replacement is detected. Set autosleep off before that change, or restart the
local bridge to clear the old negotiated session.
While the UART is stopped it cannot receive a wake byte; mesh traffic
during standby is not queued for later UART delivery. Turning autosleep or
global powersaving off resumes the saved-enabled UART; `rs232.enabled off`
remains an explicit off and never automatically wakes.
`rs232.heartbeat off` stops initiating heartbeats and prevents standby. An
updated active peer can still answer a valid incoming ping in passive mode.

---

#### Add a delay to packets routed through this bridge
**Usage:**
- `get bridge.delay`
- `set bridge.delay <ms>`

**Parameters:**
- `ms`: Delay in milliseconds (0-10000)

**Default:** `500`

---

#### View or change the source of packets bridged to the external interface
**Usage:**
- `get bridge.source`
- `set bridge.source <source>`

**Parameters:**
- `source`:
  - `rx`: bridges received packets
  - `tx`: bridges transmitted packets

**Default:** `tx`

> **Note:** For MQTT, use `mqtt.rx` and `mqtt.tx` to control each direction independently. Both can be enabled together. Use `mqtt.enabled` as the MQTT master switch; `bridge.*` configures RS232 and ESP-NOW bridges.

---

#### Select the radio for incoming bridge packets

```text
get bridge.ingress
set bridge.ingress radio2
set bridge.ingress radio
set bridge.ingress off
```

This receiver-side setting applies to incoming mesh packets from ESP-NOW, UDP
and RS-232 bridges. It is saved immediately and needs no restart. Packets
already queued retain their original route. `radio` is
the default, preserving the existing primary-radio behavior and crossing rules.
Existing senders and bridge wire formats remain compatible.
An unreadable or corrupt saved selection blocks bridge input rather than
silently restoring the primary; `get bridge.ingress` reports the fault.

`radio2` assigns incoming packets to the active MeshCore second profile and
keeps their forwarding on that profile, even with `radio2.cross on`. If radio2
is off, RX-only, owned by another protocol, or changed while a packet waits,
the packet is dropped; it never falls back to the primary radio. Normal repeat,
scope, duplicate and filter checks still apply. A matching source route below
overrides this default. `off` drops incoming packets without a matching route.
The selection follows forwarded packets, retries and relayed ACKs. Locally
generated adverts and admin replies keep their existing routing, including
`tx.reply`; this is not a global transmit override.

For a MediaMesh receiver feeding a repeater whose primary is USA 910.525 MHz,
see the [MediaMesh bridge example](espnow_bridge_setup.md#send-bridge-traffic-only-on-mediamesh).
`bridge.source` controls traffic leaving for the bridge and is independent of
`bridge.ingress`. This does not add MQTT packet injection or change MQTT
publication settings.

#### Route incoming bridge packets by sender

```text
get bridge.route
get bridge.route.1
set bridge.route.1 mac:AA:BB:CC:DD:EE:10 radio2
set bridge.route.2 udp:192.168.1.51:4404 radio
del bridge.route.1
clear bridge.routes
```

Slots `1..8` match an exact ESP-NOW station MAC (`mac:`) or UDP sender address
and listening port (`udp:`). Each source can appear once. A matching rule
selects only `radio`, only `radio2`, or drops that source with `off`, regardless
of crossing settings. Unmatched packets use `bridge.ingress`; set that to
`off` to accept only listed senders. `get bridge.route` reports the count and
default; query each numbered slot for its saved rule. `get espnow.stats mac`
reads the local station MAC once the ESP-NOW/WiFi driver is active.

Rules are saved separately from the default and from legacy node preferences.
Missing routes preserve existing behavior and bridge frames remain unchanged.
New admissions use the saved route; queued packets retain their original
profile and generation. Unavailable, RX-only or foreign-protocol targets drop
the packet rather than falling back. See the [three-V4 example](split_radio_repeater.md).

---

#### View or change the MQTT master switch

**Search terms:** enable MQTT, disable MQTT, MQTT on, MQTT off, WiFi logging.

**Usage:**

- `get mqtt.enabled`
- `set mqtt.enabled on`
- `set mqtt.enabled off`
- `get mqtt.running`
- `get mqtt.stopping`
- `get mqtt.status`

These commands are the same on MQTT-capable Companion, Repeater, Room Server,
and Sensor builds. The master switch is saved; turning it off disconnects
MQTT while keeping broker presets and credentials. Turning it on allows those
brokers to reconnect. `get mqtt.running` checks the service's runtime state;
`get mqtt.status` reports individual broker connections. Enabling MQTT does
not imply that a broker is connected.

Custom `mqtts://` and `wss://` brokers require an image with the embedded CA
bundle. Without it, the slot stays disconnected and sends no credentials;
use a build with the bundle or a supported preset with its pinned CA.
Portable MQTT images retain those presets but do not include the general
bundle for custom TLS brokers. Explicit `mqtt://` and `ws://` configurations
remain available for trusted networks and use plaintext transport.

On ESP32 Repeater and Room Server images, `set mqtt.enabled off` acknowledges
the stop request while the MQTT task disconnects and destroys its clients.
`get mqtt.stopping` stays `on` until that cleanup completes; `mqtt.running`
also stays `on` while those resources are still owned. Wait for
`mqtt.stopping` to become `off` before enabling MQTT again. UART, ESP-NOW,
and the radio continue running during this cleanup. OTA still waits for a
completed MQTT shutdown before it can write firmware.

USB logging remains independent. On images with both outputs,
`set logging.output off|usb|wifi|both` selects USB and MQTT together.

---

#### View or change MQTT RX packet uplinking

**Search terms:** MQTT receive logging, MQTT RX capture, publish received packets.

**Usage:**
- `get mqtt.rx`
- `set mqtt.rx <on|off>`

**Parameters:**
- `on`: uplink received (RX) packets to MQTT brokers
- `off`: disable RX packet uplinking

**Default:** `on`

---

#### View or change MQTT TX packet uplinking

**Search terms:** MQTT transmit logging, MQTT TX capture, publish sent packets.

**Usage:**
- `get mqtt.tx`
- `set mqtt.tx <on|off|advert>`

**Parameters:**
- `on`: uplink all transmitted (TX) packets to MQTT brokers
- `advert`: uplink only this node's own advert packets (self-originated advertisements only - forwarded adverts from other nodes are filtered out)
- `off`: disable TX packet uplinking

**Default:** `advert`

> **Note:** `mqtt.rx` and `mqtt.tx` take effect immediately - no restart required. Both can be enabled simultaneously.

---

#### View or change MQTT advert signature filtering

**Usage:**

- `get mqtt.advert.verify`
- `set mqtt.advert.verify <on|off>`

**Parameters:**

- `on`: verify the Ed25519 signature of each received advert and skip publishing any that fail (the same check the mesh applies before relaying)
- `off`: publish received adverts as heard

**Default:** `off`

> **Note:** Adverts are uploaded before the mesh validates them, so a frame corrupted over the air that still passes the LoRa CRC is published with a mangled name or location. Leaving this off keeps that corruption visible to collectors; turning it on hides it from them. Dropped adverts are counted as `badsig=` in `get mqtt.stats`. Takes effect immediately.

On Companion builds, enabling this setting opts into a marked MQTT preference
record. Before rolling back to firmware without this setting, turn it off to
restore the older record format and retain the saved MQTT setup. Ordinary
default-off updates keep the existing record size.

---

#### View or change periodic neighbors publishing (MQTT observer, neighbors feature)
**Usage:**
- `get mqtt.neighbors`
- `set mqtt.neighbors <on|off>`

**Parameters:**
- `on`: periodically discover neighbor scopes and publish the neighbor table to the `neighbors` topic
- `off`: disable periodic neighbors publishing

**Default:** `off`

> **Note:** Requires a build with the neighbors feature compiled in (all PSRAM
> boards, plus non-PSRAM boards built with `MQTT_NEIGHBORS_WITHOUT_PSRAM`);
> elsewhere this replies `Err - neighbors not enabled in this build`. Non-PSRAM
> builds publish at most 20 neighbours per pass to bound internal-DRAM use, and
> set `truncated` with the true `total_neighbors` when the table is larger.
> The setting is read live by the mesh
> loop - no restart required; enabling it triggers a discovery on the next pass.
> While enabled, `get mqtt.status` gains a trailing `nbr: <next>/<last>` field
> (time to next publish, and how the last publish went).

This setting controls this node's outgoing periodic discovery and MQTT
publication. It does not control replies to requests from other nodes. Newer
repeater source builds have separate
[node and region response switches](#enable-or-disable-discovery-responses),
both defaulting to on; those switches are not in the published 1.18.0.1 images.

---

#### View or change the neighbors publish interval (MQTT observer, neighbors feature)
**Usage:**
- `get mqtt.neighbors.interval`
- `set mqtt.neighbors.interval <hours>`

**Parameters:**
- `hours`: how often to publish the neighbor table (12-336, default 24)

**Default:** `24` (hours)

> **Note:** Out-of-range values are rejected (not clamped). Requires a build
> with `WITH_MQTT_NEIGHBORS`; PSRAM boards enable it automatically and selected
> non-PSRAM variants opt in with `MQTT_NEIGHBORS_WITHOUT_PSRAM`.

---

#### View or change the NTP server (MQTT observer only)
**Usage:**
- `get mqtt.ntp`
- `set mqtt.ntp <hostname>`
- `set mqtt.ntp none`

**Description:** Sets the primary NTP server used for clock sync (required for JWT MQTT auth). On `set`, the device attempts an immediate sync of the just-configured server (primary only, so a typo fails fast) when WiFi is connected and the MQTT bridge is running.

**Fallbacks:** If the primary fails, the firmware tries `pool.ntp.org`, `time.google.com`, `time.cloudflare.com`, `time.aws.com`, and `time.nist.gov` in order (skipping duplicates).

**Default:** `pool.ntp.org` (when unset or `none`)

---

#### Diagnose NTP server connectivity (MQTT observer only)
**Usage:**
- `get mqtt.ntp.diag`

**Description:** Probes every configured NTP server (the custom primary, if set, plus the built-in fallbacks) and reports whether each responds. This is a pure connectivity diagnostic - it does **not** change the system clock.

- **Serial console:** prints a detailed table with each server's reported UTC time (or `FAIL`).
- **Over LoRa:** returns a compact `<server> ok|fail` list, one per line.

Requires WiFi connected and the MQTT bridge running.

---

#### View or change the speed of the bridge (RS-232 only)
**Usage:**
- `get bridge.baud`
- `set bridge.baud <rate>`

**Parameters:**
- `rate`: Integer baud rate from `9600` through the board's compiled
  `BRIDGE_MAX_BAUD` (commonly `500000`); for example `115200`. Stop the bridge
  with `set rs232.enabled off` in a combined MQTT image, or
  `set bridge.enabled off` in a non-MQTT UART image, before changing it.
  Enable the UART again with the matching switch.

**Default:** `115200`

---

#### View or change the UART used by the bridge (RS-232 only)
**Usage:**
- `get bridge.uart`
- `set bridge.uart <port>`

**Parameters:**
- `port`: Hardware UART number compiled for the board. Most boards expose one
  fixed UART. RAK4631 accepts `1` or `2`; UART 2 is the default so UART 1 can
  remain available to the RAK12501/L76K GPS. RAK12500 GPS uses I2C rather than
  this UART, but the explicit legacy Serial1 bridge omits the combined GPS
  provider and therefore does not expose either GPS path.

The setting is persistent and restarts an enabled bridge immediately. Normal
repeater artifacts start with `bridge.enabled off`; configure the UART and baud
rate before running `set bridge.enabled on`. Combined MQTT images use
`rs232.enabled` for that independent UART setting. On the canonical RAK4631 runtime
image, UART 1 is reserved even if the bounded boot probe hears no RAK12501.
Silence cannot prove that a cold L76K is physically absent, and that module
remains powered by the shared WB_IO2/3V3_S rail. Use UART 2. UART 1 requires an
explicit no-GPS/dedicated Serial1 bridge image. This fail-closed reservation
also applies when the detected GPS is an I2C RAK12500; that receiver does not
electrically use UART 1, but its presence cannot rule out another silent UART
module.

---

#### View or change the channel used for bridging (ESPNow only)
**Usage:**
- `get bridge.channel`
- `set bridge.channel <channel>`

**Parameters:**
- `channel`: Channel number from 1 through 13 in either format. This matches
  the primary-ESP-NOW policy and the default regulatory range used by the
  supported ESP32 targets.

This controls the optional ESP-NOW bridge transport; it does not change a
node's primary ESP-NOW mesh channel. Primary-ESP-NOW firmware uses
`get espnow.channel` and `set espnow.channel <1-13>` instead.

---

#### View or change the ESP-NOW bridge wire format
**Usage:**
- `get bridge.format`
- `set bridge.format <format>`

**Parameters:**
- `format`:
  - `wrapped`: the original ESP-NOW bridge framing (magic, checksum, and XOR
    using `bridge.secret`)
  - `raw`: the exact serialized MeshCore packet used by primary
    `ESPNOWRadio` firmware

**Default:** `wrapped`

The setting is persistent and restarts an enabled bridge immediately. Receive
parsing is strict: `wrapped` accepts only wrapped frames and `raw` accepts only
raw MeshCore frames. Coordinate the format and channel on every ESP-NOW peer;
there is no automatic dual-format receive mode because it would permit
ambiguous, asymmetric bridge deployments.

Use `raw` to connect a LoRa-primary `*_repeater_bridge_espnow` node to
`Generic_ESPNOW`, `SenseCapIndicator-ESPNow`, or another primary-ESP-NOW node.
Raw mode enables the ESP-NOW LR PHY and sends at the same LR rate used by those
targets. All nodes must use the same 1-13 channel. On a primary-ESP-NOW node,
change that side with `set espnow.channel <1-13>` and reboot; on the bridge,
use `set bridge.channel <1-13>`.

Raw mode ignores `bridge.secret` and removes the bridge wrapper's lightweight
network isolation. MeshCore's own packet authentication/encryption still
applies where the packet type provides it, but public frames and routing
metadata remain visible. The wrapped format's XOR is isolation, not strong
cryptography. Bridge duplicate tracking remains active in both formats, but
deploying multiple gateways between the same LoRa and ESP-NOW coverage areas
can still increase duplicate traffic while their seen-packet tables converge.

One ESP-NOW frame carries at most 250 payload bytes. In raw mode, serialized
MeshCore packets up to that size remain byte-for-byte compatible with existing
raw endpoints. Updated raw endpoints split 251-255-byte transport units into
two versioned fragments and reassemble them by source MAC, length, and CRC;
both endpoints must include this support for those sizes. Current valid
MeshCore packet geometry reaches 254 bytes, while 255 is retained as transport
headroom. Wrapped mode retains its legacy 246-byte maximum after the four-byte
magic and checksum overhead, and drops larger packets instead of truncating
them.

---

#### Set the ESP-Now secret
**Usage:** 
- `get bridge.secret`
- `set bridge.secret <secret>`

**Parameters:**
- `secret`: ESP-NOW bridge secret, 1-15 characters

**Default:** Varies by board

This setting is used only by `bridge.format wrapped`; raw mode ignores it.

---

#### View the bootloader version (nRF52 only)
**Usage:** `get bootloader.ver`

Applies to every nRF52 board/role that exposes this getter, including Companion,
repeater, and room-server firmware. It reads the installed bootloader region
selected by the MBR/UICR boot address and bounded by the chip's flash geometry;
it does not search application firmware or staged update files.

The reader preserves a complete Adafruit/UF2 version string, including OTAFIX,
preview, and vendor/build suffixes. Manufacturer two-component versions such as
Seeed's `1.00` are preserved without inventing a patch number. If that string is
absent or ambiguous, it uses the existing whole-image CRC and unique BLMF/BLM2
validation to read the embedded OTAFIX version (for example, `> OTAFIX2.4.6`). Some older release
images, including MeshTower V2 SD 2.4.6, omit the UF2 text entirely. As a last
OTAFIX-specific fallback, it accepts one unambiguous standalone version token
such as `v0.11.0-OTAFIX2.4.9` from only the first 40 KiB of the identified
bootloader region. A corrupt canonical bootloader manifest cannot be bypassed
by that printable token. The Adafruit core's startup-captured version remains
the final fallback and is explicitly labelled `(base)`; it cannot identify an
OTAFIX/fork revision.

`unknown` remains possible for a bootloader exposing none of these version
sources, invalid geometry/vectors, or unresolved ambiguity. There is no universal
version field shared by every third-party bootloader. This is diagnostic only:
displaying a version neither grants OTA capability nor bypasses install checks.
The fix is in MeshCore application firmware; installing another bootloader alone
does not update an older application's version reader.

See the [stock bootloader audit](test-results/bootloader_version_stock_audit.md) for tested
manufacturer images and the boards still requiring untouched factory dumps.

---

#### View power management support
**Usage:** `get pwrmgt.support`

---

#### View the current power source
**Usage:** `get pwrmgt.source`

**Note:** Returns an error on boards without power management support.

---

#### View the boot reset and shutdown reasons
**Usage:** `get pwrmgt.bootreason`

**Note:** Returns an error on boards without power management support.

---

#### View the boot voltage
**Usage:** `get pwrmgt.bootmv`

**Note:** Returns an error on boards without power management support.

---

#### Configure battery charge voltage

T-Beam SX1262/SX1276 and T-Beam S3 Supreme support persistent charger-voltage
selection through the fitted AXP192 or AXP2101 PMU:

```text
get charge.voltage.options
get charge.voltage
set charge.voltage 4.1
```

Values are exact supported targets in volts. Neither T-Beam PMU supports 3.65 V.
Heltec Mesh Solar's CN3795 charge voltage is set by its Li-ion or LiFePO4 hardware
version, so these commands report that limitation without changing BMS settings.
See [charge-voltage behavior and supported targets](charge_voltage.md).

#### Configure nRF52 battery protection
**Usage:** `get pwrmgt.bootlock`, `set pwrmgt.bootlock <2500-4200>`

**Usage:** `get pwrmgt.cutoff`, `set pwrmgt.cutoff <off|2500-4200>`

Values are battery millivolts and are saved in internal storage. The boot lock
defaults to each board's compiled threshold and takes effect on the next boot.
Boards whose compiled boot lock is disabled also accept `set pwrmgt.bootlock off`.
The running cutoff
defaults to `off`; when enabled, three valid low readings at 30-second
intervals shut the node down. Both checks are bypassed while externally
powered. A nonzero cutoff cannot exceed the boot lock, and the boot lock
cannot be disabled while a cutoff is active. A fixed 2000 mV pre-check
protects flash on boards with a nonzero compiled boot lock before the saved
setting can be loaded. In the `custom` profile, the lower limit for the boot
lock and running cutoff is 2000 mV. Firmware warns when any saved voltage is
below 2500 mV; that experimental range may brown out the board or interrupt
flash writes. These commands are available on nRF52 boards with
`NRF52_POWER_MANAGEMENT` and a boot-voltage configuration, in roles with a
text CLI. The KISS modem has no text CLI for changing these settings.

`battery.profile` selects saved voltage-to-percentage endpoints and protection
settings. `liion` uses 3000 mV = 0%, 4200 mV = 100%, the board's original boot
lock, and running cutoff off. `lifepo4` uses 2700 mV = 0%, 3550 mV = 100%,
2900 mV boot lock, and 2700 mV running cutoff. `custom` starts with the current
values and allows endpoint overrides. These percentages are simple voltage
estimates; they are not an accurate state-of-charge measurement under load.

**WARNING: SELECTING LIFEPO4 DOES NOT CHANGE ANY BOARD'S CHARGING CIRCUIT. A
4.2 V LI-ION CHARGER WILL STILL CHARGE TOWARD 4.2 V AND CAN DESTROY A LIFEPO4
CELL. USE A LIFEPO4-COMPATIBLE CHARGER OR DISABLE/ISOLATE ON-BOARD CHARGING
BEFORE CONNECTING A LIFEPO4 CELL.** Check the charging hardware on every board
before using this profile; firmware battery-profile and cutoff settings cannot
make a Li-ion charger safe for LiFePO4.

```text
get battery.profile
set battery.profile lifepo4
set battery.profile liion
set battery.profile custom
get battery.empty
set battery.empty 2700
get battery.full
set battery.full 3550
get adc.multiplier
set adc.multiplier 1.050
```

The custom empty/full endpoints may be 2000-4200 mV and must be at least 100 mV
apart. Setting either endpoint automatically selects `custom`; boot lock and
cutoff remain separately adjustable. `get pwrmgt.bootlock` and
`get pwrmgt.cutoff` show the values in force after selecting a profile.

For a RAK3401/RAK13302 LiFePO4 cell:

```text
set battery.profile lifepo4
```

For example, the
[RAK19007 base-board datasheet](https://docs.rakwireless.com/product-categories/wisblock/rak19007/datasheet/)
specifies 4.2 V charging for a Li-ion battery. The profile does not change it.

The 2.7 V cutoff is a cell-protection choice, not a guarantee of 1 W RF
output. The [RAK13302 datasheet](https://docs.rakwireless.com/product-categories/wisblock/rak13302/datasheet-vd/)
specifies 3.0 V minimum at VBAT and lists 5 V / 1 A peak for 30 dBm output.
Use a cutoff at or above 3.0 V if keeping the radio within its published
battery-input range matters; verify the actual voltage under transmit load.

The shutdown wake comparator has a separate fixed hardware reference. Changing
these millivolt settings does not change that wake threshold; a unit may need
to charge above it or have USB power restored before it wakes.

---

### Ethernet (when Ethernet support is compiled in)

Ethernet support is available on RAK4631 boards with a RAK13800 (W5100S)
Ethernet module. Use the `_ethernet` firmware variants (for example,
`RAK_4631_repeater_ethernet`) for the normal Ethernet-only configuration.

`RAK_4631_companion_radio_full` is a distinct combined profile: it can compile
both physical RAK13800 Ethernet and an optional RAK15002 SD mOTA archive. They
share CS26, so they are mutually exclusive. In that Full profile Ethernet is
off at boot and is deliberately controlled only for the current boot:

```text
get eth
set eth on
set eth off
get sdcard free
ota cache [on|off]
```

SD-capable nRF52 Companion builds also share the infrastructure SD commands:
`get sdcard ls [page]`, `get sdcard dir [page]`, maintenance history, and
`set sdcard format|erase [--force]`. Bare `get sdcard` reports maintenance
history; use `get sdcard free` for capacity. See the
[SD command details and ownership guards](test-results/companion_cli_parity_2026-10-23.md#shared-sd-commands).

Before `set eth on`, remove the SD card and finish/cancel any active OTA
capture, transfer, serving, or host-folder work. The command rejects an
inserted card or a busy archive instead of taking the shared bus. Turn
Ethernet off before accessing or inserting the card. An insertion while
Ethernet is live fails closed and still needs `set eth off`; it is not a
supported hot-swap flow. This combined hardware path is not yet qualified.

The SD-capable Ethernet-room receiver,
`RAK_4631_room_server_ethernet_lora_ota_no_external_sensors`, uses the same
shared CS26 safety rule but has its own cooperative Ethernet control. It starts
off on every reboot and is never persisted. From USB or an already established
Telnet session, use either spelling:

```text
eth on
# or: set eth on
eth.status
eth off
```

`eth on` rejects a fitted card, active/cache/staged OTA work, or a retained NOR
owner. `eth off` waits for a safe release and refuses while OTA work is busy.
Remove the card before enabling Ethernet and do not insert it while Ethernet
is active.

---

#### View Ethernet connection status
**Usage:**
- `eth.status`

**Output:**
- `ETH: <ip>:<port>` when connected (e.g. `ETH: 192.168.1.50:23`)
- `ETH: not connected` when Ethernet is not active

**Notes:**
- Available on repeater and room server firmware only. The normal
  `_ethernet` Companion image does not expose a CLI; the combined RAK4631 Full
  profile above instead exposes `get eth` / `set eth on|off`.
- The normal `_ethernet` variants obtain an IP address via DHCP automatically
  on boot. The combined RAK4631 Full profile does not: Ethernet starts off
  until `set eth on` succeeds.
- A TCP server listens on port 23 (default) for CLI connections.
- Connect with any TCP client (e.g. `nc`, PuTTY) to access the same CLI available over serial.

---
