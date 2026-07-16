# Repeater Flood Filtering and Moderation

This guide explains the Keymind repeater forwarding filters. The filters decide
whether this repeater retransmits a packet. They do not stop local reception,
packet logging, or MQTT observation.

Only flood routes are filtered:

- `0x00` / `ROUTE_TYPE_TRANSPORT_FLOOD` — flood routing with transport codes
- `0x01` / `ROUTE_TYPE_FLOOD` — unscoped flood routing

Direct routes `0x02` and `0x03` are never affected by these rules.
The route and payload values follow the upstream
[packet-format reference](https://docs.meshcore.io/packet_format/) and
[payload layouts](https://docs.meshcore.io/payloads/), with this fork's LoRa
OTA assignment noted below.

## Before making changes

Show the current forwarding controls:

```text
get repeat
get flood.max
get flood.max.unscoped
get flood.max.advert
get flood.channel.data
get flood.channel.data.hops
get flood.channel.block
get flood.filter
get flood.moderation
```

The `flood.filter` and `flood.moderation` tables each have 16 persistent slots.
They are empty by default. A corrupt or truncated table fails open, so corrupt
storage does not silently enable blocking.

## Filter by payload type and received hop count

Use `flood.filter` when the packet type and its current path length are enough
to make the decision:

```text
set flood.filter <type> <hops>
set flood.filter.<slot> <type> <hops>
get flood.filter
get flood.filter.<slot>
del flood.filter.<slot>
del flood.filter all
```

Without a slot number, `set` reuses an identical rule or selects the first empty
slot. With a slot number, it replaces that slot.

Hop expressions are based on the path count when this repeater receives the
packet:

- `N` matches exactly `N` received hops.
- `N+` matches `N` or more received hops.
- `N-M` matches the inclusive range.
- `all` matches `0` through `63` hops.

Examples:

```text
# Stop forwarding group data once it arrives with four or more path entries.
set flood.filter grp_data 4+

# Stop long adverts, while still allowing shorter adverts.
set flood.filter.2 advert 6+

# Keep LoRa OTA floods from crossing this repeater at path counts 2 through 4.
set flood.filter.3 ota 2-4

# Apply a hard ceiling to every flood payload type.
set flood.filter any 12+
```

Accepted payload names are:

| Value | Short name | Full name |
| --- | --- | --- |
| `0x00` | `req` | `PAYLOAD_TYPE_REQ` |
| `0x01` | `response` | `PAYLOAD_TYPE_RESPONSE` |
| `0x02` | `txt_msg` | `PAYLOAD_TYPE_TXT_MSG` |
| `0x03` | `ack` | `PAYLOAD_TYPE_ACK` |
| `0x04` | `advert` | `PAYLOAD_TYPE_ADVERT` |
| `0x05` | `grp_txt` | `PAYLOAD_TYPE_GRP_TXT` |
| `0x06` | `grp_data` | `PAYLOAD_TYPE_GRP_DATA` |
| `0x07` | `anon_req` | `PAYLOAD_TYPE_ANON_REQ` |
| `0x08` | `path` | `PAYLOAD_TYPE_PATH` |
| `0x09` | `trace` | `PAYLOAD_TYPE_TRACE` |
| `0x0A` | `multipart` | `PAYLOAD_TYPE_MULTIPART` |
| `0x0B` | `control` | `PAYLOAD_TYPE_CONTROL` |
| `0x0C` | `ota` | `PAYLOAD_TYPE_OTA` in this fork |
| `0x0D` | `13` | reserved |
| `0x0E` | `14` | reserved |
| `0x0F` | `raw_custom` | `PAYLOAD_TYPE_RAW_CUSTOM` |

The numeric values `0` through `15`, the full `PAYLOAD_TYPE_*` names, and
`any` are also accepted. Upstream currently reserves `0x0C`; this fork assigns
it to LoRa OTA.

## Moderate group text by channel and username

Use `flood.moderation` for flood `GRP_TXT` messages. The repeater validates and
decrypts the selected channel, extracts the display name before the first `:`,
then applies the rule:

```text
set flood.moderation <channel> <sender> <action> [action...]
set flood.moderation.<slot> <channel> <sender> <action> [action...]
get flood.moderation
get flood.moderation.<slot>
del flood.moderation.<slot>
del flood.moderation all
```

Channels can be specified as:

- `public` for the built-in Public channel
- `#name` for a well-known hashtag channel
- a 128-bit or 256-bit channel key in hexadecimal

The key is stored locally so packets can be authenticated and decrypted. It is
not included in `get flood.moderation` output.

Available actions are:

- `drop` — do not retransmit any matching message
- `rate=X/min` — retransmit at most `X` messages per local 60-second window
- `hops=N` — do not retransmit when the received path count is `N` or higher
- `path=H1[,H2,H3]` — require the first one to three path hashes to match
- `path=*` — match every path; this is the default

At least one of `drop`, `rate=X/min`, or `hops=N` is required. Rate and hop
limits can be combined. `rate=0/min` is equivalent to `drop`.

### Per-user, per-channel rate limits

Rate limits require an exact username; `*` is not accepted for a rate rule.
Username comparison is ASCII case-insensitive, and names containing spaces must
be quoted. A rule's counter is independent from rules for the same name on
other channels, so this directly supports “X messages per minute from user X
on channel Y.” Counters are local to this repeater and reset on reboot.

```text
# At most five Public-channel messages per minute from this display name.
set flood.moderation public "Noisy User" rate=5/min

# A separate limit for the same name on #local.
set flood.moderation #local "Noisy User" rate=10/min

# Combine a rate limit with a maximum forwarding distance.
set flood.moderation public alice rate=4/min hops=5
```

### Match the start of a path

Path matching accepts one-, two-, or three-byte hashes. Every hash in one rule
must use the same width, and matching always starts at the beginning of the
received path:

```text
set flood.moderation #local bot drop path=A1B2C3,D4E5F6
set flood.moderation public alice rate=3/min path=71
```

A path-qualified rule cannot match a zero-hop packet and does not match until
the packet contains all path entries listed by the rule.

## How the forwarding controls combine

A flood packet is retransmitted only if it passes every applicable control. In
other words, the controls combine as deny rules:

1. `repeat`, `flood.max*`, and the channel-data gate are checked.
2. `flood.filter` checks payload type and hop range.
3. `flood.channel.block` checks keyed channels.
4. Region and loop-detection rules are checked.
5. `flood.moderation` checks decrypted group text, username, rate, hops, and path.

The first denial is enough to prevent retransmission. A packet that is denied
can still appear in local logs or MQTT output. Moderation runs last because its
rate counters are charged only for packets that pass every other forwarding
control and will actually be retransmitted.

## Delegate filter management

ACL permission `5` is the filter-manager role:

```text
setperm <companion-public-key-hex> 5
```

A filter manager can read non-secret operational status and manage `repeat`,
`loop.detect`, `flood.max*`, `flood.channel.data*`, `flood.channel.block*`,
`flood.filter*`, and `flood.moderation*`. Delegated `get` access uses an
explicit allowlist: it cannot retrieve guest, WiFi, MQTT, bridge, or other
credentials, and it cannot change regions, ACL entries, radio settings, or
unrelated administrator settings.

## Security limitations

Public and hashtag channels use shared, well-known keys. A valid channel MAC
proves that the sender knew the channel key; it does not identify a person.
The `<sender>` value is an unverified display name and can be spoofed. Path
hashes are truncated routing hints and can collide or be manipulated; they are
not authenticated user identities.

Use username and path rules as traffic moderation, not as an authorization
boundary. For a strict network boundary, combine these tools with region ACLs,
private transport/channel keys, and controlled device access.

## Remove the custom rules

To return the two new tables to their default empty state:

```text
del flood.filter all
del flood.moderation all
get flood.filter
get flood.moderation
```

This does not change the older `flood.max*`, channel-block, loop-detection, or
region settings; inspect or reset those separately when troubleshooting.
