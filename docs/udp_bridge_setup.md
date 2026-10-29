<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/udp_bridge_setup/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# WiFi UDP bridge: up to eight IP peers

For two receive-only V4s feeding one transmit-only V4, see
[the split repeater setup](split_radio_repeater.md#wifi-udp-alternative).

Supported ESP32 repeater images can bridge MeshCore packets directly over an
existing WiFi network, without an MQTT broker. Each board has one UDP listening
port and up to eight configured IPv4 address/port destinations. Eight is the
per-board peer limit; it is not a limit on the router's number of WiFi clients.

```text
LoRa network A <--> Repeater A <-- existing WiFi/LAN --> Repeater B <--> LoRa network B
```

The bridge starts off on new and upgraded installations. It operates only with
a connected station interface and a valid local IPv4 address. It pauses during
SoftAP or AP+STA operation, WiFi disconnection, browser OTA, and incompatible
WiFi ownership transitions, then resumes when station operation is available.
Opening a captive configuration access point does not create a UDP bridge
network. USB/LoRa CLI access remains useful for inspecting a paused bridge.

With saved WiFi credentials, enabling UDP requests a connection to that
network. The web page can share the same station connection. An unused
automatic recovery AP returns to station mode when UDP needs the network;
connected setup clients and unfinished browser/OTA work keep their link.
An explicitly requested `start webconfig ap` stays active until released.
Use `stop webconfig` when finished with that AP, then check `get udp.status`.
Switching WiFi back on does not enable UDP if you saved `udp.enabled off`.

## Router client isolation

Client isolation is an optional access-point or guest-network policy, not a
requirement of WiFi. When enabled it can prevent two wireless devices from
talking directly, even though both can reach the Internet. A similar effect can
come from VLAN rules or host firewalls. The bridge cannot bypass those rules.

Use a trusted SSID/VLAN that allows the two devices to exchange UDP, or ask the
network administrator to allow the bridge peers and port. Disable isolation
only where that network policy is appropriate. Simply changing the UDP port,
enabling MQTT on a repeater, or configuring a second peer address does not make
an isolation rule disappear. Some routers isolate only clients on the same
access point; others isolate all clients or the entire guest network. Verify
the policy on the router in use.

Reserve each board's DHCP address on the router. The bridge stores numeric
IPv4 addresses, not DNS names; a changed lease can otherwise point a peer at
the wrong device. No Internet port forwarding or public exposure is required.

## Configure two boards

Use firmware containing the UDP bridge. Connect both boards to the existing
WiFi network using the normal [WiFi settings](WiFi.md). Read `get wifi.status`
on each board to find its local address once connected. The commands below use
example addresses; substitute the actual reserved addresses.

For a LoRa-capable SenseCAP Indicator, use the Full
`SenseCapIndicator-LoRa_repeater` target on the original 8 MiB/OPI-PSRAM board.
The distinct `SenseCapIndicator-LoRa-N16R2_repeater` target is for 16 MiB/QSPI
PSRAM hardware. Both retain the LoRa expander and display; their PSRAM modes
are selected before app startup and the images are not interchangeable.
The Indicator Companion and primary-ESP-NOW images do not provide this bridge.

Both boards use the existing `bridge.secret` as their shared authentication
secret. Set the same strong secret on both before enabling the transport. Do
not paste it into shared diagnostic captures. The transport authenticates
packets with HMAC-SHA256; it does not encrypt their contents. Use trusted LAN
access and WPA2/WPA3 protection as appropriate for the network.

On board A, whose reserved address is `192.168.1.50`:

```text
set udp.enabled off
set udp.port 4404
set udp.peer.1 192.168.1.51:4404
set bridge.source rx
set repeat on
set udp.enabled on
get udp.status
```

On board B, whose reserved address is `192.168.1.51`:

```text
set udp.enabled off
set udp.port 4404
set udp.peer.1 192.168.1.50:4404
set bridge.source rx
set repeat on
set udp.enabled on
get udp.status
```

Each receiving board must also list the sender's listening address/port as a
peer; this is an explicit allowlist, not discovery or a broadcast service.
Both ports can be changed independently, but each destination must match that
peer's actual listening port. Unicast IPv4 addresses and ports `1..65535` are
supported; multicast, loopback, unspecified and broadcast destinations are
rejected. Remove a destination with `set udp.peer.1 off`. Slots `1..8` have the
same behavior, and duplicate endpoints are rejected.

The UDP settings are saved separately from the historical common preferences.
An invalid or truncated record starts with UDP disabled. A rejected save keeps
the previous runtime preferences; an uncertain filesystem result requires a
reload before further changes. `OK - UDP settings saved` acknowledges saved
intent, not network connectivity or confirmed peer delivery.

## Test each IP direction without sending on LoRa

Wait for `get udp.status` to show ready peer sessions. Each peer establishes an
authenticated session using the shared secret, a fresh boot/session identity
and a challenge exchange. Replay checks reject stale data and diagnostic probes.

From board A's local USB console:

```text
udp test 1 A1B2C3D4
get udp.stats test
```

On board B:

```text
get udp.stats test
```

Board B's `rx` should increase and `rxid` should match `A1B2C3D4`. Reverse the
test with a different ID to check the other direction. `udp test 1` generates
an ID from uptime; an explicit ID must have exactly eight hexadecimal digits.
The peer number is the local configured slot, not a global board identifier.

A queued/sent result confirms only local acceptance by the UDP stack. UDP
itself does not guarantee delivery. A received probe proves authenticated
transport receipt on that peer; it does not prove LoRa forwarding. Probes do
not enter the mesh packet pool, echo, retransmit or transmit on LoRa. Test and
counter-reset commands require a local connection; normal configuration uses
the existing authenticated CLI access policy.

## Diagnose mesh traffic

```text
get udp.status
get udp.stats
get udp.stats rx
get udp.stats tx
get udp.stats drops
get udp.stats test
```

Status separates saved intent from network readiness and authenticated peer
sessions. `ready` counts established boot sessions, not current peer liveness;
an offline peer can retain its session until a restart or reconfiguration.
Use a fresh received probe to establish current reachability.
Counter pages distinguish framing/authentication problems, unknown
source endpoints, replay, mesh filters/duplicates, packet-pool pressure and
outbound queue/socket failures. `udp resetstats` starts a new counter window
without changing saved settings or restarting the bridge.

If no peer session becomes ready, check both stored endpoints, matching
`bridge.secret`, station connectivity, isolation/firewall policy and the peer's
actual firmware. If sessions/probes work but mesh traffic does not, check
`bridge.source`, filters, duplicate counters and each side's LoRa setup.
`bridge.source rx` is valid; `tx` instead selects packets after LoRa forwarding
decisions. These source/filter settings are shared with other compiled bridges.

Incoming UDP mesh packets use `bridge.ingress radio` by default. To forward
them only on an already configured MeshCore radio2, save
`set bridge.ingress radio2` on the receiving board. Radio2 must permit TX;
unavailable profiles cause drops, never a fallback to the primary. The sender,
UDP framing, peer sessions and authentication stay unchanged. See the
[MediaMesh example](espnow_bridge_setup.md#send-bridge-traffic-only-on-mediamesh)
and [bridge-ingress reference](cli_commands.md#select-the-radio-for-incoming-bridge-packets).

The peer list contains direct destinations for locally received/transmitted
LoRa traffic. An incoming UDP packet enters the local LoRa mesh; it is not
relayed to other UDP peers, including through its later local LoRa TX callback.
A hub therefore does not provide UDP-to-UDP transit between its peers. Normal
mesh duplicate/filter checks still apply, including when several repeaters
hear the same LoRa packet. The configured eight-peer fan-out is bounded; it
does not increase LoRa capacity or promise delivery through an arbitrary IP
topology. Avoid unnecessary parallel paths and loops.

For a short USB diagnostic capture, record `get usb.logging` and
`get usb.debug`, then enable both:

```text
set usb.logging on
set usb.debug on
```

Restore both previous values afterward. Bounded `UDP:` updates are emitted
from the main loop at most once per second when diagnostic state changes.
The counter pages and probes work without either logging gate. Never include
WiFi passwords or `get bridge.secret` in logs you share.
