<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/rs232_bridge_setup/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# RS-232/UART bridge: setup and diagnostics

Two supported repeaters can exchange MeshCore packets over a wired UART link.
Each repeater can use the LoRa settings of the network on its side:

```text
LoRa network A <--> Repeater A <-- serial link --> Repeater B <--> LoRa network B
```

Use the exact board's repeater image with RS-232 support. Check
`get bridge.type`: its answer must include `rs232`. This transport is optional;
its presence in an image does not mean its UART is enabled. A Companion image
or a room-server build without RS-232 does not gain the transport from these
commands.

## Configure the serial link

Use the UART and pin map for the exact board and image. Connect the two sides
with the appropriate serial wiring and any required line drivers; a board's
UART pins do not automatically provide RS-232 voltage levels. Avoid a UART
already occupied by another peripheral.

Open each board's USB console. Run the following on both boards, using the same
baud rate:

```text
set rs232.enabled off
set bridge.baud 115200
set bridge.source tx
set bridge.delay 500
set repeat on
set rs232.enabled on
get rs232.enabled
get rs232.running
get rs232.status
get rs232.transport
```

Use `bridge.uart` only where the board offers a supported choice, while RS-232
is stopped. Canonical GPS-enabled RAK4631 builds use UART2
(`set bridge.uart 2`); UART1 requires a compatible GPS-free image. The command
rejects UART choices unavailable in the build or unsafe for an active peripheral.

`rs232.enabled` selects the UART independently of MQTT and ESP-NOW in combined
images. The historical `bridge.enabled` alias can select ESP-NOW in MQTT images,
so use the transport-specific `rs232.enabled` control for these checks.

`bridge.source tx` bridges packets transmitted on LoRa after the repeater's
forwarding decisions. An existing `bridge.source rx` setup is also valid: it
bridges received packets before those decisions. Normal scope, filters,
duplicate suppression and repeat settings still affect mesh delivery. Configure
each side's LoRa network separately; see the [bridge CLI reference](cli_commands.md#bridge-when-bridge-support-is-compiled-in).

Incoming mesh packets use the local primary profile by default. Save
`set bridge.ingress radio2` on the receiver to mark them for forwarding only on
its active MeshCore radio2; an unavailable or RX-only radio2 drops them rather
than falling back to the primary. The serial format and older peer remain
compatible. See [bridge ingress](cli_commands.md#select-the-radio-for-incoming-bridge-packets)
for the shared setting and `set bridge.ingress radio` to restore the default.

## Distinguish power state from transport activity

`get rs232.status` retains the existing heartbeat and automatic-standby report.
It separates saved intent, actual UART state, standby/probe phase and confirmed
versus legacy/unknown peer liveness. `get rs232.transport` adds the serial
transport diagnostic view. A running UART means local startup succeeded; it
does not prove the other board received a frame. With RS-232 explicitly
disabled, its transport/counter commands may report diagnostics unavailable
because there is no current UART bridge object.

Heartbeat and negotiated automatic standby are on by default. A fresh,
session-matched heartbeat reply proves bidirectional communication with a
supporting peer. Ordinary incoming mesh data proves only inbound reachability.
See [heartbeat and automatic standby](cli_commands.md#rs232-heartbeat-and-automatic-standby)
before changing power settings. A diagnostic probe does not replace heartbeat
negotiation, enable a disabled UART or establish peer authentication.

## Test each serial direction without LoRa forwarding

Updated RS-232 bridge images include runtime diagnostics without a
`BRIDGE_DEBUG` build flag or continuous USB logging. On both boards:

```text
rs232 resetstats
get rs232.transport
get rs232.stats test
```

On A, send one probe:

```text
rs232 test A1B2C3D4
```

`OK - test queued id=A1B2C3D4; confirm on peer` confirms local queueing only.
On B, read `get rs232.stats test`: `rx` must increase and `rxid` must match
`A1B2C3D4`. Then send `rs232 test B1C2D3E4` on B and confirm that ID on A.
These are two one-way tests. Local UART write completion alone is not peer
receipt.

The optional ID is exactly eight hexadecimal digits, without `0x`; omitting
it uses uptime in milliseconds. Probes are transport diagnostics, not security
tokens or MeshCore packets. They do not prove normal mesh delivery or
authenticate a peer. There is no automatic echo, retry or LoRa forwarding.
Older firmware ignores the extension and cannot report a received ID; use an
updated peer on both sides. Sending a test and resetting counters require a
local console and do not save preferences.

A write deadline records one failure. An entirely unsent probe is cancelled;
if the UART already accepted a prefix, the bridge retains and drains the
original suffix before starting another frame. Each main-loop pass does
bounded work, without retransmitting the prefix. This avoids leaving an older
receiver partway through a control frame. A late completion can increment
`sent` while the earlier `fail` remains; confirm receipt using the other board's
`rx` change and matching `rxid`.

## Locate a mesh forwarding failure

If both probe directions work, send one fresh identifiable message from a
Companion on network A and compare these pages on both repeaters:

```text
get rs232.stats
get rs232.stats rx
get rs232.stats tx
get rs232.stats drops
```

Repeat in the other direction with a different message. Compare counter
changes for the attempt rather than treating old totals as its result. Admission
to the mesh means queued for normal processing; it does not prove a completed
LoRa transmission or end-to-end delivery. If both repeaters already heard a
message directly on LoRa, duplicate suppression can reject a later serial copy.

The RX page separates framing, checksum, packet parsing, partial-frame timeout
and mesh-pool pressure. The TX and drops pages distinguish UART backpressure
from stopped/busy state, filters and duplicate suppression. Byte counters include
heartbeat and diagnostic traffic as well as mesh frames; they do not measure
mesh payload alone.

Counters belong to the current bridge object. They survive automatic standby,
but disabling or reconfiguring RS-232 can replace the object and clear them.
Reboot and `rs232 resetstats` also start a new observation window.

The [RS-232 diagnostic CLI reference](cli_commands.md#diagnose-the-rs232-bridge)
describes the status fields, counter pages and their observation window.

## Optional short USB debug capture

Record both `get usb.logging` and `get usb.debug`, then enable both gates with
`set usb.logging on` and `set usb.debug on`. Send the probes or one fresh mesh
message and restore both original settings afterward. The ordinary image emits
bounded `RS232:` updates from its main loop at most once per second when
diagnostic state changes. A busy USB console can skip an update; use the counter
pages for exact values. Neither logging gate is required for the CLI counters
or test probes.

These diagnostics identify local serial activity and rejection stages. They do
not certify a physical cable, line driver, or end-to-end LoRa path without the
corresponding peer and message checks.
