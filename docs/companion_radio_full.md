# Full Companion

`companion_radio_full` combines every Companion transport available on its
platform and acts as a host-backed LoRa mOTA source for updating other nodes.
The full Companion is deliberately not a LoRa OTA destination: it has no
firmware staging store, refuses `ota install`, and never advertises its own
firmware as an mOTA image.

| Capability | ESP32 full | nRF52 full |
| --- | --- | --- |
| USB Binary Companion | Yes | Yes |
| BLE Binary Companion | Yes | Yes |
| USB ASCII terminal | Yes | Yes |
| Dedicated USB plaintext logging | Qualified native-USB ESP32-S3 profiles | Yes |
| Host-backed LoRa mOTA source | WiFi TCP 5001 | Exclusive USB mode or encrypted BLE |
| WiFi Companion/WebConfig | Yes | No - nRF52840 has no WiFi |
| Hardware serial Companion | On targets with assigned serial pins | On targets with assigned serial pins |
| Ethernet Companion | On targets with an Ethernet module | On RAK4631 with RAK13800 |
| LoRa self-update | No | No |

## Build and install

The target is synthesized by `build.sh` only for an exact board recipe that has
passed the combined-transport size check:

- The normal automatic path requires matching WiFi, USB, and BLE recipes on
  ESP32, or matching USB and BLE recipes on nRF52.
- A measured qualification list also promotes an exact BLE recipe when the
  same board can safely add its platform's remaining transports. It never
  substitutes the pin map or peripherals from another board.

The measured ESP32 additions are M5Stack Unit C6L, Heltec Wireless Tracker,
Wireless Paper, E213, and CT62; LilyGo T3S3 SX1262/SX1276, T-Deck, TETH Elite,
classic T-Beam SX1262/SX1276, and T-Beam S3 Supreme; Ebyte EoRa-S3;
Meshadventurer SX1262/SX1268; and XIAO S3. The measured nRF52 additions are
GAT562 Mesh Watch13, LilyGo T-Echo Lite, LilyGo T-Impulse Plus, and Wio Tracker
L1 E-Ink. Their old transport-specific names remain available for explicit
compatibility builds, but the Full image is the canonical release artifact.

List the available targets:

```bash
bash build.sh list | grep companion_radio_full
```

Build by using one exact listed name:

```bash
bash build.sh build-firmware heltec_v4_r8_companion_radio_full \
  --firmware-version v1.17.0

bash build.sh build-firmware RAK_4631_companion_radio_full \
  --firmware-version v1.17.0
```

To build every canonical full Companion target, select the corresponding
interactive menu item or run:

```bash
bash build.sh build-full-companion-firmwares \
  --firmware-version v1.17.0
```

Canonical Companion bulk builds also omit legacy `_ps` and `_femoff` aliases.
Power saving and controllable FEM receive gain are persisted runtime settings;
the old names remain available through an explicit `build-firmware` command
for compatibility. Full Companion replaces separate USB, BLE, ordinary WiFi,
hardware-serial, Ethernet Companion, Terminal Chat, and USB-only packet-logging
release artifacts whenever the exact board supports those combined transports.
Direct builds of the legacy targets remain available. RAK4631 repeater and room
server Ethernet builds remain separate because they are different standalone
roles, not Companion transports. Dual-CDC builds separate framed traffic and logs;
single-TTY builds make those modes mutually exclusive. In WebConfig, use the
**FEM RX boost** switch. From the text terminal (USB, or TCP 5002 on ESP32), use:

```text
get radio.rxgain
set radio.rxgain off
set radio.rxgain on
get radio.fem.rxgain
set radio.fem.rxgain off
set radio.fem.rxgain on
get radio.fem.txgain
set radio.fem.txgain off
set radio.fem.txgain on
```

`radio.rxgain` controls the radio chip's boosted receive-gain mode; the FEM
commands control the external receive and transmit paths. The selected states
are applied immediately and retained after reboot. FEM TX gain is reported as
unsupported on boards without software-selectable PA gain.

SSD1306 display builds also persist a runtime orientation. This replaces the
separate rotated Full Companion release image:

```text
get display.rotation
set display.rotation 90
set display.rotation 180
set display.rotation 270
set display.rotation 0
```

`0` resets the screen to that board's compiled default orientation.

Heltec E290 and T190 use one `usb_ble` Companion artifact for simultaneous USB
and BLE rather than publishing separate USB-only and BLE-only images.

Device power saving is separate from LoRa RXPS. It can be changed in WebConfig
with the **Device power saving** switch or from the text terminal:

```text
powersaving
powersaving on
powersaving off
```

On ESP32, WiFi modem power saving is a third independent setting. Select it in
the WebConfig **WiFi** card, or use the Full Companion text terminal:

```text
get wifi.ssid
get wifi.status
get wifi.powersave
get wifi.cli
get webui
set wifi.ssid MyNetwork
set wifi.pwd my-password
set wifi.powersave min
set wifi.powersave max
set wifi.cli on
start webconfig
stop webconfig
```

SSID and password writes return their reply first, then restart the Companion
WiFi station with the saved credentials. A TCP terminal therefore disconnects
shortly after either write; reconnect to the IP reported by the new network.
The password is write-only and is masked while it is entered over USB.

The normal binary Companion connection can also read or write this setting over
USB, BLE, or TCP port 5000 without entering terminal mode. The saved values are
`0` for `min`, `1` for `none`, and `2` for `max`; see the
[Companion protocol](./companion_protocol.md#commands). Full Companion rejects
`none` because simultaneous WiFi and BLE require modem sleep. Fresh Cascade
builds select `min`, and an existing saved selection takes precedence.

On radios with RX duty-cycle support, WebConfig and the text terminal also
expose the persisted RXPS setting:

```text
get radio.rxps
set radio.rxps off
set radio.rxps on
set radio.rxps level 8 preamble 16
set radio.rxps 65625 60000
```

Fresh Cascade-profile Full Companion builds start with RXPS on at level 8 and
a 16-symbol preamble. Changing it takes effect immediately and remains selected
after reboot.

Companion firmware defaults device power saving to on. Version 1.17.1.2 also
turns it on once when upgrading an older Companion preference file, including
one written by the short-lived default-off regression. After that one-time
migration, an explicit `powersaving off` selection remains persistent.

On ESP32, enabling it lowers the CPU clock to 80 MHz, enables idle yielding,
and enables the configured GPS duty cycle. Disabling it restores the normal CPU
clock and keeps GPS awake. Full Companion transports remain available in both
states; WiFi modem sleep stays enabled when BLE is present because coexistence
requires it. Changing device power saving does not overwrite the saved WiFi
power-save mode. While a native-USB host is enumerated, the platform sleep
attempt is held off so USB CDC remains responsive; detaching the host releases
that guard. CPU, radio-modem, and GPS power-saving settings remain active, and
USB power from a charger alone does not create a Companion session. The
selected state is retained after reboot.

On the LilyGo T-Beam 1W Full Companion, press the physical `BOOT` button once
to turn the ESP32 WiFi radio and all WiFi services off or on. The screen confirms
`WiFi: OFF` or `WiFi: ON`, and the selected state is retained after reboot. When
WiFi is off, TCP ports 5000-5002, WebConfig, and MQTT are stopped; USB, BLE, the
display, GPS, and LoRa continue to operate. Press BOOT again to restore WiFi,
including the saved station or setup-AP mode. On boot, BLE starts two seconds
after WiFi/WebConfig so their peak startup allocations do not overlap.

Artifacts are written to `out/` by default.

Full Companion behavior is selected with independent capability macros for
TempRadio, the OTA CLI, the TCP terminal, USB folder seeding, and memory
diagnostics. The legacy `COMPANION_RADIO_FULL` flag remains an input for older
target recipes, but application behavior no longer uses that umbrella as an
unrelated compile guard. In particular, ESP32 WiFi/WebConfig terminal controls
are compiled from their actual WiFi/WebConfig capability, and BLE is always
started before WiFi on any combined ESP32 WiFi+BLE Companion to avoid heap
fragmentation. Compile-time prerequisite checks reject inconsistent feature
flags. After linking, the capability sidecar verifies USB, BLE, the OTA CLI,
TempRadio, and each platform's host-folder transport; it also verifies the TCP
terminal, WebConfig, and WiFi seeder on ESP32, plus dedicated logging whenever
the board uses dual CDC.

On 4 MB ESP32 boards, the full target uses a single 3 MB application partition
so WiFi, BLE, WebConfig, and source-only mOTA fit together. The T-Beam 1W Full
Companion uses that same LilyGo factory-compatible boot layout on its 16 MB
flash because this source-only role does not install updates into a second app
slot. Flash the generated `-merged.bin` when first installing this partition
layout. Other boards with 8 MB or more retain dual application partitions.
Heltec V2 and TLora V2 use 100 contacts, 8 group channels, and a 16-frame offline
queue in this combined profile because of internal DRAM limits. Meshadventurer
SX1262 and SX1268 retain 160 contacts, use 30 group channels, and use a
64-frame queue. That is the smallest measured reduction which cleared their
classic ESP32 internal-DRAM link limit; their ordinary transport-specific
images retain 160 contacts, 40 channels, and 128 queued frames.

Full Companions normally retain 256 pending Companion message frames. ESP32
boards with configured PSRAM retain 512 and allocate that queue from PSRAM
before WiFi and BLE start. If PSRAM is unavailable at runtime, allocation falls
back through 256 and 128 frames, then to a 16-frame internal buffer. The Full
Companion startup memory line reports the capacity actually allocated as
`offline_queue=<frames>`. The queue is volatile and shared by all channels and
direct messages; it is not flash-backed history. See
[Companion offline message queue](./companion_offline_queue.md) for all platform
defaults and full-queue behavior.

The nRF52 target inherits the board's ordinary USB Companion installation
format and adds BLE plus the serial mOTA source. It does not enable an SD cache
or any other board-specific storage behavior; host files are streamed as they
are requested. Its image is bounded by the board's normal application region,
not the smaller OTAFIX in-place workspace reserved for firmware that can update
itself.

## Interfaces

| Platform | Interface | Purpose |
| --- | --- | --- |
| Both | USB, 115200 baud | Binary Companion by default; terminal switch available |
| Both | BLE | Binary Companion; display builds show a random session PIN, while headless builds default to `123456` |
| ESP32 | TCP 5000 | Binary Companion over WiFi |
| ESP32 | HTTP 80 | Companion WebConfig and first-boot WiFi setup |
| ESP32 | TCP 5001 | Host `.mota` folder from `motatool serve --tcp` |
| ESP32 | TCP 5002 | Full Companion text terminal; same role commands as the USB terminal |
| nRF52 | USB mOTA mode | Host `.mota` folder from `motatool serve --serial` |
| nRF52 | Encrypted BLE mOTA service | Paired phone/tablet/Linux host `.mota` catalog |

Delivery-required replies are returned only to the interface which supplied the
latest command. A contact-list stream keeps that route locked from
`CONTACTS_START` through `END_OF_CONTACTS`; commands waiting on another
interface are read after the stream finishes. Best-effort asynchronous
observations such as adverts remain broadcast so passive clients can refresh
their views. Companion session state is device-wide, so use one active
Companion application at a time. On nRF52, BLE remains available while USB is
in terminal or mOTA mode.

USB Binary output is queued as complete length-prefixed frames. Temporary CDC
or UART backpressure pauses the contact stream; a frame may drain through a
smaller hardware FIFO in ordered chunks, but its remainder is retained and no
later frame can interleave with it or cause it to be discarded.

When a BLE client requests pairing, a display-equipped build wakes the screen,
switches to the first home page, and keeps the active six-digit PIN visible
until Bluetooth connects or the two-minute pairing window expires. USB, WiFi,
Ethernet, and hardware-serial connections do not suppress this screen. With no
saved BLE PIN, display builds generate a new PIN at boot; builds without a
physical display use `123456`. A PIN saved through the Companion protocol takes
effect after reboot.

The Bluetooth device name is independently configurable. In the text terminal,
use `get bluetooth.name` and `set bluetooth.name <name>`; use
`set bluetooth.name default` to restore `MeshCore-<node name>`. The WebConfig
Node card exposes the same optional field on Bluetooth-capable ESP32 builds.
Custom names replace the complete Bluetooth label rather than inheriting the
prefix, accept up to 31 valid UTF-8 bytes, and take effect after reboot. This
does not change the node's mesh advertisement name.

ESP32 ports 5000, 5001, 5002, and WebConfig have no independent login layer.
Expose them only on a trusted LAN or temporary setup network. See
[WiFi setup](./WiFi.md) for credential setup and reconnect behavior.

## USB Binary and text terminal modes

USB starts in Binary mode for MeshCore apps and `meshcli`:

```bash
meshcli -s /dev/ttyACM0 -b 115200 ver
```

Open the port with the terminal start token sent automatically:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring '+++MESHCORE-TERM-START' \
  /dev/ttyACM0
```

The input map prevents any Binary Companion control bytes received during the
mode transition from changing the local terminal's character set or display
state while leaving UTF-8 emoji intact. The banner confirms that terminal mode
is active; do not enter the start token again after it appears.

The terminal supports Companion chat commands, including `channels`,
`channel <name-or-slot> <message>`, remote administration with
`login <admin-password>` and `cmd <remote-command>`, and routed
`trace [recipient-name-or-prefix]`, plus local `ota`, `tempradio`, and
`normalradio` controls. ESP32 builds also provide local
WiFi credential, status, WebConfig, CLI-tab, and power-save controls. Every
Full Companion provides persistent `get/set usb.logging` and starts with
logging off on a fresh installation.

### Single USB serial port

On an ESP32 Full Companion without dual CDC, interface `00` has two exclusive
modes. It starts as framed Binary Companion. Enter its text terminal with
`+++MESHCORE-TERM-START`, then run `set usb.logging on`; the same TTY emits
plaintext packet/debug logs and continues accepting CLI commands, including
`set usb.logging off`. Turning it off sends the command reply and then returns
that TTY to Binary Companion automatically, including on USB-UART bridges that
cannot detect a cable disconnect. A saved logging-on preference boots directly
into this input-capable logging terminal. BLE and Wi-Fi Companion remain
available while USB is logging.

### Dual USB serial ports

Current nRF52 Full Companion and qualified native-USB ESP32-S3 Full Companion
firmware can expose two CDC ACM serial interfaces on one physical USB cable:

- USB interface `00` is the normal Binary Companion, text terminal, and serial
  mOTA source port. It is always present.
- USB interface `02` is the optional write-only plaintext packet/debug logging
  port. Host input on this interface is ignored and cannot invoke firmware
  commands.

A fresh Full Companion starts with USB logging off and therefore enumerates
only interface `00`. Use `set usb.logging on` to save logging on; the reply says
that a reboot is required. Use `set usb.logging on reboot` to save it and have
the node reboot automatically after the reply. The second interface appears
after that reboot. Likewise, `set usb.logging off reboot` removes interface
`02`. The optional `reboot` word is accepted only in these exact command forms
and triggers a reboot only when the descriptor actually needs to change.

These are Full Companion **text-terminal** commands. The superficially similar
`meshcli ... get usb.logging` command uses the Binary Companion parameter
registry and can report `Unknown var usb.logging`; it does not forward that
line to the text terminal. Enter terminal mode on interface `00` with the
`+++MESHCORE-TERM-START` token as described above, then issue the command.

When logging is on, Linux normally shows two `/dev/ttyACM*` devices. Match the stable
`/dev/serial/by-id/*-if00` and `*-if02` links, or use a udev rule matching
`ID_USB_INTERFACE_NUM`, rather than assuming which tty number is assigned. On
Windows they appear as two COM ports; identify them by USB interface instead of
depending on a particular COM number. The nRF52 bootloader temporarily exposes
its normal DFU serial interface during an update. Qualified S3 boards
temporarily expose the ESP32-S3 ROM USB-JTAG serial port during a wired flash.

Dual-CDC ESP32-S3 targets are Heltec V4, T-Beam 1W, Station G2/G3, XIAO S3
WIO, Heltec Tracker V2, Meshnology W12, and Nibble Screen/Zero Connect. The
base Heltec V4 profile has completed live two-interface, ROM-flashing, and
logging-off one-interface validation. Full recipes with only one usable TTY
still replace separate transport images; they use the exclusive terminal/log
mode above because firmware cannot add a second interface to a USB-UART bridge
or a single-port USB peripheral.

Every ESP32-S3 Full Companion image uses DIO flash mode, including the RAK3112
and RC32 profiles that do not expose dual CDC. The S3 ROM supports DIO while
loading the software bootloader, and some flash configurations fail before the
application starts when a merged image inherits QIO. DIO trades some maximum
flash-read throughput for compatibility; it does not change a board's PSRAM
type or any ordinary non-Full firmware profile.

Point MeshCore Companion software, `meshcli`, and `motatool` at interface `00`.
When enabled and rebooted, point a plaintext reader or USB-connected MQTT
service at interface `02`.

ESP32 Full Companion exposes this same text terminal on TCP port 5002. Connect
with `nc DEVICE_IP 5002`; no USB control token is needed. USB terminal mode and
the TCP terminal share recipient, login, command, trace, and display state, so
only one may own the terminal at a time. Entering USB terminal mode closes an
active TCP terminal session. Disconnecting TCP clears pending terminal-only
state without cancelling Binary Companion delivery or radio retries.

Both terminal transports accept `reboot`. The reply is sent first and the
device reboots one second later, so a script can distinguish an accepted reboot
from an abruptly lost connection.

Port 5002 is plaintext and has no device-local login gate. A remote-admin
password entered with `login` is sent across the LAN connection as typed even
though the terminal does not echo it. Use port 5002 only on a trusted LAN or a
temporary setup network.

For example:

```text
channels
channel #rgdata Hello from Eugene 👋
show
show channels on
to Hilltop Repeater
path A1B2C3,D4E5F6
path 7773D0 7E7662
login my-admin-password
cmd ver
trace
```

The terminal `list [n]` command displays favorite contacts first and orders
each favorite/non-favorite group by its most recent advertisement. This does
not alter the binary Companion contact-list protocol.

Unsolicited terminal output starts in a quiet mode: advertisements and
ordinary channel messages are hidden, while `#emergency` messages remain
visible. Use `show adverts on|off`, `show channels on|off`, and
`show emergency on|off` to control each category independently; plain `show`
reports their state. These runtime filters affect terminal printing only and
reset to their defaults after reboot.

The `to` command selects the remote-administration target. `path` shows its
saved outgoing route; `path direct`, `path clear`, or a list separated by
spaces, commas, or both changes the route used by subsequent `login`, `send`,
and `cmd` commands. Every hop must use the same 2-, 4-, or 6-digit hexadecimal
width. Login passwords are masked during entry and limited by the radio
protocol to 15 UTF-8 bytes. Wait for the asynchronous login result before using
`cmd`; command replies appear as `CLI -> from <name>` and use a response window
of 300% of the route estimate. `DIRECT via path <hop,...>` displays the exact
saved prefixes copied into the packet. Remote ACL permissions determine which
commands the target accepts. The matching reply reports its round-trip time
from local queueing through result reception, including radio transit and
remote execution. Only one terminal `cmd` can be pending at a time.

Incoming unicast replies are labeled `ROUTED`. Their exact return prefixes are
not available at the destination because each forwarder consumes its prefix;
use `trace` to verify the return route.

With no argument, `trace` uses the current `to` recipient. A name-prefix
argument traces that contact directly without changing the current recipient.
The contact must already have a known direct path; results show the SNR at each
hop, or a timeout if the round trip does not return.

An explicit route can use 1-, 2-, or 4-byte hexadecimal prefixes. Spaces,
commas, and mixed separators are accepted:

```text
trace path 1 12 34 56 34 12
trace path 2 1234,ABCD,5678,ABCD,1234
trace path 4 12345678, ABCDEF01 89ABCDEF, ABCDEF01,12345678
```

The entered route must include both the outward and return prefixes. Exact
three-byte traces are not supported.

Return to Binary mode with:

```text
+++MESHCORE-TERM-STOP
```

Closing the USB data connection also resets the port to Binary mode. A
different baud rate, including 57600, does not select ASCII mode.

On an ESP32 Full Companion built with `OTA_FOLDER_SERIAL`, `motatool` can keep
that terminal session open as an mOTA folder source when WiFi is unavailable:

```sh
motatool serve --serial /dev/ttyACM0 --companion-terminal --dir ./motas -v
```

The explicit flag is required because an ESP32 Companion starts the same USB
port in binary Companion mode. TCP port 5001 remains the preferred unattended
source transport.

## nRF52 USB mOTA mode

The nRF52 full target has a third, exclusive USB mode for the host folder.
Unmodified `motatool serve --serial` already sends `ota folder on` when it
opens the port. The Binary parser recognizes that exact idle control sequence,
stops USB Binary traffic, and attaches the serial folder source. The sequence
is not examined inside a framed Binary Companion packet.

While mOTA mode owns USB:

- mOTA manifests and blocks stream from the computer on demand.
- USB Binary and the USB text terminal are unavailable.
- BLE Binary Companion remains available.
- `motatool` sending `ota folder off`, or disconnecting the USB data session,
  detaches the folder and restores Binary mode.

No manual mode token or modified `motatool` build is required.

## nRF52 Bluetooth mOTA source

Protocol v14 also lets a phone, tablet, or Bluetooth-capable Linux host feed
the `.mota` catalog to an nRF52 Full Companion. Normal Companion commands stay
on the Nordic UART service. Firmware data uses a separate GATT service, so
binary app traffic cannot be mistaken for a firmware block.

The client must pair with the Companion PIN, subscribe to the mOTA Device
Request characteristic, and send `CMD_BLE_MOTA_SOURCE` action `start` over the
normal Binary Companion connection. The source is available only while that
encrypted MITM-authenticated connection remains active. Disconnecting,
unsubscribing, overflowing a frame, or receiving malformed data automatically
detaches the catalog. USB and BLE source modes are mutually exclusive.

The included Raspberry Pi reference client validates each `.mota` with
`motatool`, schedules the local TempRadio window, serves until interrupted,
then detaches and restores the normal radio tuple:

```bash
python3 tools/ble_mota/ble_mota_seeder.py \
  --device MeshCore-MyCompanion \
  --dir ./motas \
  --local 'tempradio 909.950,250,5,5,120'
```

Prefer this relative `tempradio` form when the phone/Pi and radio clocks may
disagree. It starts a duration on the Companion and does not compare their
wall clocks. Use the absolute `tempradioat` scheduler only after synchronizing
the participating nodes.

Use `--pair` when the Linux host has not already bonded. BlueZ must have an
agent capable of entering or confirming the six-digit PIN. Use `--source
status` without `--dir` for a read-only channel/status check. The complete
UUID, frame, action, and status definitions are in the
[Companion protocol](./companion_protocol.md#bluetooth-lora-mota-source).

This reference process stands in for the phone application. A mobile app can
use the same sequence while retaining its normal contact and Repeater Admin
UI: log in to the destination, put each required node on the same bounded
TempRadio tuple, start the local Bluetooth catalog, then send the normal remote
`ota ls`, `ota pull`, and `ota install` commands. The destination still checks
container geometry, hardware identity, hashes, signature policy, and the
OTAFIX bootloader before installation.

## Serve mOTA images manually

First put the destination, required relays, controller, and source on the same
bounded TempRadio tuple. The example frequency below is not legal everywhere;
choose a legal tuple supported by every participating radio.

### ESP32 source

Use the local console to start TempRadio:

```bash
nc 192.168.1.50 5002
```

```text
tempradio 909.950,250,5,5,120
ota status
```

Then start the dedicated TCP seeder:

```bash
motatool serve --dir ./motas --tcp 192.168.1.50:5001 -v
```

### nRF52 source

Use the USB terminal briefly to schedule TempRadio, then return to Binary mode
and close the terminal:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring '+++MESHCORE-TERM-START' \
  /dev/ttyACM1
```

```text
tempradio 909.950,250,5,5,120
+++MESHCORE-TERM-STOP
```

After sending the stop token, exit `picocom` with Ctrl-A, Ctrl-X.

Start the serial seeder on that same port:

```bash
motatool serve --dir ./motas --serial /dev/ttyACM1 --baud 115200 -v
```

`motatool` switches the port into mOTA mode automatically. Stop it with
Ctrl-C to detach the folder. Reopen the terminal and use `normalradio` if the
source should return early; otherwise the saved radio settings return when the
bounded window expires.

As a cable-free alternative, keep the normal Companion BLE session open and
run the Bluetooth reference client shown in the nRF52 Bluetooth section. Do
not run the USB seeder at the same time.

Both platforms intentionally refuse firmware installation commands such as:

```text
ota pull <id> flash
ota install
ota dev ...
```

## Script a complete update

The Bash and PowerShell wrappers accept a release ZIP or ready `.mota`, set up
TempRadio, run `motatool`, monitor the exact image, install it on the
destination, and restore the radio path. Use a separate Companion as the
controller.

For an ESP32 full source:

```bash
export MESHCORE_ADMIN_PASSWORD='target-admin-password'

./tools/lora_ota/lora_ota.sh ./release.zip "Roof Node" \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.50:5001 \
  --source-cli-tcp 192.168.1.50:5002
```

For an nRF52 full source, the script automatically detects the token-switched
terminal and uses the same source port sequentially for control and seeding:

```bash
export MESHCORE_ADMIN_PASSWORD='target-admin-password'

./tools/lora_ota/lora_ota.sh ./release.zip "Roof Node" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

```powershell
$env:MESHCORE_ADMIN_PASSWORD = 'target-admin-password'

& .\tools\lora_ota\lora_ota.ps1 '.\release.zip' 'Roof Node' `
  --controller-serial COM7 `
  --source-serial COM8
```

See the [start-to-finish LoRa OTA guide](./lora_ota_automation.md) for package
selection, nRF52 in-place deltas, relays, trust checks, and recovery behavior.
