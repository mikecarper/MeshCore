<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/split_radio_repeater/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Split radio repeater

Install the matching **Full Repeater** image on each Heltec V4. Configure
each board over USB or its WiFi CLI. Choose the [two-V4 layout with MediaMesh
on the outer radio](#two-v4s-with-mediamesh-on-the-outer-radio) or the
[three-V4 layout with a transmit-only center](#two-receive-only-v4s-feeding-one-transmit-only-v4).

## Two V4s with MediaMesh on the outer radio

The center V4 is a dedicated USA receiver. The outer V4 repeats USA packets
from the center and handles MediaMesh reception and transmission locally.

```text
USA 910.525 --> center V4 (RX only) --> ESP-NOW or UDP --> outer V4 --> USA 910.525
                                                            |
                                                            +-- RX/TX MediaMesh 911.150
```

The outer V4's one LoRa chip sends on both frequencies. It finishes one
packet before switching to send a packet on the other frequency. Keep
crossing off so a received packet stays on its own frequency.

Between transmissions, the outer V4 scans both profiles. The center keeps
listening on USA even while the outer V4 transmits. An RX-only V4 can scan
two profiles if both are enabled. Keep the center on one profile for this
setup because the bridge route identifies the sender by MAC or IP and port.
Two frequencies forwarded from that sender would use the same route.
A strong nearby transmission can also interfere with the center's reception
even when it is tuned to the other frequency. For simultaneous reception on
both frequencies at the repeater site, use the
[three-V4 layout](#two-receive-only-v4s-feeding-one-transmit-only-v4) with a
dedicated receiver for each frequency and sufficient antenna isolation.

### Configure the two radios

On **both V4s**, stop the two bridge transports before changing their routes.
The commands below clear existing bridge routes. Use the same 1–15 character
secret on both boards in place of `YourSharedKey`.

```text
set espnow.enabled off
set udp.enabled off
set bridge.secret YourSharedKey
set bridge.delay 500
clear bridge.routes
set bridge.ingress off
set radio 910.525,62.5,7,5
set radio2.cross off
```

On the **center V4**:

```text
set radio.mode rx
set radio2 off
set repeat off
set bridge.source rx
```

On the **outer V4**:

```text
set radio.mode both
set radio2 mediamesh
set repeat on
set bridge.source tx
```

The [MediaMesh preset](radio_profiles.md#mediamesh-presets) selects
911.150 MHz, 250 kHz, SF7, CR 4/5 from the saved USA primary and enables
forwarding for the `#mediamesh` region.

### Connect the two V4s with ESP-NOW

On **both V4s**, select the same 2.4 GHz channel, start ESP-NOW and read
each board's WiFi station MAC:

```text
set bridge.channel 6
set bridge.format wrapped
set espnow.enabled on
get espnow.stats mac
```

Use the actual MACs below. In this example the center ends in `10` and the
outer board ends in `20`.

On the **outer V4**, route the center's packets to the USA primary.

```text
set bridge.route.1 mac:AA:BB:CC:DD:EE:10 radio
set espnow.peer AA:BB:CC:DD:EE:10
```

On the **center V4**, send receptions to the outer board.

```text
set espnow.peer AA:BB:CC:DD:EE:20
```

### Connect the two V4s with WiFi UDP

Choose either ESP-NOW or UDP. For UDP, leave ESP-NOW off. Connect both V4s to the same
[WiFi network](WiFi.md) and substitute their reserved addresses below.
This example uses center `192.168.1.50`, outer `192.168.1.51` and UDP port
`4404`. Remove any other saved UDP peers when converting an existing setup.

On the **outer V4**:

```text
set udp.port 4404
set udp.peer.1 192.168.1.50:4404
set bridge.route.1 udp:192.168.1.50:4404 radio
set udp.enabled on
get udp.status
```

On the **center V4**:

```text
set udp.port 4404
set udp.peer.1 192.168.1.51:4404
set udp.enabled on
get udp.status
```

### Check the two-radio layout

Check `get radio.mode` on each board. The center should report `rx` and the
outer board `both`. On the outer board, `get bridge.route.1` should show the
center routed to `radio`, and `get radio2.cross` should report `off`.

Send a USA test packet from a spot where only the center can hear it. Check
for the outer V4's repeater ID on 910.525 MHz. Then send a different packet
on MediaMesh near the outer V4 and check for its ID on 911.150 MHz. Monitor
both frequencies to confirm each test repeats only on its original network.

### Receive both frequencies on a Companion

On each downstream Companion with the USA primary, run `set radio2 mediamesh`.
The preamble on each frequency gives an idle Companion time to switch back
and detect the packet. Each board calculates timing from its own radio
switches.

After about two minutes of scanning, compare these readings:

| Where | Command | Values in USA, MediaMesh order |
| --- | --- | --- |
| Outer V4 | `get radio2.scan` | `preamble=` gives the transmitted lengths in symbols. |
| Each Companion | `get radio2.timing` | `need=` gives that receiver's recommended lengths. |

Use matching preamble lengths on the transmitter and receivers for each
frequency. The SX1262 receiver uses its configured length to decide how long
to wait for the packet header. A longer preamble sent to a receiver configured
for a shorter one can cause the receiver to switch away before the header.

Leave the preset on automatic when the effective lengths match and meet
every receiver's recommendation. If the outer V4 sends 40 MediaMesh
symbols and a Companion needs 48, apply the 48-symbol setting to the
**outer V4 and each downstream Companion** in this USA example.

```text
set radio2 911.150,250,7,5,rxtx,48
```

Use the largest requirement among your receivers. After changing the USA
preamble, recheck both recommendations because the USA length also controls
how long each Companion listens on MediaMesh. For traffic going the other way,
compare both lengths with the outer V4's `need=`.
The outer V4 serializes its own transmissions. Other nodes can still transmit
at the same time, and a Companion receiving one frame stays on that frequency
until the frame ends. It can also miss frames while transmitting.
Simultaneous reception of independent packets on both frequencies requires
two receivers. See [preamble timing](radio_profiles.md#preamble).

## Two receive-only V4s feeding one transmit-only V4

The left V4 receives MediaMesh, the right V4 receives USA, and both feed the center
over ESP-NOW or WiFi UDP:

```text
MediaMesh 911.150 --> left V4  (LoRa RX only) --+
                                              +--> center V4 (LoRa TX only)
USA 910.525      --> right V4 (LoRa RX only) --+       radio:  USA 910.525
                                                     radio2: MediaMesh 911.150
```

The center routes by each bridge sender's identity. It never needs to infer
the source frequency from encrypted packet contents. Its single LoRa chip
transmits the two profiles sequentially; they cannot transmit simultaneously.

`radio.mode rx` also prevents LoRa
admin replies, and `tx` prevents receiving LoRa commands. These modes apply to
both LoRa profiles and persist across reboot. They leave the 2.4 GHz bridge
able to transmit and receive. TX-only requires a clear-channel CAD check on
the selected LoRa profile before forwarding a packet. If busy, the center keeps
backing off; it does not bypass CAD after a timeout. A clear check cannot
guarantee collision-free delivery. The center does not accept LoRa packets.

### ESP-NOW setup

On **all three V4s**, prepare the same bridge group. Replace `YourSharedKey`
with the same 1–15 character secret, and use one common 2.4 GHz channel:

```text
set espnow.enabled off
set bridge.channel 6
set bridge.format wrapped
set bridge.secret YourSharedKey
set bridge.source rx
set bridge.delay 500
clear bridge.routes
set bridge.ingress off
```

`clear bridge.routes` replaces any existing source mappings. With ingress
`off`, a sender without an explicit rule cannot enter the local LoRa mesh.
Using source `rx` on the center also prevents its LoRa transmissions being
exported back to the receivers.

On the **left V4**:

```text
set radio.mode rx
set radio 911.150,250,7,5
set radio2 off
set radio2.cross off
set repeat off
set espnow.enabled on
get espnow.stats mac
```

On the **right V4**:

```text
set radio.mode rx
set radio 910.525,62.5,7,5
set radio2 off
set radio2.cross off
set repeat off
set espnow.enabled on
get espnow.stats mac
```

Record each returned `mac:AA:BB:CC:DD:EE:FF` value. This is the WiFi station
MAC used by ESP-NOW, not the node's MeshCore public key or Bluetooth address.

On the **center V4**, substitute those actual MAC addresses:

```text
set radio.mode tx
set radio 910.525,62.5,7,5
set radio2 mediamesh
set radio2.cross off
set bridge.route.1 mac:AA:BB:CC:DD:EE:10 radio2
set bridge.route.2 mac:AA:BB:CC:DD:EE:20 radio
set repeat on
set espnow.enabled on
get espnow.stats mac
```

The first example MAC represents the left V4; the second represents the right.
Forwarded left traffic stays on MediaMesh, and forwarded right traffic on USA.
If a selected profile is unavailable, incoming packets are dropped rather
than moved to the other channel. Normal duplicate, scope, filter and airtime
checks still apply. The `mediamesh` preset also enables the normal MediaMesh
forwarding region. The center's own adverts and replies retain their existing
routing policies, including `tx.reply`; source rules select forwarded traffic.

Use the center's returned station MAC as the destination on **both receivers**:

```text
set espnow.peer AA:BB:CC:DD:EE:30
get espnow.peer
```

Replace the example with the center's actual MAC. Unicast enables MAC
acknowledgements and up to three retries after an explicit failed send. The
packet format is unchanged, so the receiving bridge can run older firmware.
A MAC acknowledgement confirms reception by the peer radio; check the output
LoRa network to verify forwarding. The setting persists and restarts an
already running bridge. `set espnow.peer broadcast` restores the default
broadcast behavior, which has no receiver acknowledgement.

A dedicated ESP-NOW bridge keeps WiFi modem sleep disabled for prompt packet
reception. A bridge sharing an active WiFi connection retains that connection's
power policy and must use its channel.

### WiFi UDP alternative

Keep the LoRa settings and direction modes above. Disable ESP-NOW on all
three, connect them to an existing WiFi network using the [WiFi settings](WiFi.md),
and reserve their DHCP addresses. These examples use left `192.168.1.50`,
right `192.168.1.51`, center `192.168.1.52`, all on UDP port `4404`.
Keep the same `bridge.secret` on all three.

On **each receiver**, configure the center as its only UDP destination:

```text
set espnow.enabled off
set udp.enabled off
set udp.port 4404
set udp.peer.1 192.168.1.52:4404
set udp.enabled on
get udp.status
```

Remove any other saved `udp.peer.2` through `.8` destinations if present. On
the **center**, replace the MAC rules with endpoint rules and allow both peers:

```text
set espnow.enabled off
set udp.enabled off
set udp.port 4404
set udp.peer.1 192.168.1.50:4404
set udp.peer.2 192.168.1.51:4404
clear bridge.routes
set bridge.ingress off
set bridge.route.1 udp:192.168.1.50:4404 radio2
set bridge.route.2 udp:192.168.1.51:4404 radio
set udp.enabled on
get udp.status
```

The route matches the sender's IP address **and UDP listening port**. Both
ends must allow each other's endpoint for the authenticated bridge session.
See [UDP bridge setup](udp_bridge_setup.md) for station readiness, router
isolation and transport probes.

### Check the result

On each receiver, `get radio.mode` should report `rx`. On the center:

```text
get radio.mode
get cad
get radio
get radio2
get bridge.ingress
get bridge.route.1
get bridge.route.2
get radio2.status
```

Expect center mode `tx`, effective CAD on, default ingress `off`, the left rule targeting
`radio2`, and the right rule targeting `radio`. Send a packet on each LoRa
network and check receipt on that same network beyond the center. Bridge
counters show transport activity but do not by themselves prove LoRa delivery.

To return a V4 to normal LoRa operation, use `set radio.mode both`. Existing
installations default to `both`, with no source routes and primary-radio
bridge ingress. Existing bridge wire formats are unchanged. Strict direction
modes require updated firmware on each configured V4; old bridge senders can
still feed an updated center using the source mappings.
