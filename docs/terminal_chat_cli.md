# Terminal Chat CLI

Below are the commands you can enter into the Terminal Chat clients:

## Companion WiFi browser terminal

On ESP32 WiFi Companions with WebConfig, open the node's LAN IP address and
select **CLI**. The browser runs the same commands as the USB terminal and
Full Companion's TCP terminal on port 5002, including contact import, contact
selection, messages, remote login/commands, and delayed RF replies. Run `help`
to list commands compiled into the device. Enable the tab with
`set wifi.cli on`; the open setup AP does not expose it.

For example, copy the complete `meshcore://...` string from `card` on another
Companion, then run these commands on the receiving Companion:

```text
import meshcore://<full-contact-card-data>
list
to <contact-name-or-prefix>
```

Replace the placeholders; a bare public key is not a contact card. Import is
queued for signature validation, so use `list` to confirm the contact appears.
Explicit import works with manual contact addition enabled and bypasses the
automatic discovery type/hop filters. It still respects contact storage limits;
a full contact table follows the configured overwrite policy. `to`
remains selected for subsequent `send`, `path`, `login`, and `cmd` commands
within this terminal session.

Only one USB, TCP, or browser text terminal owns the session at a time. An
idle startup USB prompt can be borrowed by WiFi. Use `disconnect` or the
browser's **disconnect** button to release it. Closing the page releases the
session; if the connection disappears, it expires after 60 seconds without a
poll. Long replies page into the browser automatically, and incoming replies
continue appearing after a command finishes. Terminal scrollback starts at
4 KiB and can grow to 32 KiB for unread output, preferring PSRAM; it shrinks
after reading and is freed when the session expires. If output exceeds the
available buffer while unread, the browser reports the missing output.

USB protocol-switch and USB MOTA ownership commands still apply to the USB
connection. Use the WiFi MOTA seeder on port 5001 for a host folder over WiFi.

## Local maintenance commands

Local means a connection directly to the node: USB, BLE or WiFi/Ethernet
Companion protocol, a TCP terminal, or the browser CLI on the LAN. These
connections can use the following commands when the role and build include
the corresponding feature:

| Command | Result |
|---|---|
| `stats-core`, `stats-radio`, `stats-radio-diag`, `stats-packets` | Runtime diagnostics |
| `get prv.key` | Node identity private key; Companion requires private key export enabled in the build |
| `get password` | Infrastructure admin password; Companion reports that it has no admin password |
| `erase` | Erase stored identity and settings; reboot for a fresh node |
| `get wifi.pwd` | Stored WiFi password on WiFi builds |
| `get mqttN.password`, `get mqttN.token` | Stored credentials for MQTT slot N |
| `get acl`, `log` | Infrastructure ACL or captured packet log; these stores do not exist on Companion |

The command text is the same for direct connections. A binary Companion app
can send `0x42` (`CMD_RUN_CLI_COMMAND`) followed by the ASCII command, without
switching into terminal mode. The response is `0x1D` (`RESP_CODE_CLI_REPLY`)
followed by the reply text. For example, payload `42 67 65 74 20 70 61 73 73 77
6f 72 64` runs `get password`. USB framing remains the normal Companion frame;
BLE and TCP use their existing Companion transport framing.

`set freq <MHz>` is available on these local connections and through authorized
LoRa administration, just like `set radio`. It saves the frequency without
changing bandwidth, spreading factor, or coding rate and requires a reboot to
apply. Infrastructure roles also apply the board's transmit-power limit.

Commands relayed to another node using `cmd` travel over LoRa and retain the
remote restrictions on local-only maintenance and secret reads. These local
permissions do not add an ACL or packet-log store to Companion. Use `list` or
the binary contacts operations for Companion contacts. Interactive chat
commands still use the text terminal or their corresponding binary protocol
operations.

The browser CLI requires station/LAN mode and `wifi.cli on`. Infrastructure
uses its admin login; Companion uses the trusted LAN. Configuration forms keep
password fields masked; explicit local CLI getters return their values.

## Companion USB mode

Updated 1.17.1.6 USB Companion builds, including ordinary and Full builds, start
in ASCII at 115200 baud. A valid framed app command automatically selects Binary
Companion. You can open a terminal without a start token:

```sh
picocom --baud 115200 /dev/ttyACM0
```

For older firmware or a port already in Binary mode, use:

```sh
picocom --baud 115200 \
  --imap spchex \
  --initstring $'+++MESHCORE-TERM-START\r' \
  /dev/ttyACM0
```

`--initstring` sends this exact terminal-start line automatically:

```
+++MESHCORE-TERM-START
```

The carriage return is required in Binary mode. Control tokens are recognized
only as complete CR/LF-delimited lines, so the same text embedded in unrelated
unframed input cannot switch modes accidentally.

Binary Companion frames can contain terminal control bytes. The `spchex` input
map renders those bytes as bracketed hexadecimal during the short transition
instead of allowing them to change the local terminal's character set or
display state. It leaves high-bit bytes unchanged so a UTF-8 terminal displays
emoji and non-ASCII text normally. Do not add `8bithex` unless you explicitly
want UTF-8 bytes displayed as sequences such as `[f0][9f][91][8b]`. Once the
terminal banner appears, the start sequence has already succeeded; do not
enter it again as a terminal command.

Send the following exact sequence to return to the binary protocol:

```
+++MESHCORE-TERM-STOP
```

An observable USB session reset restores ASCII after clearing the previous
client's state. Native USB with DTR can detect a terminal closing; ESP32 hardware
USB Serial/JTAG detects bus resets and physical host loss. USB-to-UART bridges
usually cannot detect a terminal closing. Use the start token on ports that
remain in Binary mode; idle time does not select ASCII.

Both modes use the same port at 115200. Selecting 57600 is not a portable mode
switch: native USB CDC devices ignore the requested baud, while USB-to-UART
devices really change the UART timing and receive corrupt data. Binary mode is
the framed Companion API used by apps and `meshcli`; close the terminal before
opening that port from an app.

All USB Companion builds automatically switch when they see a complete
`<`-prefixed Companion frame at an empty prompt. The explicit start/stop tokens
remain available. See [Companion USB CLI and binary switcher](./full_companion_usb_switcher.md)
for the state machine and limitations.

## Commands

ESP32 and nRF52 Full Companions include an offline World English Bible lookup:

```text
get John 3:16
```

This returns a single complete verse from John, using plain ASCII punctuation
with unchanged wording and capitalization, through the local USB/TCP terminal.
See [John lookup and compression](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/bible/README.md) for the
source, supported build profiles and memory costs.

```
set freq {frequency}
```
Set the saved LoRa frequency. Example: `set freq 915.8`. Available over local
connections and authorized LoRa CLI; reboot to apply.

```
set tx {tx-power-dbm}
```
Sets LoRa-chip transmit power in dBm. The firmware rejects values outside the
radio/board limit; external-PA boards can have a lower input-power ceiling than
the radio chip itself.

```
set name {name}
```
Sets your advertisement name.

```
get bluetooth.name
set bluetooth.name {name|default}
```
Shows or changes the complete Bluetooth device name without changing the mesh
advertisement identity. A custom name may contain spaces and is limited to 31
valid UTF-8 bytes. `default` restores `MeshCore-<node name>`. The saved change
takes effect after reboot. `get ble.name` and `set ble.name` are short aliases.

```
get bluetooth.mac
set bluetooth.mac {address|random|random-every-boot|random-after-connect|default}
get bluetooth.stealth
set bluetooth.stealth {on|off}
```
Shows or changes the Bluetooth identity on Companion builds with BLE. A
literal address must be BLE random-static, such as `C2:11:22:33:44:55`.
`random` generates and saves one address; `random-every-boot` generates a new
one on each startup (`random everyboot` is also accepted).
`random-after-connect` keeps its address across unused power cycles, then
rotates on the next boot after an authenticated connection. `default` or
`clear` restores the factory address without changing the stealth flag.
Reboot, forget the old phone entry, and pair again after an address change.
`get ble.mac` and `set ble.mac` are short aliases.

The independent `bluetooth.stealth` flag defaults off and preserves the chosen
address policy. With it on, the node is discoverable until its first
authenticated pairing, then accepts only that bonded peer. Custom and saved
random addresses keep the bond across boots; rotating modes reopen pairing
when the address rotates, leaving stealth enabled. It still transmits the
directed or allowlisted packets BLE requires for reconnection. Repeating `on`
keeps the bond. Send `off`, then `on`, then reboot to reopen pairing manually.
`ble.stealth` is the short alias. Flag changes require reboot.

```
set lat {latitude}
```
Sets your advertisement map latitude. (decimal degrees)

```
set lon {longitude}
```
Sets your advertisement map longitude. (decimal degrees)

```
set dutycycle {percent}
```
Sets the transmit duty cycle limit (1-100%). Example: `set dutycycle 10` for 10%.

```
set af {air-time-factor}
```
Sets the transmit air-time-factor. Deprecated - use `set dutycycle` instead.

```
get powersaving
set powersaving {on|off}
```
Shows or changes Companion device power saving. On ESP32 this controls CPU and
GPS idle behavior; it does not change LoRa RXPS or WiFi modem sleep.

```
get usb.logging
set usb.logging {on|off} [reboot]
```
Shows or changes persistent live USB debug and packet output in an ordinary
USB-loggable Companion or Full Companion. USB Companion and Full start off on
a fresh install with logging disabled to protect framed traffic.
nRF52 Full changes its interface count after a reboot and keeps Companion on
interface `00`. Every ESP32 Full Companion has one TTY and needs no reboot:
logging uses the active text terminal, disables framed Binary Companion on
USB, and continues accepting `set usb.logging off`. Turning logging off leaves
the port in the normal ASCII terminal, matching fresh firmware. Send
`+++MESHCORE-TERM-STOP`, or let a Companion app send a valid framed probe, to
switch it to Binary Companion afterward.

```
reboot
```
Sends an acknowledgement, then reboots the Companion one second later. The
delay gives either the USB terminal or the Full Companion TCP terminal on port
5002 time to deliver the reply before its transport disappears.

```
get radio.rxps
get radio.rxps.config
set radio.rxps {off|on|level 1-10 [preamble 16|32]|rx_us sleep_us}
```
Shows or changes LoRa receive duty cycling on supported radios.
`get radio.rxps.config` also reports the saved level and preamble assumption so
automation can restore a level-based preference exactly. Fresh Cascade
builds select level 8 with a 16-symbol timing assumption. The `preamble`
argument controls the RXPS calculation; it does not change the physical wire
preamble. A configured level is the minimum: when faster SF/BW settings shorten
the timing window, firmware raises the effective level only as far as needed,
up to level 10. Starting with v1.17.1.5, SF5-SF8 packets normally use a
32-symbol physical preamble. Firmware selects 64, then 128, only when every
shorter choice fails to enable RXPS at any level. The configured values remain
unchanged, so every radio change recalculates from the saved minimum. A slower
tuple returns to that exact level and the shortest viable wire preamble.

If neither adjustment leaves enough time for the radio to wake, RXPS stays
logically enabled but receives continuously instead of rejecting the radio
setting or starting an invalid duty cycle. The Companion terminal's
`get radio.rxps` reports this as `mode=continuous-fast` and reports any
`effective-level` or `effective-preamble` adjustment;
`get radio.rxps.config` reports the persisted preference. A later compatible
SF/BW change resumes duty cycling without another RXPS command.

For the SX1262+TCXO boards tested here, these fast combinations are the useful
RXPS boundary profiles (CR does not change the RXPS preamble timing):

| SF | BW (kHz) | Wire preamble | Effective preamble | Minimum effective level | RX / sleep |
|---:|---------:|--------------:|-------------------:|------------------------:|-----------:|
| 7 | 500 | 32 | 32 | 7 | 2731 / 6101 us |
| 6 | 250 | 32 | 32 | 7 | 2731 / 6101 us |
| 5 | 125 | 32 | 32 | 7 | 2731 / 6101 us |
| 5 | 250 | 64 | 64 | 8 | 1252 / 6424 us |
| 6 | 500 | 64 | 64 | 8 | 1252 / 6424 us |
| 5 | 500 | 128 | 128 | 8 | 626 / 6398 us |
| 5 | 62.5 | 32 | 16 | 10 | 4096 / 6272 us |

The 64- and 128-symbol rows require the v1.17.1.5-or-newer adaptive-preamble
contract on every sender that may reach the RXPS receiver. Cascade/USA builds
on a Heltec V4 and WisMesh Tag (RAK4631 target) passed 16/16 packets in each
direction at both SF5/BW250/64 and SF5/BW500/128, CR5, 909.950 MHz. An older
sender makes a long-preamble timing window unsafe, so use continuous RX for a
mixed deployment. Retry packets use the same physical preamble as other
packets.

```
get wifi.powersave
set wifi.powersave {none|min|max}
```
Shows or changes the persisted WiFi modem-sleep policy on ESP32 WiFi Companion
builds. Full Companion requires at least `min` while BLE is present and rejects
`none`. The WebConfig WiFi card and the normal binary Companion protocol expose
the same setting; binary clients do not need the terminal-start token. On an
ESP32 Full Companion whose primary mesh radio is ESP-NOW, `max` is also
unavailable because maximum modem sleep can make the
station miss ESP-NOW broadcasts, which the access point does not buffer. A
previously saved `max` value is capped to and reported as `min`, and a new `max`
selection is rejected. Such a WiFi/BLE/primary-ESP-NOW build therefore uses
`min`.

```
get espnow.channel
set espnow.channel <1-13>
```
On builds whose primary mesh radio is ESP-NOW, this shows or saves the primary
radio's channel. The default is channel 1. Use only a channel permitted in
your region, and restart or power-cycle the node after changing it. Every
primary ESP-NOW peer must use the same channel.

On Full Companion, the setup AP and infrastructure-WiFi station also share
that channel, so the router's 2.4 GHz radio must remain fixed to it. Power
saving does not permit separate channels. `wifi.powersave max` is unavailable
on these ESP32 Full builds and `min` is the coexistence setting. This channel
setting is also distinct from `bridge.channel`, which controls the optional
ESP-NOW bridge transport. An updated LoRa-primary ESP-NOW bridge can join this
raw primary transport by selecting the same channel and running
`set bridge.format raw`; its default `wrapped` format remains the
bridge-to-bridge protocol.

```
get radio.rxgain
set radio.rxgain {on|off}
get radio.fem.rxgain
set radio.fem.rxgain {on|off}
get radio.fem.txgain
set radio.fem.txgain {on|off}
```
`radio.rxgain` changes the radio chip's boosted receive-gain mode. The FEM
settings control the external receive-path LNA or transmit-path gain on
supported boards. Changes are applied immediately and saved across reboots;
changing either receive-gain path also recalibrates the radio noise floor.
Boards without the respective control report it as unsupported.


```
time {epoch-secs}
```
Set the device clock using UNIX epoch seconds. Example:  time 1738242833


```
advert
```
Sends an advertisement packet

```
clock
```
Displays current time per device's clock.


```
ver
```
Shows the device version and firmware build date.

```
card
```
Displays *your* 'business card', for others to manually _import_

```
import {card}
```
Imports the given card to your contacts.

```
list {n}
```
Lists favorite contacts first, then all remaining contacts. Each group is
ordered by the most recent advertisement. Optional `{n}` limits the displayed
contacts after applying that order.

```
show
show adverts {on|off}
show channels {on|off}
show emergency {on|off}
```
Controls unsolicited receive output in the USB terminal. Plain `show` reports
the current settings, and `show {category}` reports one category. At boot,
advertisements and ordinary channel messages are hidden while `#emergency`
messages are shown. The three controls are independent, so `show channels on`
does not override `show emergency off`.

These filters affect terminal printing only. Messages still enter the offline
queue and are delivered through the binary Companion protocol. Changes remain
active when switching between terminal and binary mode and reset to their
defaults after reboot.

```
to
```
Shows the name of current recipient contact. (for subsequent 'send' commands)

```
to {name-prefix}
```
Sets the recipient to the _first_ matching contact (in 'list') by the name prefix. (ie. you don't have to type whole name)

```
path
```
Shows the saved outgoing path for the current `to` recipient. This command and
all path changes require a recipient to be selected first.

```
path direct
path clear
path {hop-hash...}
```
Sets the outgoing path used by subsequent `login`, `send`, and `cmd` commands.
`direct` selects a zero-hop route. `clear` forgets the saved route, causing the
next operation to use flood routing and allowing normal path discovery to
learn a replacement.

Explicit paths use spaces, commas, or a mixture of both between hop hashes.
Each hop must contain exactly 2, 4, or 6 hexadecimal digits, and every hop in
one path must use the same width. Whitespace and hexadecimal letter case do
not matter. The setting is saved with the selected contact.

For example:

```text
to Hilltop Repeater
path A1B2C3,D4E5F6
path 7773D0 7E7662
path
login my-admin-password
```

```
login {admin-password}
```
Sends a remote login request to the current recipient. Select a repeater,
room, or other remotely managed node with `to {name-prefix}` first. The
password is masked with `*` while it is entered and must be 1-15 UTF-8 bytes;
longer passwords are rejected instead of truncated. Login uses the route shown
by `path`: a known or explicitly set route is direct, while an unknown route is
flooded.

Login results arrive asynchronously. A successful modern response displays
the remote ACL permissions byte and server protocol level. A wrong password,
an unreachable target, or a server that does not support remote login normally
produces a timeout because those nodes do not send a rejection packet.

```
cmd {remote-command}
```
Sends CLI data to the current recipient. Wait for the login result before
sending the first command. The remote node applies its own ACL permissions,
and any reply appears asynchronously as `CLI -> from {name}`. The response
window is 300% of the route estimate. A routed send is displayed as
`DIRECT via path {hop,...}` with the exact prefixes copied into the packet; in
MeshCore, `DIRECT` is the route class for an explicit path, not a synonym for
zero hops. A matched reply also shows its local round-trip time, measured from
queueing the command through receiving the result. This includes both radio
directions and remote execution; it is not execution-only CPU time. Only one
terminal `cmd` can be pending at a time.

For example:

```text
to Hilltop Repeater
login my-admin-password
LOGIN -> Hilltop Repeater accepted (ACL permissions 0x03, server v13)
cmd ver
cmd get radio
```

The exact commands and permissions depend on the target firmware. `cmd` does
not run a command on the local Companion; it sends the text over LoRa to the
selected node.

Incoming direct-route messages are labeled `ROUTED`, not `DIRECT`. Forwarders
consume direct-route prefixes as the packet travels, so the destination cannot
recover the reply's actual hop history from the received packet. Use `trace`
when the return route itself must be verified.

```
send {text}
```
Sends the text message (as DM) to current recipient.

```
trace
```
Traces the saved round-trip route to the current recipient and displays the
SNR at each hop. Select the recipient first with `to {name-prefix}`.

```
trace {name-prefix}
```
Traces a recipient directly without changing the current `to` selection. A
trace requires a known direct path; use normal messaging or path discovery
first if the terminal reports that no valid path is available. Only one
terminal trace can be pending at a time, and a missing response is reported as
a timeout.

For example:

```text
to Hilltop Repeater
trace
trace Downtown
```

The displayed route uses one- or two-byte node hashes and per-hop SNR values.
Saved three-byte paths are traced with two-byte prefixes because the trace
packet format has no three-byte hash-size mode.

To trace an explicit route instead of a saved contact path, provide the prefix
size followed by the complete ordered route:

```text
trace path 1 12 34 56 34 12
trace path 2 1234,ABCD,5678,ABCD,1234
trace path 4 12345678, ABCDEF01 89ABCDEF, ABCDEF01,12345678
```

Prefix separators may be spaces, commas, or any mixture of them. Each prefix
must contain exactly 2, 4, or 8 hexadecimal digits for a 1-, 2-, or 4-byte
trace respectively. Three-byte traces are not supported.

The prefixes are used exactly in the order entered. To receive the trace
result, enter the complete outward route followed by its return route, as in
the mirrored examples above. A route that does not return to this node will
eventually report a timeout.

```
reset path
```
Resets the path to current recipient, for new path discovery. This is retained
as an alias for `path clear`.

```
public {text}
```
Sends the text message to the built-in `Public` group channel.

```
channels
```
Lists the configured channel slots and names without exposing their secrets.

```
channel {name-or-slot} {text}
```
Sends a message to any configured channel by its exact name or numeric slot.
Use the slot shown by `channels` when a channel name contains spaces.

For example:

```text
channels
channel #rgdata Hello from Eugene
channel 2 Another message
show channels on
```

Messages are UTF-8. Emoji use multiple bytes toward the available message
length, which also includes the sender-name prefix added over the air.
