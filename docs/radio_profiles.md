<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/radio_profiles/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Two LoRa profiles on one radio

`radio2` lets one LoRa transceiver alternate between two frequencies or modulation
settings. It is useful for listening to two networks, or keeping your usual
channel available during a LoRa firmware update.

The second profile and `radio2.cross` both default to `off`. Saved settings
survive firmware updates; selecting a profile does not change the crossing policy.

There are **at most two active profiles**. `tempradio` temporarily replaces
`radio`; `tempradio2` temporarily replaces `radio2`. These are time-shared receive
windows, not simultaneous receivers. Receiving or transmitting a packet on one
profile makes the other unavailable until that packet finishes.

The second-profile commands are shared by repeater, Companion, room server,
sensor and standalone terminal-chat firmware with a LoRa radio. ESP-NOW-only
devices reject LoRa profile commands. KISS is a raw host-controlled modem: it
exposes equivalent RAM-only binary `SetRadio2`/`SetTempRadio2` commands and
logical KISS Data ports, documented in [the KISS modem protocol](kiss_modem_protocol.md).
It does not use the Mesh CLI's persistent profile file or Mesh retry queues.

Full ESP32 and nRF52 LoRa images also offer an optional
[raw host KISS service](kiss_modem_protocol.md#full-firmware-host-kiss).
`set kiss on` enables it, default off, while radio1 retains its normal role.
It borrows an explicitly configured saved `radio2` profile for the host's
session. Meshtext, Ethernet and temporary radio2 owners cannot share that
lease. This service is a raw modem; the connected computer handles its own
network protocol and routing.

## Quick setup

Keep the primary channel configured with `radio`, then add the second:

```text
set radio2 910.5,500,8,5,rx
get radio2
get radio2.status
```

The tuple is **frequency in MHz, bandwidth in kHz, spreading factor, coding-rate
denominator, mode, optional preamble in symbols**. `5` means coding rate 4/5.

### MediaMesh presets

Enable the saved MediaMesh profile with one command:

```text
set radio2 mediamesh
```

This sets **911.150 MHz, 250 kHz, SF7, CR 4/5, `rxtx`, automatic preamble**.
For the same profile at **125 kHz**, use:

```text
set radio2 mediamesh-range
```

On Companions, adding `#r2-mediamesh` in the app also saves the `mediamesh`
profile; `#r2-mediamesh-range` selects `mediamesh-range`. Renaming another
channel to either alias does the same. Names without `#` also work. No extra
CLI command or forwarding-region setup is needed on a Companion.

Re-saving the same preset alias, editing its key, or rebooting preserves any
later manual radio2 changes, including `set radio2 off`. Removing the channel
does not switch radio2 off. All `r2-` channels share one secondary profile, so
the most recently added or renamed MediaMesh preset chooses its RF settings.
Meshtext channels and an active foreign-protocol radio2 session cannot share
this setup. The supplied channel name and key retain the normal alias rules
[below](#companion-channel-name-routing-aliases).

The RF settings match the presets documented by
[MeshCore Open TAK](https://github.com/jade-on-mesh/meshcore-open-tak/blob/main/documentation/media-mesh.md)
and [Wadamesh TAK](https://github.com/jade-on-mesh/wadamesh-tak/blob/main/MEDIAMESH.md).
Nodes exchanging media need matching radio settings.

On repeaters, room servers and sensors, either preset also creates and enables
the public `#mediamesh` region for scoped flood forwarding, saving the permission
automatically. Later RF tuple changes keep that permission. `set radio2 off` or
`set radio2 auto` disables it but retains the region entry; selecting either
MediaMesh preset enables it again. A full region table rejects the preset
before changing the radio settings. Remote changes apply after the command's
acknowledgement, alongside the profile change.

This permission allows matching incoming scoped floods; it does not select the
default scope for outgoing traffic or enable crossing between profiles.
Default/home regions, the wildcard policy and app-selected scopes stay
unchanged. Companions need no additional forwarding-region permission.

Both leave the primary profile and `radio2.cross` unchanged. The center is
625 kHz above a 910.525 MHz primary. Automatic preamble remains scan-aware;
neither preset fixes it at 32 symbols. See [preamble](#preamble) for the
switching measurement and recommendations.

On Companions, the default `tx.media auto` sends recognized binary photo/voice
traffic only on an active `rxtx` MeshCore second profile, including `tempradio2`.
Apps do not need to set `tx.media`. Ordinary traffic keeps its existing route.
If the second profile cannot transmit MeshCore, media uses the normal
channel/global route; `set tx.media radio2` instead requires radio2 with no
primary fallback. See [application TX routing](#companion-media-and-ssh-tx-routing).

Infrastructure repeaters can separately mark packets arriving over ESP-NOW,
UDP or RS-232 with `set bridge.ingress radio2`. Those packets forward only on
the second MeshCore profile; they are dropped if it cannot transmit, without
primary fallback. The default `bridge.ingress radio` preserves existing bridge
behavior. See the [MediaMesh bridge example](espnow_bridge_setup.md#send-bridge-traffic-only-on-mediamesh).

### Discover the local radio2 profile

Clients can request a repeater's current MeshCore radio2 profile through public,
zero-hop discovery. Set bit 1 (`0x02`) of the `DISCOVER_REQ` flags byte: `0x82`
for the full public key, or `0x83` with the prefix-only bit also set. The type
filter, tag and optional `since` fields keep their existing meanings. Omit
`since` or send zero when requesting current settings; radio changes do not
update the discovery timestamp.

The requested `DISCOVER_RESP` appends this version-1 trailer immediately after
the public key, at payload offset 38 (full key) or 14 (8-byte prefix):

| Field | Bytes | Meaning |
| --- | --- | --- |
| version | 1 | `1` |
| flags | 1 | Bit 0: configured; bits 1–2: mode (`0` off, `1` rx, `2` rxtx); bit 3: temporary; other bits zero |
| frequency | 4 | Little-endian unsigned integer, MHz × 1000, rounded |
| bandwidth | 4 | Little-endian unsigned integer, kHz × 1000, rounded |
| SF | 1 | Spreading factor |
| CR | 1 | Coding-rate denominator (`5` means 4/5) |

For example, the MediaMesh preset reports frequency `911150`, bandwidth
`250000`, SF `7`, CR `5` and flags `0x05`. An active `tempradio2` reports its
current settings with bit 3 set. Clients use automatic preamble; the trailer
does not carry a preamble value.

When radio2 is off, unsupported, or using a foreign protocol, the trailer is
only `[1][0]`. Full responses are therefore 50 bytes with a profile or 40 bytes
without one; prefix-only responses are 26 or 16 bytes. Without the request bit,
responses remain 38 or 14 bytes. A legacy responder's missing trailer means
unsupported, not disabled. Clients should ignore unknown versions and
incomplete trailers.

Replies remain on the main radio with zero hops and share the existing limit
of four replies per two minutes. Existing visibility controls still apply.
Discovery is public and unauthenticated. See [control data](payloads.md#control-data)
for the base packet fields.

### Channel spacing

When setting a saved, temporary or scheduled second profile, the response warns
if its occupied bandwidth overlaps the primary channel or comes within **6 kHz
of either edge**. This check uses both channel bandwidths, regardless of SF or
coding rate. The change still applies. The warning also appears when reading
the second profile, including while the switching timing self-test is pending.

The warning suggests a nearby center frequency with the same bandwidth, SF and
coding rate that clears the buffer. It chooses the nearest whole-kHz frequency
on the requested side of the primary channel (above for identical centers),
checks the radio's supported frequency range, and tries the other side if
needed. The suggestion is not applied automatically.

For example, with `radio` at **910.525 MHz / 62.5 kHz**, a second 62.5 kHz
profile at 910.550 MHz produces:

```text
WARN radio2 overlaps radio1 (6kHz guard); try 910.594MHz
```

The center spacing must exceed `(primary bandwidth + second bandwidth) / 2 +
6 kHz`; touching that boundary also warns. Frequency comparisons use the stored
RF centers rounded to the nearest Hz. This is a channel-spacing aid; choose
the suggested frequency only when it also suits the network and local band plan.

| Mode | Receive on profile 2 | Transmit on profile 2 |
| --- | --- | --- |
| `rx` | Yes | Only admin CLI replies explicitly configured with `tx.reply ... force` |
| `rxtx` (also `rx&tx`) | Yes | Yes |
| `off` | No | No |

For transmission on both permanent profiles:

```text
set radio2 910.5,500,8,5,rxtx
set radio2.cross on
```

The same encoded message is queued separately for each allowed transmit profile.
Each has its own direct/flood retry slots and channel-busy backoff. A forwarding
echo on one profile cannot cancel the other profile's retry. Packet storage and
the physical transceiver's airtime budget are shared; exhausted queues can still
drop packets.

## Keep the main channel during an update

```text
set tempradio2 910.5,500,8,5,rxtx,120
get tempradio2
```

This enables the second profile for 120 minutes (2 hours). Locally generated
LoRa OTA requests use the temporary second profile. OTA responses retain the
request's profile and follow `radio2.cross`. Ordinary local traffic stays
on the primary profile by default. Use an OTA-capable build and the usual OTA
setup on every participating node; a temporary profile does not add OTA support
to firmware built without it.

Local settings take effect after a short reply allowance. On repeaters, immediate
remote `radio2`/`tempradio2` changes (including `off`) wait until their exact reply
copies finish, with at least one successfully transmitted. A reply that cannot
be queued or whose copies all fail cancels the change. Queued replies have a
five-minute deadline; an already-transmitting copy may finish under the normal
radio watchdog. This confirms transmission, not reception by the remote client.
An active temporary profile keeps its radio generation until those tracked
reply copies finish or are retired, but its remaining lease continues counting
down during that wait.

Permanent remote changes are prepared in an uncommitted file before replying;
the saved configuration is replaced only after reply transmission. If that
replacement fails, the requested settings remain pending and the commit retries.
An expired temporary profile returns to the last saved configuration during
those post-reply storage retries; the uncommitted settings do not become active.
Local `get radio2.status` reports this pending state. Roles without the tracked remote
reply path require local USB for these immediate secondary-profile changes.

Temporary periods and schedules live in RAM and disappear on reboot. Waiting
for a reply does not extend a temporary lease. Timer expiry also uses monotonic
time, so setting the clock backwards cannot extend a temporary session.

On infrastructure roles, `get tempradio` shows the live temporary RF tuple,
or `pending` when a session is waiting to start and none is running. A queued
replacement or future schedule does not hide the current live tuple. The
accepted command includes the initial two-second application delay.
On LR2021 boards such as W12, saved extra-SF detectors pause when incompatible
with the temporary tuple and return when normal radio settings are restored.
Compatible extra-SF detectors remain active.

## Repeater, room-server, and sensor replies

These roles default to **`tx.reply both`** for CLI replies to authenticated
admins only. Those replies are queued on `radio` and on an active `radio2`
that permits TX. This includes cached CLI results and delayed admin GPIO
command completion. Each copy has its own retry state.

Login replies, ACKs, returned paths, status/telemetry responses, room posts,
LoRa OTA traffic, signed fleet acknowledgements, and non-admin CLI replies
follow the normal `radio2.cross` rules. A telemetry command entered through
the admin CLI still produces an admin CLI reply; an app telemetry request does
not. Region/filter-manager roles do not receive the admin override.

```text
get tx.reply
set tx.reply both
set tx.reply both force
set tx.reply radio
set tx.reply auto
```

| Choice | Admin CLI replies transmit on |
| --- | --- |
| `both` | Primary plus an active `rxtx` second profile. Default. |
| `radio` | Primary only. |
| `radio2` | Second only; no primary fallback. |
| `auto` | Normal `radio2.cross` behavior, including permanent/temporary isolation. |
| `off` | Neither profile. Suppresses admin CLI replies only. |

Append **`force`** to `both` or `radio2` to allow these replies on an active
second profile configured as `rx`. Without `force`, `rx` remains receive-only.
Sending the command again without `force` removes the override. It never turns
on `radio2 off`, changes the saved `rx` mode, or enables ordinary forwarding/TX
there. Existing crossover packet filters still apply to the extra copy.

The explicit choices override `radio2.cross`, including `off` and mixed
permanent/temporary profiles. `radio` means the active primary profile,
including `tempradio`; `radio2` also includes an active `tempradio2`.
For example, a repeater can retain its normal channel while allowing admin CLI
responses on a temporary receive profile:

```text
set tempradio2 910.5,500,8,5,rx,120
set tx.reply both force
```

This override does not allow OTA responses on a receive-only profile. Use
`rxtx` for a profile that needs to exchange OTA requests and data.

This setting survives reboot; older saved profiles adopt `both` without force.
The getter reports when the chosen second profile cannot transmit. Already
queued replies retain their choice; disabling/changing a profile invalidates
stale queued packets. A parameterized repeater `tempradio` command waits for
both admitted reply copies to drain before changing the primary settings;
at least one must transmit successfully. Those tracked replies omit alternate
paths and transport retries so no late success copy can outlive the handoff.
Queue capacity and the shared transceiver's airtime budget still limit delivery.

Companions use the per-contact/channel choices below; `tx.reply` is an
infrastructure setting.

## Companion messages on both profiles

To send ordinary Companion messages on both `radio` and an active `tempradio2`,
configure the temporary profile in `rxtx` mode and enable crossing:

```text
set radio2.cross on
get tempradio2
get radio2.cross
get radio2.status
```

`rxtx` permits transmission on the second profile; `radio2.cross on` copies new
messages between the two profiles. `TX=a,b` reports primary and secondary
transmit counts. Crossing also applies to OTA traffic.

The crossing setting is saved and remains enabled after the temporary session
ends or the node reboots. Run `set radio2.cross off` to restore the default
isolation between profiles. The firmware picker's
Companion tempradio2 instructions include both choices and the status commands.

## Companion TX routing by contact or channel

Companions can save a transmit choice for each contact and each configured
channel. The commands apply to messages sent from the phone app, USB/BLE/TCP
Companion clients, and the text terminal:

```text
set tx.user "Alice Jones" radio2
get tx.user "Alice Jones"
set tx.channel 0 both
get tx.channel 0
set tx.channel #wardriving radio
get tx.channel #wardriving
```

Channel indices match the app and the terminal's `channels` list; Public is
normally slot `0`. Names must match exactly, including capitalization and any
leading `#`. Quotes always select a literal name, so `"1"` is a channel named
`1`, while unquoted `1` selects slot 1. Quote contact names beginning with
`key:` to distinguish them from public-key selectors. Duplicate names are
rejected: use the channel index or a contact's public key instead.

| Choice | Locally addressed traffic transmits on |
| --- | --- |
| `auto` | Existing behavior, including `radio2.cross` and temporary-profile isolation. This is the default. |
| `radio` | The primary profile only. |
| `radio2` | The second profile only; sending fails if it is off or RX-only. |
| `both` | The primary profile and the second profile whenever the second is enabled for TX. |
| `off` | Neither profile for this contact/channel. Reception stays enabled. |

Explicit choices override `radio2.cross`, including `off` and the isolation
between a permanent and temporary profile. They do not turn on radio2 or
change its `rx`/`rxtx` mode. For example, leave `radio2.cross auto` and use
`set tx.channel 0 both` to send Public messages on both `radio` and an active
`tempradio2`, while other channels keep their default routing.

`radio` means the currently active primary profile, whether permanent or
`tempradio`; `radio2` similarly includes `tempradio2`. There are still at most
two active profiles. Getters show the saved choice and `active=` for newly
initiated messages with the current profile configuration. A temporarily
unavailable second profile is reported explicitly.

Contact selection uses the full stored public key after resolving a unique
name or key prefix. It never routes by the short on-air destination hash.
For key selection, prefix 12–64 hexadecimal digits (whole bytes) with `key:`:

```text
set tx.user key:0123456789AB radio2
get tx.user key:0123456789AB
```

Replace the example prefix with your contact's key; use more digits if it is
ambiguous. `get tx.user` and `get tx.channel` report override counts and query
syntax. To return a target to the global behavior:

```text
set tx.user "Alice Jones" auto
set tx.channel 0 auto
```

A user's setting covers addressed texts, login/CLI/telemetry requests, replies,
ACKs, and returned paths to that contact. Thus `off` also prevents ACKs to
that contact. Channel settings cover group text and group data, including
an explicitly routed group datagram. Contact choices do not filter other
people's messages inside a group channel. Adverts, raw traces, raw packets,
and OTA packets without a contact/channel destination retain global routing.
Packets and their retries already queued retain the choice used when created;
changing or disabling the radio profile still invalidates stale queued work.
Message timeout estimates use the selected profiles' airtime, independently
of the scanner's current receive profile.

Choices survive reboot and normal phone-app updates to the same contact or
channel key. Replacing a channel's key or deleting/replacing a contact clears
its choice. They use reserved bytes in existing saved records, so record
sizes and the phone protocol are unchanged. Older firmware ignores these
bytes and may reset the choices when it saves the records.

## Companion media and SSH TX routing

Companions also save independent application-data routes:

```text
get tx.media
set tx.media radio2
get tx.ssh
set tx.ssh radio
```

Both accept `auto|radio|radio2|both|off` and default to `auto`. An explicit
application route overrides the channel's TX choice for that datagram only;
`tx.media auto` chooses only radio2 whenever its active profile permits
`rxtx` **MeshCore** transmission, including an active `tempradio2`. This
overrides the channel/global route for recognized media even when an app
has never configured `tx.media`. Otherwise, media uses the normal channel/global
route. `tx.ssh auto` always preserves that normal route.

The explicit `radio2` choice never falls back to `radio`, and requires an enabled
`rxtx` **MeshCore** profile. `both` uses the
profiles available for MeshCore transmission. `off` blocks transmission, not
reception. Getters show the saved choice and current `active=` mask; `auto`
shows `active=radio2` while media automatically selects radio2, and
`active=channel/global` otherwise, because the effective route then depends on the
destination channel (or global rules for raw data). Settings
survive reboot; a failed save returns an error and keeps the previous choice.
Already queued packets retain their original route.

`tx.media` recognizes Open TAK photo/voice channel data type `0x5643`, raw
clip packets `0x56`/`0x57`, and well-formed binary fetch requests `0x72`.
This covers `CMD_SEND_CHANNEL_DATA` and `CMD_SEND_RAW_DATA`, plus visible
raw media in `CMD_SEND_RAW_PACKET`. Fully encoded encrypted group packets
submitted through `CMD_SEND_RAW_PACKET` retain their existing route.
Text announcements (`VE3:`, `VM1:`, `VI1:`), text fetch requests (`VR3:`),
ordinary chat, contact ACKs, adverts, OTA, and unrelated raw data keep their
normal routing. The format is defined in
[Open TAK's voice/media wire implementation](https://github.com/jade-on-mesh/meshcore-open-tak/blob/main/lib/vox/voice_wire.dart).

`tx.ssh` uses a **new opt-in application contract**, not automatic inspection
of encrypted SSH or arbitrary IP tunnels. A tunnel client must use either:

- `CMD_SEND_CHANNEL_DATA` with data type `0x5353` (wire bytes `53 53`), or
- `CMD_SEND_RAW_DATA` with the four-byte prefix `53 53 48 01` (`SSH`, version 1),
  followed by at least one application byte, on **every** tunnel packet.

The firmware does not add/remove the prefix, decrypt the tunnel, or run an
SSH server. Both endpoints must understand this framing. Untagged existing
tunnel apps remain unchanged and are **not** affected by `tx.ssh` until they
adopt the contract. Other channel data types do not acquire an SSH route
merely because their contents start with `SSH`. Raw packets still require
a direct path, including zero-hop; this does not enable raw flooding.

For a media-only side channel, use `set radio2 mediamesh` with the default
`radio2.cross off` and `tx.media auto`. Ordinary text can stay on the primary
profile without routing every raw application onto radio2. Choose
`set tx.media radio2` if media must fail rather than use the primary when
radio2 is off, RX-only, or owned by another protocol.

## ESP32 Companion Ethernet over LoRa (KISS)

ESP32 **Full Companion** builds with a supported SX1262/SX1276 modem can
carry raw Ethernet on radio2 while the primary profile stays MeshCore. This
separate feature is named `ethernet`, not `tx.ssh`: it transports the
**whole Ethernet link**, including ARP, TCP acknowledgments, IP fragments
and SSH on any port. There is no Ethernet-to-MeshCore forwarding.

The mode is off by default. It requires an explicit RF tuple; it never
chooses a frequency or starts WiFi automatically. For a dedicated lab link:

```text
set ethernet.radio 909.5,500,5,5,512
set ethernet on
get ethernet
get ethernet.radio
get radio2.timing
```

Use an appropriate frequency and permitted power for your network. Both
ends must match frequency, bandwidth, SF, coding rate and sync word.
Ethernet uses sync word `0x16`, explicit headers, normal IQ and a LoRa CRC.
The optional preamble defaults to 32 symbols; it can be set explicitly
(8–65528, subject to driver/timing limits). SX1276 requires SF7 or higher.

Do not assume 32 symbols is enough for a fast SF5/BW500 link. Run
`get radio2.timing` after its switching self-test completes, and accommodate
the slower end of the link. The lab example uses a 32.8 ms preamble and was
tested on a Heltec V4/V3 pair, not an I/O-expander-controlled board. Both
primary and secondary preambles matter if
primary MeshCore traffic must also be heard while time-sharing. Longer
preambles consume airtime; they do not prevent half-duplex collisions.

On that V4/V3 pair, the actual MeshCore-open SSH backend verified the remote
host key, authenticated with a public key, opened a PTY and received exactly
4096 test bytes with a matching SHA-256 check. Radio1 stayed on the USA
receive profile (`910.525,62.5,7,5`); its TX counter remained zero on both
boards. Raw Ethernet still had losses, so this demonstrates TCP/SSH recovery,
not loss-free RF or a minimum preamble guaranteed for every board. The bench
used an explicit test-image TX-frequency lock; it did not transmit test
packets on the USA profile.

The settings are saved in a checked atomic NVS blob. `set ethernet off`
closes the host connection and releases only this feature's runtime radio2
lease; it does not rewrite the saved MeshCore profiles. A manually configured
radio2 must already match the Ethernet RF tuple, including explicit preamble,
and be `rxtx`; its protocol is borrowed only while Ethernet is enabled.
An explicit saved `radio2 off`, a temporary/scheduled profile, or conflicting
`r2-`/`mt-` channels prevents automatic takeover. Those channel aliases cannot
be added while Ethernet is enabled. Clear conflicts or disable Ethernet first.
Only one secondary profile can be active: Meshtext and Ethernet cannot share
radio2 at the same time.

### Attach a Linux Ethernet interface

Connect the Companion to station WiFi. Its **station IP** exposes a single
KISS-over-TCP listener on port **5003**, separate from Companion binary TCP
5000, OTA TCP 5001 and the existing text terminal on TCP 5002.
Use [tncattach](https://github.com/markqvist/tncattach) on the host:

```sh
sudo tncattach -T -H 192.168.1.100 -P 5003 --ethernet --mtu 230 --noipv6 --ipv4 10.10.10.10/24
```

Replace the station IP. Attach the other end with a different IP in the
same subnet, then use normal host tools such as ping or SSH. A peer running
[MeshTNC's Ethernet-over-LoRa mode](https://github.com/datapartyjs/MeshTNC#ethernet-over-lora)
uses the same raw Ethernet framing; configure its PHY to match this link.
No custom `SSH` prefix or MeshCore packet wrapper is added.

**Enable only on a trusted LAN.** KISS has no authentication and permits raw
RF transmission. The service binds to the station address, not the setup AP;
do not expose port 5003 to the Internet. The ESP32 does not run an SSH server:
SSH connects the attached host computers.

The host MTU must fit one RF frame: MTU 230 plus the 14-byte Ethernet header
is 244 bytes. Ethernet frames of 14–255 bytes are supported; larger frames
are rejected, never truncated. There is no firmware fragmentation protocol,
compression, retransmission or autonomous Ethernet forwarding. Queues are
bounded and host TCP is backpressured. Host disconnects, WiFi/IP changes,
disabling and profile changes discard stale queued/partial host traffic;
an RF frame already transmitting cannot be unsent.

One transceiver time-shares reception between the two profiles. Short
preambles on an unmodified peer can be missed while listening to MeshCore;
TCP handles losses but performance is not equivalent to two physical radios.
Host/codec and firmware-build tests do not establish an RF interoperability
or throughput guarantee. nRF52 and non-Full Companion builds do not acquire
this Ethernet service. `tx.media` and tagged `tx.ssh` remain independent
MeshCore application policies; neither controls this raw Ethernet link.

## Companion channel-name routing aliases

Companions recognize the case-sensitive prefixes `r2-` and `mt-`, immediately
after the optional leading `#`. Keep the prefix in the app's displayed name.
For MeshCore aliases, the firmware strips exactly one prefix when deriving an
automatic hashtag key:

```text
#emergency       -> hashtag key for #emergency
#r2-emergency    -> the same key for #emergency
#mt-emergency    -> the same key for #emergency only in MeshCore fallback mode
```

Only the app's automatic, name-derived hashtag key is normalized; a supplied
private key is not replaced. Non-hashtag names such as `r2-Private` also retain
their supplied key. Empty or nested prefixes (`r2-`, `#r2-mt-emergency`) are
rejected. MeshCore hashtag names remain case-sensitive.

When radio2 is configured (`rx` or `rxtx`), `r2-` binds the channel to radio2 for
both send and receive, including emergency retransmissions. It overrides global
crossing and cannot be changed with `set tx.channel`; rename the channel to
remove the binding. RX-only radio2 cannot send. If radio2 is off/unconfigured,
ignore the routing prefix and use ordinary MeshCore routing instead. The key
stays the same as radio2 is enabled, disabled, or a temporary window expires.

If both the ordinary and `r2-` slots have the same key, a packet received on
radio2 selects the `r2-` slot; a packet received on radio1 selects the ordinary
slot. Global packet deduplication still delivers each packet once, not once in
each slot. Receiving a radio2-only channel on the wrong profile does not mark
that authenticated packet seen, so it can still arrive later on radio2.

The saved channel record retains the original displayed name and app-supplied
key for editing and sharing. Runtime copies resolve the actual wire key.
Repeater/room/sensor hashtag behavior is unchanged; those nodes can join the
canonical MeshCore channel (`#emergency`). Older Companion firmware does not
recognize these aliases.

### Local emergency-channel notice

When a Companion app adds `#emergency` (or `#r2-emergency`), the firmware seeds
its local chat with this notice, using `#EMERGENCY` as the sender:

```text
#EMERGENCY: This is an emergency channel. Do not use for testing or small talk.
```

This is a local onboarding message, not an RF transmission or an emergency
alert. It does not change the node's name. It is added only after the channel
is successfully saved, not on every connection, reboot, key edit, or alias
rename. Deleting and adding the channel again adds a fresh notice. Queued
real messages are delivered first: the notice waits for an empty receive
inbox and for held one-key DMs to be released, retrying as the app downloads
messages. If later traffic fills the inbox, the local notice is the first
channel message eligible for eviction. As with other pending received
messages, it is RAM-backed until downloaded by the app; a reboot before
download can discard it.

### Meshtext channel chat

`MESH_ENABLE_MESHTEXT_CHAT` enables the text-only Meshtext backend in the
Companion. It defaults on for **Full Companion** builds on ESP32 and nRF52,
including classic ESP32, C3, C6, S3, T1000-E, XIAO nRF52, RAK3401, Nano G2,
T114 and the other Full families. The installed radio driver must support
Meshtext and the selected frequency/modulation. An ESP-NOW primary, non-Full
builds, and other platforms default off; an explicit build override is retained.
This default replaces the earlier ESP32-S3/SX1262 and RAK4631-only scope.
Older images can therefore lack the backend even when their channel editor
accepts an `mt-` name; a factory reset cannot add code omitted from an image.

The common default and modem support do not establish physical qualification
for every board. Each release image must still pass its flash and runtime RAM
budgets, and hardware interoperability must be checked separately.
For example, [LLCC68 modulation limits](https://www.semtech.com/products/wireless-rf/lora-connect/llcc68)
exclude LongFast (SF11 at BW250): enabling the backend cannot add that modem
combination. Compatible presets such as MediumSlow or LongTurbo remain available.

In an enabled build, an `mt-` name always requests Meshtext, even if radio2 is
off or setup is blocked. It never falls back to sending MeshCore frames. In a
feature-disabled build, `mt-` remains an ordinary MeshCore naming alias while
radio2 is off; with radio2 configured, MT channel changes/sends are unsupported.
The `r2-` MeshCore behavior above is unchanged in either build.

With the region configured, join `#mt-longfast`, for example, through the app's
normal hashtag-channel editor. A recognized preset name selects the stock
peer channel name (`LongFast`) and, **only for an automatically
name-derived hashtag key**, the expanded public `AQ==` key. This public key is
not a secret. An explicitly
supplied private AES-128 or AES-256 key stays private and unchanged. A custom
name such as `mt-Private` uses the exact tail (`Private`) as its wire name and
the supplied key; custom wire names are limited to 11 bytes and need a manual
radio2 profile or another MT channel with a recognized preset. Preset names
are ASCII case-insensitive; the `mt-` marker is still case-sensitive.

Recognized aliases and modulation are:

| Alias tail | Stock wire name | BW (kHz) | SF | CR |
| --- | --- | ---: | ---: | ---: |
| `shortturbo` | `ShortTurbo` | 500 | 7 | 5 |
| `shortfast` | `ShortFast` | 250 | 7 | 5 |
| `shortslow` | `ShortSlow` | 250 | 8 | 5 |
| `mediumturbo` | `MediumTurbo` | 500 | 9 | 5 |
| `mediumfast` | `MediumFast` | 250 | 9 | 5 |
| `mediumslow` | `MediumSlow` | 250 | 10 | 5 |
| `longturbo` | `LongTurbo` | 500 | 11 | 8 |
| `longfast` | `LongFast` | 250 | 11 | 5 |
| `longmod` or `longmoderate` | `LongMod` | 125 | 11 | 8 |
| `longslow` | `LongSlow` | 125 | 12 | 8 |

The selected region must have room for the bandwidth; for example, 500 kHz
presets are unavailable in the supported EU868 plan. Use these CLI commands:

```text
get mt.status
get mt.region
get mt.slot
get mt.hops
get mt.retry
get mt.rebroadcast
get mt.node
get mt.shortname
set mt.node !12345678
set mt.node default
set mt.region auto
set mt.region us
set mt.slot 0
set mt.hops 7
set mt.rebroadcast off
set mt.shortname HALO
set mt.shortname default
send mt.nodeinfo 0
get radio2
set radio2 auto
```

`mt.region` accepts `auto`, `us`, `eu868`, `eu433`, or `anz`. `auto` infers US
only from a primary frequency at least 902 MHz and below 915 MHz, EU868 from
869.4–869.65 MHz, and EU433 from 433–434 MHz. The US/ANZ overlap at 915–928 MHz
is ambiguous: explicitly select `us` or `anz`. `mt.slot` is a 1-based frequency
slot, or `0` for the stock preset-name-derived slot; unavailable slots are
blocked. Check `get mt.status` after configuration; a successful settings save
does not mean the MT backend is active.

Outgoing MT messages default to a hop limit of **7**. `set mt.hops <0..7>`
changes this budget, and `get mt.hops` or `get mt.status` shows it. `0` sends
only the initial over-air hop with no forwarding budget; larger values permit
protocol-compatible peers to forward within that budget. The Companion itself
does not relay MT packets unless explicitly enabled as described below.

`get mt.node` shows the effective Meshtext sender node ID. By default it is
SHA-derived from the MeshCore public identity and remains stable for that
identity. `set mt.node !12345678` sets a saved override; exactly eight hexadecimal
digits are required, with or without the leading `!`. Input is hexadecimal,
not decimal, and is never silently truncated. IDs `00000000` through `00000003`
and `ffffffff` are reserved and rejected. `set mt.node default` clears the
override and restores the identity-derived ID.

The example ID is illustrative: choose an ID unique among your protocol-compatible
peers. Collisions can merge different senders into one identity and interfere
with packet deduplication. Changing the MT ID does not alter the MeshCore
private key, reset the outgoing packet-ID sequence or send automatic traffic.
Settings changes are refused while an MT transmission is
pending. Neither node IDs nor text labels are authenticated.

`set mt.shortname <label>` saves a Meshtext-only NodeInfo name of 1–4 Unicode
scalar characters, with valid UTF-8 and at most 16 encoded bytes. Combining
characters count separately. `get mt.shortname` shows the saved/effective
name. Stock NodeInfo accepts only four UTF-8 **bytes**, so the effective wire
name is the longest complete UTF-8 prefix that fits. The saved value is not
changed. `set mt.shortname default` clears the override and uses the last four
hexadecimal digits of the effective MT node ID. This does not change the
MeshCore node name, add a label to chat text or transmit anything.

After the first chat packet actually finishes transmitting on a configured MT
channel, the Companion queues a separate, GPS-free `NODEINFO_APP` broadcast.
Automatic metadata waits for a 250 ms gap after the most recent successful MT
transmission, including retries, so a stock peer can re-arm its receiver.
Explicit `send mt.nodeinfo` does not wait for this automatic-metadata gap.
This happens once per used channel and sender identity per boot, not for every
chat message. A rejected or failed chat send does not trigger it. There is no
NodeInfo transmission just from booting, receiving traffic, changing settings
or joining a channel, and there is no periodic NodeInfo timer.
Changing the node name cancels old-name metadata retries; the next new
successful chat announces the updated name without sending from the rename.

`send mt.nodeinfo <channel slot>` still explicitly queues **one** standard
`NODEINFO_APP` broadcast on the selected configured MT channel. The slot is
the Companion channel index, not `mt.slot` (the frequency slot). The packet
contains the actual sender ID, the global MeshCore node name as its long name
(UTF-8-safe truncated to 24 bytes), the effective short name, `CLIENT_MUTE`
by default (`CLIENT` when `mt.rebroadcast` is on), and the no-DM flag.
Both automatic and explicit NodeInfo omit GPS/coordinates, MAC, public keys
and unsupported metadata. The usual radio2
TX-mode, pending-TX, airtime and profile guards apply. The reply says
**queued**, not delivered or acknowledged.

Received NodeInfo is still ignored; this backend does not maintain a node
database.

Outgoing Meshtext chat and automatic or explicitly requested NodeInfo set
**OK to MQTT** on by default, allowing a compatible receiving gateway to upload those packets.
This does not start an MQTT client, bridge networks, request a reply, or mark
the packet as having arrived through MQTT. The 228-byte text limit is unchanged.

Region, frequency slot, hop limit, shortname, node-ID override and rebroadcast
choice are saved atomically together; changing one preserves the others.
Current 40-byte `MT04` settings include all of them. New firmware accepts valid
legacy `MT01`/`MT02` settings and defaults the shortname to the sender-ID suffix and
the node ID to the MeshCore identity; `MT01` also defaults the hop limit to 7.
Valid `MT03` settings preserve both overrides. All legacy versions default
rebroadcasting to off. Older MT firmware cannot read `MT04`, so downgrading
does not preserve access to these settings. Invalid or unsupported stored settings block MT operation
rather than silently selecting another network.

Private channels can be provisioned directly through the Companion CLI:

```text
set mt.preset <longfast|shortturbo|...>
set mt.channel <slot> <mt-name> <32/64 hex digits|AQ==>
get mt.channel <slot>
delete mt.channel <slot>
```

`set mt.preset` accepts the recognized presets above and explicitly saves a
manual `rxtx` radio2 RF tuple using the current region and frequency slot. It
is not an automatic owner: deleting the channel does not remove that saved RF
configuration. Set region/slot first; changing them later does not overwrite
the manual frequency, so rerun `set mt.preset` if you intend to retune it.

`set mt.channel` accepts a 32-digit hexadecimal AES-128 PSK, a 64-digit
hexadecimal AES-256 PSK, or the literal `AQ==` for the public key. It does not
accept arbitrary Base64 private keys. The stored format interprets an all-zero
upper 16-byte half as AES-128, including in a 64-digit input; use a nonzero
upper half for AES-256. Slot numbers are zero-based and build-specific, as in
the app. `get mt.channel` reports name, wire name and AES size, never the PSK.
The firmware console masks key input echo; keep your client-side logs and
stored keys private as well. SET and DELETE save transactionally and restore
the previous channel if storage fails. DELETE applies only to an MT channel,
not a MeshCore slot.

For a private channel named `Team` on a protocol-compatible peer:

```text
set mt.region us
set mt.preset longfast
set mt.channel 1 mt-Team <your-32-or-64-hex-digit-key>
get mt.channel 1
get mt.node
set mt.shortname HALO
get mt.status
send mt.nodeinfo 1
channel 1 Hello
```

Replace the key placeholder with your actual hexadecimal PSK and send after
`get mt.status` reports active. The peer must use the same preset, frequency,
PSK and exact case-sensitive wire name `Team`; `mt-` is only the local marker.
Alternatively, `set mt.channel 1 mt-longfast <your-key>` preserves a private
PSK but uses wire name `LongFast` and can acquire automatic RF without
`set mt.preset`, provided radio2 automatic ownership is permitted. This route
is available for both 128-bit and 256-bit private keys even when the stock app
cannot provision or export the latter.

When radio2 was never manually configured, a recognized preset can acquire an
automatic `rxtx` profile. Its RF tuple is RAM-only and stays available for RX
while the required channels exist. Removing the last MT channel, losing its
last recognized-preset dependency, or detecting a conflict releases only the
automatic profile. Explicit manual RF settings are retained: named aliases
must match their BW/SF/CR and fit the selected region, but do not overwrite
their frequency. The runtime MT overlay uses its required 16-symbol preamble;
the saved manual preamble is retained.

With a Meshtext secondary, an automatic primary MeshCore preamble keeps its
normal SF/BW-derived wire length (32 symbols for the USA SF7/BW62.5 preset),
so scan recommendations do not silently lengthen normal-network transmissions.
An explicit primary preamble is still honored. Automatic sizing for two
MeshCore profiles is unchanged; mixed-network reception remains best-effort
and time-shared.

`set radio2 off` is an explicit manual veto, including after reboot. Older
saved off profiles are conservatively treated as manual. Local
`set radio2 auto` clears the saved manual RF/off choice and permits automatic
ownership after two seconds; it rejects pending changes, leases and schedules
rather than cancelling them. `get radio2` distinguishes `auto-owned`, `off (manual)`
and `off (automatic allowed)`. Temporary profiles, scheduled changes and reply
commit barriers take precedence and can suspend MT operation. Automatic
cleanup does not undo later manual edits.

All MT channels share this one secondary modem profile. Different recognized
presets, or mixing any MT and `r2-` channels, are rejected instead of selecting
whichever channel was edited last. Loaded conflicts also block MT traffic.
Neither `radio2.cross on` nor MeshCore channel routes cross MT frames between
protocols.

The app uses its existing channel index/text commands and receive events.
Outgoing MT chat is plain text exactly as entered, with the sender node ID in
the standard radio header. It contains no custom name envelope or NodeInfo.
Incoming text uses the exact sender-ID prefix `!12345678: ` before the received
payload when passed to the MeshCore app. Changing the shortname only changes
future NodeInfo, not chat or the packet's sender ID;
`set mt.node` changes the actual Meshtext sender ID. Neither setting change
emits advertisements. A successful
send response means **queued**, not acknowledged by a remote node. This backend
does not relay MT packets by default. It does not provide ACKs, administrative
commands, direct messages, periodic node-info adverts or a node database. MeshCore emergency forwarding
does not apply to MT traffic. Interoperability is with stock peers' PSK channel
text using AES-CTR, not AES-CCM. AES-CTR channel traffic is not authenticated;
successful decoding is not proof of sender identity.

The codec supports AES-128 and AES-256 and up to 228 UTF-8 bytes of wire text,
without an added label/node-ID prefix. The standard app channel editor
still provisions 16-byte keys. An MT-only 32-byte binary SET/opt-in GET
extension is available for capable clients, with protection against legacy
16-byte edits truncating an existing 256-bit key; see
[Companion protocol](companion_protocol.md#4-set-channel). This does not give
stock apps seamless 256-bit provisioning or sharing. Current V3 Companion
receive events carry at most 165 text bytes **including the node-ID prefix**;
longer incoming MT text is UTF-8-safe truncated for the app.

Radio1 and radio2 still time-share **one physical radio**. The device cannot
receive either network while tuned to the other or transmitting; long MT
airtime, particularly LongFast, can leave substantial MeshCore receive gaps.
The compatible protocol's fixed short preamble also does not provide the scan-aware long
preamble described below. Do not treat this as continuous coverage of both
networks or guaranteed message delivery.

#### Heard-repeat retries

MT channel messages default to **15 total attempts** without a heard repeat:
the initial send and up to 14 retries. They use the Companion's saved
`flood.retry.count`, capped at 14 extra transmissions for MT text. Lower saved
counts retain their extra-retry meaning; `0` disables retries. MeshCore's retry
count meaning is unchanged. Locally originated MT traffic is hop zero for the
`flood.retry.path` gate, so a gate of `0` still permits it; the group-data gate
does not apply to channel text. `mt.hops 0` disables heard-repeat retries
because peers cannot forward a packet with no remaining hop budget; the
original chat and its first-use NodeInfo still transmit normally.
Retries keep the exact encoded payload and
packet ID, allowing peers to recognize duplicates. They use conservative,
airtime-based flood spacing and the normal CAD and airtime limits rather than
transmitting continuously.

`get mt.retry` is read-only diagnostics: it reports retry transmissions,
matching heard repeats, exhausted retry sequences, pending work and the shared
configured retry `count`. Use `set flood.retry.count` to change the budget.

A matching over-air copy with a lower remaining hop limit cancels the retry
sequence. Merely receiving another message, an unchanged-hop copy or a packet
with the same ID but different payload does not. A heard repeat indicates a
compatible peer forwarded the packet; it does **not** confirm that a particular
person or app received it. This does not add DM delivery receipts or ACKs.

NodeInfo follows the metadata retry cap: at most one extra send, only when
`flood.retry.count` is nonzero and `flood.retry.advert` is `on`. Its wait adds
one minute to the airtime-based delay, matching the conservative self-advert
policy. Turning retry count off cancels retained retries. Channel, identity,
settings or radio-profile changes invalidate retained work rather than sending
it on a different network. The bounded pending-frame storage may reject a new
send when full. Forwarding other people's decoded channel text is a separate,
default-off option below. MeshCore repeater prefix/bridge IDs and flood-filter
rules are not MT routing rules.

#### Optional decoded-text rebroadcasting

```text
get mt.rebroadcast
set mt.rebroadcast on
set mt.rebroadcast off
```

This saved option defaults to **off**. `mt.maybe_rebroadcast` is an alias for
`mt.rebroadcast`; `get mt.status` also shows the current choice.

When enabled, only broadcast **text successfully decrypted and parsed with a
configured channel's key**, and admitted to the local receive queue, is eligible.
Unknown channels, wrong keys, malformed packets, DMs, NodeInfo, GPS and other
non-text traffic are not relayed. A packet must have a valid remaining hop
budget. AES-CTR is unauthenticated: decoding does not authenticate its sender.

Before queuing a relay, the Companion waits **five additional packet airtimes**
calculated from that packet's actual length and the current radio2 settings,
plus the normal flood jitter. Existing CAD, receive-busy and airtime-budget
waits still apply; this does not replace or shorten them. Hearing another
forwarded copy while waiting cancels the redundant relay. An unchanged-hop
retry from the original sender does not cancel it.

The forwarded frame retains the original sender, packet ID, channel and
ciphertext; only remaining hops and the relay-node byte change. It is sent
at most once, without originated-message retries or an automatic local NodeInfo
announcement. At most one relay occupies the existing bounded chat delivery
slots, with no additional frame-sized buffer. A new local chat can displace
an unsent relay. Full queues skip the relay, and work delayed more than a
minute past its due time expires. Changes to the channel/key, identity or
radio profile cancel stale work instead of forwarding it on another network.
Turning the option off cancels pending relay work; settings edits are refused
while a radio transmission is already queued or in progress.

Relays stay on Meshtext radio2. This is not a MeshCore bridge, and ordinary
MeshCore forwarding rules remain unchanged. Enabling it does not add periodic
traffic or cause NodeInfo to be sent just because something was received.

## Choose whether traffic crosses between profiles

```text
get radio2.cross
set radio2.cross auto
set radio2.cross on
set radio2.cross off
```

| Active primary | Active secondary | `auto` | `on` | `off` (default) |
| --- | --- | --- | --- | --- |
| `radio` | `radio2` | Cross | Cross | Isolated |
| `tempradio` | `tempradio2` | Cross | Cross | Isolated |
| `radio` | `tempradio2` | Isolated | Cross | Isolated |
| `tempradio` | `radio2` | Isolated | Cross | Isolated |

This table describes MeshCore profiles. A Meshtext secondary profile is
always isolated, including with `radio2.cross on`.

New configurations default to `off`. An explicitly saved `auto`, `on`, or `off`
setting is preserved across firmware updates and reboots.

These are the global rules used by Companion targets set to `auto`; an explicit
contact/channel choice above overrides crossing for its own outbound packets.
Recognized media uses the separate `tx.media auto` selection described above.
Ordinary crossing respects `rx`. The infrastructure-only `tx.reply ... force`
override above permits authenticated admin CLI replies on that profile.
With crossing allowed, a packet received there may still be forwarded on the
primary profile. Existing routing, forwarding and packet-filter settings apply.
An RX-only `tempradio2` also blocks locally generated OTA requests under
`auto` or `off`; they do not fall back to the normal primary channel. Use `rxtx`
for a fetching node that needs to exchange requests and data. OTA responses
also follow this crossing table; `tx.reply` does not apply to them.

## MCU power saving while scanning

On nRF52, `set powersaving on` also permits interrupt-driven MCU idle during a
settled `radio2` or `tempradio2` listening visit. The RF receiver stays on.
This includes a Meshtext LongFast visit (SF11 / 250 kHz), whose minimum visit
is about 37.7 ms. No extra command is needed.

The scanner first completes its switch self-test and both noise-floor
baselines. It polls again for pending packets, calibration, changed settings,
retries, or short visits. All normal USB, logging, bridge, OTA, queue and role
sleep guards still apply. An nRF52 periodic interrupt bounds each wait to
about 0.977 ms; admission reserves another 0.488 ms before the visit deadline
and is rechecked immediately before sleeping. If that wake source is not
available, scanning keeps polling.

ESP32 also permits MCU idle during settled long visits. It requires radio GPIO
wake and physically stopped WiFi/Bluetooth drivers. Its timer slices are at
most 10 ms, reserve 4 ms before the visit deadline, and require at least 2 ms
usable time. Absolute deadlines are checked again after wake-source setup;
expired visits, pending interrupts or setup failures retain polling. USB,
logging, active wireless services and the other role guards remain blockers.
Other MCU families retain polling until their timing is validated.

The integer admission gate requires at least 49 RTC2 ticks (about 1.495 ms)
remaining after the work checks. A configured visit must also cover the loop
work before that point. Eligibility is therefore per visit, not an SF/BW
cutoff: a fast SF5 secondary can still idle the MCU when the primary profile
gives it a sufficiently long listening visit.

For example, the RAK3401 bench with USA primary `910.525,62.5,7,5,32` and
secondary `909.5,500,5,5,rxtx,384` had roughly 9.4/21.3 ms visits. MCU sleep
was observed in 35% of 4265 debugger samples with power saving on, versus
zero of 2129 with it off. This is a CPU-state spot check, not a current or
Ethernet throughput measurement. Enabling power saving changed neither preamble.

Radio RX duty cycling remains suspended while scanning, then its previous
setting is restored when radio2 stops. Meshtext's fixed 16-symbol preamble
does not have the same sleep margin as a longer MeshCore preamble. Adding
RF sleep inside a scan visit would introduce extra blind time, so this
optimization saves MCU activity without shortening either listening visit.

## Preamble

Append the preamble to the end of a command:

```text
set radio2 910.5,500,8,5,rxtx,88
set tempradio2 910.5,500,7,5,rxtx,120,128
set radio 909.5,62.5,7,5,32
set tempradio 909.5,62.5,7,5,120,32
```

Existing primary `radio`, `tempradio`, `radioat` and `tempradioat` commands accept
the optional trailing preamble where those primary commands are supported by
the role. Getters include the preamble. Omit it, or use `auto`/`0`, to calculate
it automatically. Explicit values are used as entered, subject to the radio's
limits and safe airtime arithmetic.

Automatic dual-profile preambles round **up to a multiple of eight**, with a
32-symbol minimum and a 512-symbol maximum. When a second profile activates,
the radio first self-tests four successful RX-to-RX switches in each direction
using the actual tuples. It defers the test while receiving, transmitting or
busy, and never counts a failed switch. The 512-symbol automatic preamble is
provisional until the test completes. The largest successful hop, rounded up
with a **10% guard**, then supplies the per-switch allowance for both the
automatic preambles and the scan dwell. There is no fixed switch-time floor.
The measurement is reset when either profile changes, including when a
`tempradio2` session starts or ends; it is not saved to flash.
The rapid startup hops briefly interrupt reception on each channel; a new
packet beginning during this test may be missed. An already detected packet
defers the test instead of being interrupted.

Displayed preamble recommendations and warnings wait until the active pair has
run for at least two minutes, measured from its first successful RX-to-RX hop.
Four successful hops in each direction are still required. A pair change starts
a fresh wait; an unchanged or rejected request does not. Use a temporary lease
longer than two minutes if you want its recommendation before it expires.

This is a display-only delay. The bounded startup test, guarded maximum switch
budget, automatic preambles, ordinary scanning and MCU-idle behavior do not wait
two minutes. Live switch and scan diagnostics remain available, marked pending
with `wait=Ns` until advice is ready. Waiting does not discard an early maximum
or replace it with an average. No extra retunes are forced at the deadline; a
successful normal hop completes the display wait.

Each idle scan starts with **4.6 symbols on the slower
profile**, then spends the remaining budgeted time on the faster profile,
also with a **4.6-symbol floor**. Slower
means a longer LoRa symbol (`2^SF / bandwidth`), regardless of profile number.
The slower preamble sets the return deadline. The calculation allows two slow
visits within that preamble, reserving the self-tested, 10%-guarded switch time
per hop. There is no extra fixed main-loop allowance. Fast-channel transmit
preambles include sixteen acquisition symbols.
For a primary SF7 / 62.5 kHz profile with a 763 us measured maximum switch
(840 us after the 10% guard, from the BLE-on T1000-E trial):

| Secondary profile | Automatic secondary preamble | Previously tested explicit preamble | Primary preamble |
| --- | ---: | ---: | ---: |
| SF9 / 500 kHz | 32 | 32 | 32 |
| SF8 / 500 kHz | 40 | 64 | 32 |
| SF7 / 500 kHz | 64 | 80 | 32 |

With preamble 32 on that slower profile, its visit is 9.421 ms and the faster
visit is 21.667 ms, plus the actual switching time. Increasing the slow preamble
also increases the time available on the fast channel. If the symbols are equal
in length, the primary profile goes first. Detected packets hold the current
channel until reception finishes.

The SF8 / 500 kHz value is calculated from the same formula as every other
profile; the older pair-specific 88-symbol floor has been removed. The explicit
values come from [historical production validation](test-results/radio_profiles_validation.md):
the final fast-channel attempts each received 50/50 packets at 4.8 slow symbols.
Automatic values use the measured switch allowance below.
Those explicit-preamble tests used the older 4.8-symbol policy; they do not
validate the newer 4.6-symbol slow dwell. The
[4.6/0.3 ms two-profile bench](test-results/radio_dwell_policy_validation.md#hardware-context) received 197/200
at SF7/62.5 + SF8/500 with explicit 32-symbol preambles. The newer four-channel
5.1/6.1/7.7 tests also had misses, so this is a timing policy, not a claim of zero packet loss.
The automatic fast preamble covers the blind interval during the slow visit,
both switches and the acquisition margin. These settings do not guarantee
reception during overlapping packets or long pauses in the firmware loop.
**Transmitters on each channel also need a long enough preamble.** Changing the
receiver's setting does not lengthen packets sent by other nodes.

### Chirp calculator and warnings

`get radio2.timing` (alias `get radio.timing`) reports the active pair in
`radio,radio2` order: dwell in chirps, recommended preambles in symbols, and
the switching/loop allowances. During the two-minute advice wait it reports
`timing self-test pending`, the remaining wait and the current provisional
switch allowance/preambles, without a final `need=` recommendation. On nRF52 boards, enabling `radio2`
uses RTC2 for both retune timing and scan visits (about 31 us resolution);
turning `radio2` off stops RTC2 and returns to the usual `micros()` clock.
Previously, the nRF52 `micros()` fallback counted 1,024 Hz RTOS ticks: a real
sub-millisecond retune could be reported as exactly 977 us, and two ticks as
1,953 us. Those old numbers are timer artifacts, not measured RF costs.
For SF7/62.5 on `radio` and SF8/500 on `radio2`, one live BLE-on read returned:

```text
> chirps=4.60,42.84; need=32,40; switch=706us; loop=0us; test=4/4,4/4 ready; WARN recommended preamble: radio2=40
```

Later good hops in another BLE-on trial raised the measured maximum to 763 us
(840 us with the 10% guard); the recommendation stayed at 40 symbols.

The measured switch duration is wall time, so an interrupt during the hop is
included. Bluetooth's SoftDevice may still preempt application code; this
clock correction does not stop it. Ordinary Companion/USB work in the main
loop does not run inside the synchronous retune, but it can delay when the
next scan visit begins. The zero fixed loop allowance is not a guarantee of
lossless reception under arbitrary application load.

`WARN recommended preamble` identifies **each** profile whose recommendation
exceeds the standard 32 symbols or its explicitly selected value. For example,
with SF7/62.5 on the primary profile, `set radio2 910.5,500,7,5,rx,32`
initially replies with `timing self-test pending`. Once active and measured,
`get radio2` warns:

```text
> 910.500,500.000,7,5,rx,32; WARN recommended preamble: radio2=64; short override: radio2
```

Successful `set radio ...` replies also include the recommended preamble for
each affected profile, calculated with the requested settings. A `short override`
note identifies an explicit value below the estimate; it is not silently
rewritten. Warnings are also included in profile settings/getters, scan info,
temporary settings and schedule acknowledgments. These preamble-bearing replies
also include `switch=547us` (or the current measured value) after the self-test
for that exact profile pair. The first ACK for a new pair says `timing self-test
pending` instead: it is sent before activation, so it cannot contain the future
measurement. Query `get radio2` after activation to see the measured value beside
the resulting preamble. `off` disables the dual-profile
warning. `get radio2.status` shows self-test progress and the resulting guarded
switch budget; its separate `max` field remains a lifetime diagnostic that can
include failed/rolled-back retunes and is not used for calibration. If the
recommendation exceeds 512, it additionally reports
`WARN need >512`; an automatic preamble stays at 512, which may not be enough
for reliable reception on that pair. Explicit preambles are not rewritten and
still use the radio's ordinary validation limits. A timing readout does not
change settings or transmit a packet.

The shared `RadioProfiles::calculateChirpTiming()` function accepts both tuples,
optional visit times in microseconds, a per-switch allowance and loop margin.
Using microseconds throughout:

```text
T[i] = 2^SF[i] * 1000 / BW_kHz[i]
slow = profile with the longer T (radio wins ties)
L[slow] = max(requested_visit_us[slow], ceil(4.6 * T[slow]))
L[fast] = max(requested_visit_us[fast], ceil(4.6 * T[fast]))
O    = 2 * switch_us
C    = L[radio] + L[radio2] + O

P[slow] = round_up_to_8(max(32, 2 * C / T[slow]))
P[fast] = round_up_to_8(max(32, (L[slow] + O) / T[fast] + 16))
```

Automatic selection uses the minimum visits to choose a preamble, then allocates
the fast visit from half the slow preamble's duration. Diagnostics feed the
actual allocated visits back into the same function, including longer visits
caused by an explicit slow preamble. Too-short slow preambles that cannot fit
both minimum visits remain rejected by the existing configuration validation.

The automatic scheduler uses these same role-specific minima and computes:

```text
slow_dwell = ceil(4.6 * T[slow])
fast_dwell = max(ceil(4.6 * T[fast]),
                 floor(P_configured[slow] * T[slow] / 2 - slow_dwell - O))
```

For the tested SF7/62.5 + SF8/500 pair with slow preamble 32, this yields
9,421 us slow and 21,197 us fast (41.40 fast chirps) with the 1,075 us guarded
switch allowance above. Swapping `radio` and `radio2` swaps the allocations automatically.

Production scheduling and automatic preambles use the **self-tested maximum
successful switch plus 10%**, without an additional fixed loop allowance. Subsequent
successful hops can only raise that allowance until the next profile change.
The measured recommendation is rounded up to eight symbols and capped at 512
for automatic settings. The calculator and warnings retain the *uncapped*
recommendation so a slow board is not shown as safe merely because it hit the
limit. Older V4 tests peaked at 836 us (827 us in the 4.6/0.3 ms run), and
Indicator reached 8428 us; these are historical measurements, not default
budgets for other boards.
The model preserves two slow-profile return opportunities and the existing
16-symbol fast-profile acquisition allowance. These are conservative policies,
not a fitted success curve, and neither packet overlap nor arbitrary application
stalls can be made safe by a finite preamble recommendation.

See the [implementation and validation notes](test-results/radio_dwell_policy_validation.md).

## Schedule the second profile

Use UTC Unix timestamps (seconds), or `+N` for N whole minutes from now:

```text
set radioat2 910.5,500,8,5,rxtx,START[,PREAMBLE]
set tempradioat2 910.5,500,8,5,rxtx,START,END[,PREAMBLE]
get radioat2
get radioat2 1
get tempradioat2
get tempradioat2 1
del radioat2 1
del tempradioat2 all
```

Replace `START` and `END` with timestamps or `+N`, and the optional `PREAMBLE`
with a number; omit the square
brackets. Each schedule family has four slots. Times must be in the future and
within 24 days. Overlapping temporary second-profile sessions are rejected.
`radioat2` changes the saved second profile when it starts; `tempradioat2`
restores the saved profile at its end. Pending schedules do not survive reboot.

For example, `set tempradioat2 910.5,500,8,5,rxtx,+5,+15` starts in 5 minutes
and restores in 15 minutes (10 minutes active). Both offsets are measured from
the same node-clock snapshot when accepting the command, not from each other.
`set radioat2 910.5,500,8,5,rxtx,+5` schedules a saved change in 5 minutes.
Offsets must be positive whole minutes. Absolute and relative endpoints can be
mixed; bare numbers still mean UTC seconds. Existing clock requirements apply.

## Return to one profile

```text
set radio2 off
```

This saves the disabled state and cancels all second-profile temporary activity
and schedules.

```text
set tempradio2 off
```

This ends temporary second-profile activity, cancels its temporary schedules and
restores saved `radio2`. If the saved second profile is off, this returns to one
profile. Packets/retries bound to an expired or changed session are discarded.

## Saved switching controls

Supported radio HALs expose saved SPI clock controls and, where available,
separate controls for switching optimizations. SPI clock choices can be
available even when no other acceleration feature is supported. Available
acceleration features start enabled, including on supported boards that have
not had a hardware timing test. The
board's SPI default remains its selected default clock. Availability depends on
the radio, board bus, and compiled feature support; inspect the device first:

```text
get radio.fast
get radio.fast.caps
```

`get radio.fast` reports the selected SPI MHz, its actual clock in Hz, and each
feature as `on`, `off`, or `na` when unsupported. `get radio.fast.caps` lists
supported feature names and accepted SPI values (`spi=na` without clock
control). Settings affect the one
physical radio and both profiles. They are shared by the same CLI roles as
`radio2`, including Full Companion, and survive reboot and firmware updates.
Integrated STM32WL builds omit these controls because their radio interface
does not support the tuning features.

| Control | Values | Effect when enabled |
| --- | --- | --- |
| `radio.fast.warm` | `on`, `off` | Retain owned continuous-RX state between profile changes |
| `radio.fast.fs` | `on`, `off` | Use the synthesizer/FS shortcut for an eligible retune |
| `radio.fast.packet` | `on`, `off` | Reuse acknowledged unchanged receive-packet parameters |
| `radio.fast.iq` | `on`, `off` | Reuse the unchanged SX1262 IQ workaround state |
| `radio.fast.modem` | `on`, `off` | Reuse confirmed unchanged modem state/queries |
| `radio.fast.batch` | `on`, `off` | Apply SF, bandwidth and coding rate together |
| `radio.fast.buffer` | `on`, `off` | Transfer SPI command bytes in a buffer where supported |
| `radio.fast.spi` | `0`, `2`, `4`, `8`, `9`, `16` where supported | Select the radio SPI clock; `0` restores the board default |

For example, compare buffered transfers or a lower clock independently:

```text
set radio.fast.buffer off
set radio.fast.spi 4
get radio2.timing
set radio.fast.spi 0
set radio.fast.buffer on
```

The integer `9` selects 8.889 MHz (8,888,888 Hz); the rounded `9 MHz` label does
not request 9,000,000 Hz. ESP32 buses expose this choice when their tunable
radio HAL and clock limit support it. Check `get radio.fast.caps` on the device
and read the actual Hz with `get radio.fast.spi`. The current ESP32-C6 core
cannot select this clock, so ESP32-C6 does not offer `9`. Current nRF52,
RP2040, and STM32 radio buses also do not offer this choice.

`0` preserves board-specific defaults, including clocks that are not whole MHz.
Adding the `9` choice does not change those defaults. An unsupported feature or
clock is rejected. A feature saved as `on` only runs
when its normal ownership and safety checks allow it; disabling `warm` also
prevents its dependent RX shortcuts from being used. Buffered transfer support
is separate from clock support. Some nRF52 buses support an 8 MHz byte path
while reporting `buffer=na`.

For a broad comparison, `set radio.fast off` disables all supported acceleration
features and selects 2 MHz where available. `set radio.fast on` enables all
supported features and restores the board's default clock. These commands do
not change the chosen SPI controller or the pins and do not adjust CPU clock,
RX power saving, channel settings, or whether `radio2` is enabled.

Changes require local USB or BLE access. Valid changes made while the radio
is busy are saved once and applied automatically at a safe gap, including
between queued transmissions. Separate feature changes compose with the latest
accepted policy. `OK - saved; pending` means no repeat command is needed.
Unsupported settings and storage failures remain errors.

An immediately applied change restarts receive safely and verifies it before
saving; a receive or storage failure restores the prior policy. Switching
measurements restart after a policy change, so check `get radio2.timing` again
after its timing test completes. Packet and error history remains available.
The aggregate getter reports `pending saved policy` until the saved target is
applied. Hardware failures retry with a delay; a temporary radio owner retains
control until it finishes.

## Power saving and diagnostics

Dual mode suspends RX power saving and MCU idle sleep so receive visits can run
promptly. The saved RX power-saving setting returns when profile 2 is off.
The scan uses **normal receive**, following the
[CAD/RX measurements](test-results/cad_scan_validation.md), with the updated 4.6-symbol dwell on the slower
channel, followed by the remaining fast-channel visit. It does not use CAD for scanning.
The separate CAD check before an initial transmission still follows `cad`.

SX1262 dual-profile mode keeps the oscillator running in XOSC standby between
visits, so each retune does not need to restart the TCXO. It restores the prior
standby policy when returning to one profile, together with the saved RXPS
setting. Radio reinitialization temporarily uses RC standby until the TCXO has
been configured again. Other radio families retain their existing behavior.
This does not disable the TCXO supply or its initial startup delay, and it is
not conditional on 500 kHz bandwidth. Keeping the oscillator on uses additional
power during the short standby intervals.

The switch-time self-test is not a forced settling delay or a proof of RF
acquisition. The earlier standalone
[keep-warm scan experiment](test-results/mixed_scan_validation.md) averaged about 1.19 ms
of overhead per hop (scan-cycle time minus both receive windows, divided by
two). That experiment used a leaner receive path; it is not a measurement or
guarantee of the production implementation. Measure the target board before
shortening its timing margins or transmit preambles.

The [production-path XIAO timing test](test-results/radio_profile_switch_validation.md)
measured **1.434 ms average / 1.467 ms maximum** with warm standby, compared
with **3.166 ms average** using RC standby: about **1.732 ms saved per hop**.
It covered 12,000 retunes across SF7/8/9 at 500 kHz paired with SF7/62.5 kHz.
These measurements include observing BUSY low, but exclude application/UI
scheduling and are not a packet-delivery qualification or a guarantee for
other boards, especially those using I/O expanders.

A same-image follow-up batching SF/BW/CR into one modulation command reduced
warm switching from **1.444 ms to 1.158 ms average** (batched maximum **1.198 ms**),
saving another **0.286 ms / 19.8%**. RadioLib's modulation caches and LDRO
calculation remain synchronized; frequency, preamble, packet guards, and RX
restart are unchanged. A later [HIL-only screening](test-results/radio_profile_switch_validation.md#expanded-screening-redundant-rx-setup-and-spi-v5)
tested redundant standby/IRQ/buffer/packet setup and SPI transfer changes.
Its fastest candidate measured 0.535 ms mean on XIAO but 8.203 ms on the
Indicator, whose radio GPIOs go through an I2C expander. That production stage
opted XIAO S3 WIO and Indicator LoRa into buffered 8 MHz SPI
and a guarded fast-RX state machine. The `heltec_v4_kiss_modem` and
`Station_G3_ESP32_kiss_modem` targets use the same guarded path only for KISS
dual-profile scanning; their normal board roles keep their established
HAL/timing policy. V4 uses its existing 160 MHz role override and Station G3
uses its base 240 MHz setting, but both still need an exact-image hardware
timing check before a nominal 600 µs target is claimed. It skips redundant
standby, IRQ mapping,
buffer-base writes and modem queries only during an owned continuous RX-to-RX
retune. Preamble/packet parameters (including the IQ workaround), stale-IRQ
clearing, RX commands and BUSY waits remain. TX, CAD, sleep, reset, RXPS and
failed commands revoke reuse; failed RX startup rolls back the profile.
Sleep entry uses standby and wake restores the existing TCXO voltage/delay.
At that stage other variants retained their earlier SPI and RX policies. The
current saved controls and supported defaults are described above.
Within a valid owned fast-RX retune, identical acknowledged SF/BW/CR/LDRO
settings now reuse the previous modulation command: frequency-only hops omit
`SetModulationParams` (0x8B). A changed effective LDRO value also forces a write,
including when automatic LDRO policy changes. Ordinary setters and lost RX
context invalidate that acknowledgement; initial setup, reset, sleep, failed
commands and full RX setup conservatively reapply the tuple. Frequency,
preamble/packet setup, IRQ clearing and BUSY checks are unchanged. This follow-up
has [same-image hardware measurements](test-results/separated_radio_modulation_cache_validation.md):
frequency-only hops averaged 0.452 ms on XIAO and 7.601 ms on Indicator.
The mixed-bandwidth timing figures above predate it. Four-channel SF6 scans
still missed packets; faster switching is not proof of loss-free acquisition.
See [production and USB validation](test-results/radio_profile_switch_validation.md#production-integration-and-usb-recovery-v8)
for current measurements, lifecycle tests and limitations.
The Indicator result shows why each board now measures its own switch time.
That measurement adjusts automatic preambles and dwell, but it cannot verify
RF acquisition or lengthen packets transmitted by other nodes.

Single-channel noise-floor calibration and RSSI interference comparison are
suspended while scanning. Retries use the radio's preamble/header activity
indicators. LR2021 extra-SF side detectors are suspended and restored as well.

`get radio2.status` reports the active second-profile mode, MeshCore RX/TX counts
for each profile, switch count, switch failures, longest measured switch and
both effective preambles. A long switch time or rising error count indicates
that the chosen settings need investigation on that board.

`get radio2.scan` shows which profile goes first, the two idle receive-window
lengths in microseconds, and both effective preambles. It reports `off` in
single-profile mode.
