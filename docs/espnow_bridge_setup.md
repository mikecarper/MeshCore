<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/espnow_bridge_setup/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP-NOW bridge: simple setup

For two dedicated receivers feeding one dual-profile transmitter, see
[the three-V4 split repeater setup](split_radio_repeater.md).

Use two ESP32 LoRa boards to join two MeshCore networks with different radio
settings. Each board repeats on its own LoRa network, and the boards exchange
packets directly over 2.4 GHz ESP-NOW. No router, internet connection, or manual
peer pairing is needed.

```text
LoRa network A <--> Repeater A <-- ESP-NOW --> Repeater B <--> LoRa network B
```

## 1. Choose the firmware

Install the exact board's **Full repeater** image on each board. ESP-NOW is
included as a runtime transport on supported ESP32 Full repeaters. Newly
combined ordinary repeaters leave it off until explicitly enabled; that
choice is saved across reboot. Boards with measured capacity exceptions keep
separate `_repeater_bridge_espnow` images. Historical dedicated target names
remain available for direct builds with their original bridge-on defaults.
Full Companion is a different role. See the [firmware picker guide](firmware_picker.md).

Open each board's [USB console](https://flasher.meshcore.io/console) at
115200 baud and run:

```text
get bridge.type
```

The answer can be `espnow`, `mqtt+espnow`, `rs232+espnow`, or
`mqtt+rs232+espnow`, depending on the image. Confirm the
independent ESP-NOW control is present with `get espnow.enabled` and
`get espnow.running`. The `espnow` commands select this transport without
changing another bridge in a combined image.

## 2. Set each board's LoRa network

Configure one board at a time over USB. Replace the placeholders below with
the radio settings used by the network on that board's side. Use `Bridge-A`
for the first board and `Bridge-B` for the second, or choose your own names.

```text
set espnow.enabled off
set name Bridge-A
set radio <freq>,<bw>,<sf>,<cr>
```

Do not type the angle-bracket placeholders literally.

| Radio option | Meaning |
| --- | --- |
| `freq` | LoRa frequency in MHz. Match the local network on this side. |
| `bw` | LoRa bandwidth in kHz. Match that network's bandwidth. |
| `sf` | Spreading factor, 5-12. Match that network's setting. |
| `cr` | Coding-rate value, 5-8. Match that network's setting. |
| `name` | A label to tell the two repeaters apart. It does not pair them. |

**`set radio` saves immediately and switches after two seconds, once the radio
is free and the reply has drained.** No reboot is needed. `get radio` shows the
saved settings; the device screen shows the applied settings. Use permanent radio settings for this setup;
`tempradio` sessions expire.

## 3. Configure the ESP-NOW link on both boards

Run this block on **both** boards. Replace `YourSharedKey` with the same
case-sensitive secret on each board, using 1-15 characters. Channel `6` is
an example; both boards must use the same channel.

```text
set espnow.enabled off
set bridge.channel 6
set bridge.format wrapped
set bridge.secret YourSharedKey
set bridge.source tx
set bridge.delay 500
set repeat on
set espnow.enabled on
```

These settings are saved across reboot. Bridge changes apply immediately;
changing an enabled bridge's channel, format, or secret restarts the bridge.
Stopping it first lets you finish configuring both sides before connecting them.

For a fixed pair, use acknowledged unicast. Run `get espnow.stats mac` on each
board, then set each board's destination to the other board's station MAC:

```text
set espnow.peer AA:BB:CC:DD:EE:FF
get espnow.peer
```

Use the actual peer MAC in place of the example. The packet bytes stay
compatible with older receivers. A failed unicast send is retried up to three
times; MAC success confirms radio reception, not subsequent LoRa forwarding.
The setting persists across reboot. `set espnow.peer broadcast` restores the
default, which lets multiple bridges receive each packet without acknowledgements.
Dedicated ESP-NOW keeps the WiFi receiver awake to avoid missing bridge packets,
using more power than modem sleep. When a WiFi service shares the driver, that
service retains control of its sleep policy.

## What each bridge option does

| Option | Choices and purpose |
| --- | --- |
| `espnow.enabled` | `on` starts ESP-NOW; `off` stops it and keeps its saved settings. Independent of MQTT or RS-232. |
| `espnow.peer` | `broadcast` by default, or a single destination station MAC for acknowledged unicast. Changing it restarts a running bridge. |
| `rs232.enabled` | On combined UART images, starts or stops RS-232 independently of MQTT and ESP-NOW. Use `get rs232.running` to check the live UART. |
| `bridge.channel` | The 2.4 GHz channel, 1-13. Match it on both boards. This is separate from the LoRa frequency in `set radio`. |
| `bridge.format` | `wrapped` is the default for this two-repeater setup and uses the shared secret. `raw` connects to primary-ESP-NOW nodes such as `Generic_ESPNOW` or `SenseCapIndicator-ESPNow`; it ignores the secret. Both peers must use compatible formats. |
| `bridge.secret` | A matching, case-sensitive value of 1-15 characters for `wrapped` mode. It separates bridge groups; it is not strong encryption. MeshCore's own message encryption remains in place. |
| `bridge.source` | `tx` crosses packets this repeater transmits on LoRa, after its forwarding decisions; use this for the setup above. `rx` crosses received packets before those decisions and can pass traffic the local repeater would not forward. |
| `bridge.ingress` | Selects the local radio for incoming bridge packets: `radio` preserves the default primary-radio behavior; `radio2` forwards only on the active MeshCore second profile, with no primary fallback. |
| `bridge.delay` | Wait before processing a packet received from the other bridge, in milliseconds. Range: 0-10000. Start with the default `500`. |
| `repeat` | `on` enables normal LoRa forwarding; `off` disables forwarding. Keep it `on` on both bridge repeaters. Normal routing, scope, and packet-filter rules still apply. |
| `bridge.type` | Read-only firmware capability, including combined transports. |
| `espnow.running` | Read-only ESP-NOW runtime state. `on` means the local bridge started; it does not prove the other board is reachable. |

This fork uses **`set bridge.source tx`** or **`rx`**. Some upstream guides
use `logTx` and `logRx`; those are not the command values used here.

For a LoRa gateway connected to primary-ESP-NOW Companions instead of another
LoRa repeater, see [raw ESP-NOW compatibility](WiFi.md#wifi-companion-setup).

### Send bridge traffic only on MediaMesh

Keep the receiving repeater's primary USA profile at 910.525 MHz while sending
packets arriving over the bridge only on MediaMesh:

```text
MediaMesh 911.150 RX --> Repeater A --> ESP-NOW --> Repeater B --> radio2 911.150 TX
```

Configure the matching ESP-NOW channel, format and secret as above. On the
source repeater already receiving MediaMesh, export receptions without local
relay forwarding:

```text
set bridge.source rx
set repeat off
```

`repeat off` still allows this source's own adverts and replies. For an RX-only
MediaMesh profile, use `set radio2 911.150,250,7,5,rx` and keep crossing off;
the bridge also exports receptions from any other active profile. This setting
does not filter bridge output by the source frequency.

On the destination repeater, leave its existing primary USA settings and use:

```text
set radio2 mediamesh
set radio2.cross off
set bridge.ingress radio2
set repeat on
get bridge.ingress
```

The incoming bridge packets are marked as radio2 traffic locally; the sender
does not need updated firmware or an extra wire-format field. Radio2 must be
available in `rxtx` MeshCore mode. Otherwise packets are dropped rather than
sent on the primary. This saved setting covers all incoming ESP-NOW, UDP and
RS-232 mesh traffic on the destination; `set bridge.ingress radio` restores
the original behavior. Normal scope and filtering rules still apply.

## 4. Check the link

Run on both boards:

```text
get espnow.enabled
get espnow.running
get espnow.status
get bridge.channel
get bridge.format
get bridge.source
get bridge.delay
get repeat
get radio
```

For the example above, expect enabled and running `on`, matching channel and
format, source `tx`, delay `500`, repeat `on`, and the intended LoRa settings
on each side. An existing `bridge.source rx` setup is also valid; diagnose
the selected source rather than changing it just to match this example.
Compare the shared secret privately; omit `get bridge.secret` from logs you
share. `get espnow.status` distinguishes the configured channel from the actual
2.4 GHz channel. Its `last` error stage/code is historical and may remain after
recovery. A running bridge is not proof that the other board received anything.

Send a message from a Companion on network A to one on network B, then reply
from B to A. Check both directions; configuration readback alone does not test
delivery.

If it does not work:

- **Commands are unavailable:** check `get bridge.type` and the firmware choice.
- **Enabled is on but running is off:** initialization failed. Reboot and
  check again; ensure another WiFi service is not occupying the radio.
- **Both are running but traffic does not cross:** compare channel, format,
  and secret; confirm each board's LoRa settings were applied with a reboot,
  and check the networks' forwarding and scope rules.

If those saved settings already match, use the bounded test below before
changing them again.

### Test the 2.4 GHz path without LoRa forwarding

Updated ESP-NOW bridge firmware includes runtime diagnostics in the ordinary
image. No `BRIDGE_DEBUG` build flag or continuous USB logging is needed. Use
the USB console so a WiFi change cannot disconnect your diagnostic session.

On both boards, start a fresh observation window:

```text
espnow resetstats
get espnow.status
get espnow.stats test
```

On A, send one probe:

```text
espnow test A1B2C3D4
```

`OK - test queued id=A1B2C3D4; confirm on peer` means queued locally. On B, run
`get espnow.stats test`: `rx` must increase and `rxid` must be `A1B2C3D4`.
Then send `espnow test B1C2D3E4` on B and confirm that ID on A. The two
commands are two one-way tests; there is no
automatic reply or retry. Sender completion alone does not prove peer receipt.
The optional ID is exactly eight hexadecimal digits, without `0x`; omitting
it uses the current uptime in milliseconds as a correlation ID.

These small, unencrypted CRC-checked probes test transport reachability only.
They do not validate packet-format compatibility or the wrapped shared secret,
authenticate the peer or prove MeshCore packet delivery. They are never
injected into the mesh or forwarded
onto LoRa. Older firmware ignores them and cannot report the received ID;
use an updated peer for confirmation. Sending a probe and resetting
statistics require a local connection; neither changes saved preferences.

### Locate a mesh forwarding failure

If both probe directions work, send one fresh identifiable message from a
Companion on A's LoRa network and read these pages on both bridge boards:

```text
get espnow.stats
get espnow.stats rx
get espnow.stats tx
get espnow.stats drops
```

Repeat in the opposite direction with a new message. Counters accumulate until
`espnow resetstats` or reboot and survive an ESP-NOW stop/start. Inspect changes
across the one message, rather than assuming old totals describe the current
attempt.
Stopping or restarting clears pending queue work without classifying delivery;
the TX drop counter records local rejection/final-failure decisions, not every
undelivered packet.

- **No source-side bridge TX request:** check the configured source.
  `bridge.source rx` exports LoRa receptions; confirm the board heard the
  fresh message. `bridge.source tx` exports LoRa transmissions; confirm it
  forwards the message with repeat on and the intended routing, scope and
  flood-filter settings. Either source is valid.
- **TX requested but not sent:** the TX/drops pages separate queued traffic,
  filtering, duplicates, size/queue drops and SDK start/completion failures.
  Check the configured/live 2.4 GHz channel and last error stage/code.
- **Peer sees frames but accepts no MeshCore packet:** inspect RX rejection
  counters, format and the privately compared wrapped secret. A successful
  diagnostic probe does not rule out a packet-format or secret mismatch.
- **Peer admits the packet but no LoRa delivery follows:** admission means
  queued for normal mesh processing, not a completed LoRa transmission. Check
  repeat, scope, filters, duplicate suppression and the destination network.

An absent counter change narrows a stage; these checks do not assume a cause
for a particular pair of boards.

If both bridge boards also hear the same message directly on LoRa, normal
duplicate suppression can discard its later bridged copy. That alone does
not show a broken ESP-NOW link. This is a possibility to check with the
counters, not an established cause of a reported delivery failure.

### Optional short USB debug capture

For a brief observation window, run `get usb.logging` and `get usb.debug`
and record both original settings. Enable both gates with `set usb.logging on`
and `set usb.debug on`. The bridge emits bounded `ESPNOW:` status/counter
updates from its main loop, at most once per second when diagnostic state
changes. No `BRIDGE_DEBUG` build flag is required, and WiFi callbacks do not
print to USB. Send the two probes or one fresh message, then restore both
original settings. The CLI counter pages and probes do not require either
logging gate. Use the counter pages for exact values; a busy USB console can
skip a debug update. Do not include secrets or WiFi credentials in a shared
capture.

A running bridge keeps the device awake, even with device power saving enabled.
ESP-NOW shares the WiFi radio: use the connected WiFi network's channel when
WiFi is active. Browser OTA pauses ESP-NOW on ordinary non-MQTT repeaters,
reports that pause, and resumes saved-enabled ESP-NOW when OTA is stopped.
A failed OTA start restores the bridge. The RS-232 transport remains available
on combined boards while ESP-NOW is paused.

For every command's full details, see the [bridge CLI reference](cli_commands.md#bridge-when-bridge-support-is-compiled-in).

For a broader comparison with a Linux-based bridge, see the
[MeshCore Nexus bridging guide](https://meshcore.nexus/guides/lora-bridging#method-2-meshcore-esp-now-bridge).

## Keep wardriving traffic off the bridge

New generalized repeater filter tables include a third built-in rule that
blocks authenticated `#wardriving` text and data in both bridge directions,
at every hop count. The same row blocks crossover between radio profiles:

```text
get flood.rule.3
set flood.rule type=any channel=#wardriving hops=all mode=bridge,cross drop
```

The same rule can be added with the shorter command:

```text
set fr any c=#wardriving m=bc d
```

Both forms use the same table. `m=b` means bridge only, `m=c` means crossover
only, and `get fr.N` returns a short command for copying a numbered rule.

The unnumbered command adds the rule safely on existing devices whose saved
tables predate this default. It works with either bridge packet source and
both ESP-NOW formats. Nearby same-profile LoRa forwarding retains the separate
four-hop default. Use `mode=bridge` for a bridge-only block, or `mode=cross` for
a crossover-only block. See the [mode matrix and examples](flood_filtering.md#filter-bridge-and-radio-crossover-traffic).
