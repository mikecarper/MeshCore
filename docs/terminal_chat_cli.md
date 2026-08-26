# Terminal Chat CLI

Below are the commands you can enter into the Terminal Chat clients:

## Companion USB mode

A Companion USB build starts in the normal binary Companion protocol at
115200 baud. Use this command to switch the same USB connection into terminal
mode as soon as `picocom` opens it:

```sh
picocom --baud 115200 \
  --imap spchex \
  --initstring '+++MESHCORE-TERM-START' \
  /dev/ttyACM0
```

`--initstring` sends this exact terminal-start sequence automatically:

```
+++MESHCORE-TERM-START
```

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

Closing the serial connection also returns native-USB devices to binary mode.
Boards whose USB connector is implemented by a USB-to-UART bridge cannot
observe the host closing the port; on those boards, use the stop sequence or
reboot the device.

Both modes use the same port at 115200. Selecting 57600 is not a portable mode
switch: native USB CDC devices ignore the requested baud, while USB-to-UART
devices really change the UART timing and receive corrupt data. Binary mode is
the framed Companion API used by apps and `meshcli`; close the terminal before
opening that port from an app.

## Commands

```
set freq {frequency}
```
Set the LoRa frequency. Example:  set freq 915.8

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
powersaving
powersaving {on|off}
set powersaving {on|off}
```
Shows or changes Companion device power saving. On ESP32 this controls CPU and
GPS idle behavior; it does not change LoRa RXPS or WiFi modem sleep.

```
get usb.logging
set usb.logging {on|off} [reboot]
```
Shows or changes live USB debug and packet output in a Companion logging
artifact. The setting is persistent. Dual-CDC Full Companion defaults off;
changing its USB interface count requires a reboot, and the optional exact
`reboot` argument performs that reboot after sending the reply. Companion
protocol frames and terminal replies remain enabled on interface `00`.

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
the same setting; binary clients do not need the terminal-start token.

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
