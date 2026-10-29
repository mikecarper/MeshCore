<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/split_radio_repeater/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Two receive-only V4s feeding one transmit-only V4

Use the exact board's updated **Full Repeater** image on all three V4s. The
left V4 receives MediaMesh, the right V4 receives USA, and both feed the center
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

Configure over USB or a direct WiFi CLI. `radio.mode rx` also prevents LoRa
admin replies, and `tx` prevents receiving LoRa commands. These modes apply to
both LoRa profiles and persist across reboot. They leave the 2.4 GHz bridge
able to transmit and receive. TX-only requires a clear-channel CAD check on
the selected LoRa profile before forwarding a packet. If busy, the center keeps
backing off; it does not bypass CAD after a timeout. A clear check cannot
guarantee collision-free delivery. The center does not accept LoRa packets.

## ESP-NOW setup

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

## WiFi UDP alternative

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

## Check the result

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
