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
Sets LoRa transmit power in dBm.

```
set name {name}
```
Sets your advertisement name.

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
List all contacts by most recent. (optional {n}, is the last n by advertisement date)

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
path {hop-hash[,hop-hash...]}
```
Sets the outgoing path used by subsequent `login`, `send`, and `cmd` commands.
`direct` selects a zero-hop route. `clear` forgets the saved route, causing the
next operation to use flood routing and allowing normal path discovery to
learn a replacement.

Explicit paths use comma-separated hop hashes. Each hop must contain exactly
2, 4, or 6 hexadecimal digits, and every hop in one path must use the same
width. Spaces around commas and hexadecimal letter case do not matter. The
setting is saved with the selected contact.

For example:

```text
to Hilltop Repeater
path A1B2C3,D4E5F6
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
and any reply appears asynchronously as `CLI -> from {name}`.

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
channel #rgdata Hello from Eugene 👋
channel 2 Another message
```

Messages are UTF-8. Emoji use multiple bytes toward the available message
length, which also includes the sender-name prefix added over the air.
