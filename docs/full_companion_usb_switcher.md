# Full Companion USB CLI and binary switcher

Full Companion uses one primary USB serial interface for two incompatible wire
formats:

- a human-readable ASCII command line;
- the framed Binary Companion protocol used by MeshCore apps and `meshcli`.

The primary interface starts in the ASCII terminal after each boot. A Binary
Companion client does not need to send a special mode command: its first valid
frame automatically hands the interface to the binary parser.

This automatic behavior is compiled only into `companion_radio_full` targets.
Ordinary USB Companion builds continue to use the explicit
`+++MESHCORE-TERM-START` and `+++MESHCORE-TERM-STOP` controls described in the
[Terminal Chat CLI guide](./terminal_chat_cli.md).

## Wire formats

Host-to-device Binary Companion frames use this layout:

```text
'<'  length-low  length-high  payload[length]
```

Device-to-host frames use the same little-endian length with a different
marker:

```text
'>'  length-low  length-high  payload[length]
```

For example, a representative two-byte device query is:

```text
3C 02 00 16 03
```

The ASCII terminal is line-oriented and accepts commands such as:

```text
get radio.cad
set display.rotation 90
reboot
```

Changing the configured baud rate does not select a mode. Use 115200 for
compatibility even though native USB CDC hardware does not use UART timing.

## State transitions

```text
                         complete '<' frame
                 +--------------------------------+
                 |                                v
boot ------> ASCII terminal                  Binary Companion
                 ^                                |
                 |                                | +++MESHCORE-TERM-START
                 | incomplete '<' probe           |
                 | (one-second timeout)            |
                 +--------------------------------+

ASCII terminal -- +++MESHCORE-TERM-STOP ------> Binary Companion
ASCII terminal -- observable USB disconnect ---> Binary Companion
any mode ------- reboot ------------------------> ASCII terminal
```

Serial mOTA and single-TTY logging add exclusive ownership states described
below. BLE, WiFi, Ethernet, and hardware-serial Companion transports are not
switched; they remain binary.

## How automatic detection works

1. Full Companion initializes the normal USB Binary Companion interface, then
   gives its primary stream to the ASCII terminal before normal loop service
   begins.
2. While the prompt has no buffered input, the terminal peeks at the next byte.
   It does not remove that byte.
3. If the byte is `<`, the terminal temporarily releases the stream and enables
   the existing `ArduinoSerialInterface` frame parser.
4. The parser consumes the original `<`, the two-byte length, and the payload.
   There is no second parser and no copied or synthetic frame.
5. A monotonically increasing completed-frame counter confirms that the parser
   received a complete frame. The interface then remains in Binary Companion
   mode.
6. If no complete frame arrives within one second, the parser state is reset
   and the ASCII terminal prints a new banner and prompt.

The switcher checks framing, not client identity. Any syntactically complete
Binary Companion frame confirms binary mode; it does not require the first
command to be `CMD_APP_START` or `CMD_DEVICE_QUERY`. Normal command validation
still occurs after the frame parser returns the payload.

The empty-prompt requirement prevents a literal `<` in the middle of a command
from silently changing modes. A literal `<` typed as the first character does
start a probe, but the terminal returns after the one-second timeout if no
binary header and payload follow.

## Manual controls

The original controls remain available.

From Binary Companion, send this exact unframed line while the parser is idle:

```text
+++MESHCORE-TERM-START
```

Terminate it with CR or LF. The binary parser accepts control tokens only as
complete delimiter-bounded lines; prefixes, suffixes, and partial tokens are
ignored.

From the ASCII terminal, send this exact sequence to return to binary mode:

```text
+++MESHCORE-TERM-STOP
```

The stop sequence takes effect as soon as its last byte arrives in ASCII mode.
The start sequence is recognized only as a completed line while the binary
parser is idle and is not examined inside a length-prefixed frame.

`meshcli` can normally connect directly after boot:

```bash
meshcli -s /dev/ttyACM0 -b 115200 ver
```

An explicit terminal start token is still useful when the device is already in
binary mode:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring $'+++MESHCORE-TERM-START\r' \
  /dev/ttyACM0
```

## Logging and mOTA ownership

The switcher never attempts to mix ASCII, framed Companion traffic, or binary
mOTA traffic on the same stream.

| Situation | Primary USB behavior |
| --- | --- |
| Full Companion after boot | ASCII; a complete `<` frame switches to Binary Companion |
| Dual-CDC logging enabled | Primary interface still follows the switcher; logs use the optional second interface |
| Single-TTY logging enabled at boot | Logging terminal owns primary USB; automatic `<` detection is disabled |
| nRF52 USB serial mOTA active | mOTA owns primary USB; ASCII and Binary Companion are unavailable there |
| BLE/WiFi/Ethernet/hardware serial | Always Binary Companion and unaffected by the USB mode |

On a single-TTY build, use `set usb.logging off` in its logging terminal before
trying to use primary USB with an app. BLE and WiFi Companion transports remain
available while primary USB is logging.

On nRF52, serial `motatool` is also text-first. Its exact initial
`ota folder on` line is recognized in either startup ASCII or Binary Companion
mode. From ASCII, the firmware leaves terminal mode and directly enters
exclusive mOTA ownership; from binary, the idle frame parser recognizes the
same control sequence. The following mOTA request/reply frames therefore cannot
be consumed by the ASCII line editor. `motatool serve --serial` can be the
first client after boot and does not require a terminal token or disconnect
workaround.

Only the exact completed line selects mOTA from ASCII. Extra arguments,
leading/trailing whitespace, or a partial line remain ordinary terminal input.

## Shortcomings and edge cases

This mechanism is deliberately small and deterministic, but it is not a full
protocol negotiation layer.

### It is startup selection, not per-connection negotiation

After the first complete binary frame, the device stays in Binary Companion
mode. Closing `meshcli` does not automatically restore ASCII. Use the terminal
start token or reboot when an ASCII prompt is needed again.

Conversely, closing an ASCII terminal on native USB normally changes the port
to binary mode because the firmware can observe USB DTR/data disconnect. The
next client therefore sees binary mode, not a new ASCII session. A USB-to-UART
bridge often cannot report disconnect, so it can remain in ASCII until the stop
token or a reboot.

### Detection works only at an empty prompt

If part of an ASCII command is already buffered, an incoming `<` is treated as
ordinary terminal input. Clear or submit the line before starting a Binary
Companion client. Only one process should have the serial port open.

### The first frame has a one-second deadline

The complete marker, length, and payload must arrive within the probe window.
This is generous for local USB but may reject a heavily buffered serial proxy,
a debugger that pauses the MCU, or a tool that writes the header and body with
a long delay. A timed-out client can retry after the ASCII banner appears.

### Framing confirmation is not authentication

Any complete length-prefixed frame selects binary mode, even if its command is
unknown or malformed at the application layer. This is safe for stream
separation but means a random complete frame can leave the device in binary
mode until manually switched back.

### The terminal banner is best effort

The firmware enters ASCII mode during boot, often before a host opens the CDC
device. The banner may therefore be absent even though the terminal is ready;
send a newline or a harmless `get` command rather than treating a missing
banner as proof of binary mode.

A host that remains connected across a reboot may receive ASCII banner and
prompt bytes before the first binary response. Binary clients should discard
leading bytes until a plausible `>` frame marker and length are found, reject
implausible lengths, and resynchronize. The ordinary open-after-boot path has
been tested with `meshcli`, but third-party clients that assume byte zero is
always `>` may fail.

### Text and binary output cannot be interleaved

Primary USB suppresses Binary Companion output while the terminal owns the
stream. Packet/debug logging must use its dedicated CDC interface or the
exclusive single-TTY logging mode. Writing diagnostic text directly to the
primary binary stream will corrupt clients regardless of the switcher.

### A literal leading `<` briefly hides the prompt

Typing `<` as the first terminal character begins a binary probe. With no
complete frame, the prompt returns after one second and the banner is printed
again. There is currently no escape syntax for entering a literal leading `<`;
prefix it with another character if it is needed as command text.

## Troubleshooting

If `meshcli` cannot connect:

1. close every terminal or logging reader using the primary interface;
2. confirm that saved single-TTY logging is off;
3. reboot and let `meshcli` be the first process to open the data interface;
4. use the stable `/dev/serial/by-id/*-if00` path on Linux when available;
5. if a prompt appears after one second, the client's first frame was not
   completed inside the probe window.

If an ASCII terminal shows no banner, press Enter and issue a harmless query
such as `get radio.cad`. If binary bytes appear, send the exact terminal start
token or reboot.

The switch policy is implemented in
[`UsbAsciiBinarySwitch.h`](../src/helpers/UsbAsciiBinarySwitch.h), with stream
ownership in [`main.cpp`](../examples/companion_radio/main.cpp) and framing in
[`ArduinoSerialInterface.cpp`](../src/helpers/ArduinoSerialInterface.cpp).
