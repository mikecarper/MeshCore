# Repeater Flood Filtering and Moderation

This guide explains the Keymind repeater forwarding filters. The filters decide
whether this repeater retransmits a packet. They do not stop local reception,
packet logging, or MQTT observation.

Only flood routes are filtered:

- `0x00` / `ROUTE_TYPE_TRANSPORT_FLOOD` - flood routing with transport codes
- `0x01` / `ROUTE_TYPE_FLOOD` - unscoped flood routing

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
get flood.channel.scope
get flood.filter
get flood.moderation
```

The `flood.filter` and `flood.moderation` tables each have 16 persistent slots.
A new `flood.filter` table starts with `ota all suspend=tempradio` in slot 1;
`flood.moderation` starts empty. A row can opt into `suspend=tempradio`;
temporary radio is not synonymous with OTA and can carry normal packet types
too. A corrupt or truncated table fails open, so corrupt storage does not
silently enable blocking.

## Force unscoped floods into a transport scope

`flood.channel.scope` can convert a received unscoped flood into a
transport-scoped flood before this repeater forwards it:

```text
set flood.channel.scope <channel|txt:*|login:*|other:*> <region>
set flood.channel.scope.<slot> <channel|txt:*|login:*|other:*> <region>
get flood.channel.scope
get flood.channel.scope.<slot>
del flood.channel.scope.<slot>
del flood.channel.scope all
```

The channel may be `public`, `#channel`, or a 128/256-bit hex key. The region
must already exist and provide a usable transport key. Keyed rules first check
the one-byte channel hash carried in the packet, then validate the MAC by
decrypting with the configured channel key. A hash collision alone cannot
force a scope.

There are three independent wildcard classes:

- `txt:*` handles otherwise-unmatched `GRP_TXT` and `GRP_DATA`; plain `*` is
  its alias.
- `login:*` handles `REQ`, `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH`.
- `other:*` handles every remaining flood payload type except TRACE, including
  OTA. TRACE is deliberately exempt from forced-scope wildcards.

`login:*` and `other:*` classify only the visible outer payload type; they do
not authenticate its contents. Exact channel rows with usable target regions
always take precedence over `txt:*`, even if that wildcard has a lower slot
number. A missing or unusable target is skipped, so later exact rows and then
the applicable wildcard are tried. Within a wildcard class, the lowest usable
duplicate row wins.

On a successful match, the route changes from `ROUTE_TYPE_FLOOD` to
`ROUTE_TYPE_TRANSPORT_FLOOD`. Transport code 0 is calculated with the target
region key over the payload type and payload; code 1 remains zero. The change
happens before region enforcement, forwarding filters, and deduplication.
Consequently `flood.max.unscoped` no longer applies to the rewritten packet,
but `flood.max`, target-region permissions, `flood.filter`, channel blocking,
loop detection, and moderation still do.

Already-scoped floods and direct routes are never rewritten. TRACE is never
rewritten even in flood form; its existing code, if any, is preserved and it
bypasses region/unknown-code enforcement. Scope assignment also does not
override normal payload validation or make an otherwise non-forwardable packet
type forwardable.

LoRa OTA (`0x0C`) falls under `other:*`. A matching row adds the selected
transport code, but OTA still operates normally during the temporary-radio
window because the OTA handler accepts both unscoped and transport-scoped flood
routes. The target region must allow flooding. A new repeater also seeds
`ota all suspend=tempradio` in flood-filter slot 1. That visible rule blocks OTA
forwarding at every received hop outside temporary-radio operation and is
skipped while temporary radio is active. Independently, the OTA core refuses
OTA receive, relay, and transmit outside an actually active temporary-radio
window, even if the seeded row is deleted or replaced.

Capacity is selected at build time:

- Roomy ESP32 builds: 255 slots, 9,180 bytes RAM, 9,185-byte file.
- DRAM-tight classic ESP32 LoRa-OTA repeaters, nRF52, and other normal
  constrained builds: 31 slots, 1,116 bytes RAM, 1,121-byte file.
- Very-tight STM32WL builds: 15 slots, 540 bytes RAM, 545-byte file.
- The no-PSRAM LilyGo T-LoRa V2.1 repeater/observer: 4 slots, 144 bytes RAM,
  149-byte file. This minimum holds the three wildcard classes and one exact
  channel mapping.

The region map still has 32 named-region entries. Large ESP32 tables can map
many channels to the same targets, but cannot reference more than 32 distinct
configured region names.

### Interaction with duplicate detection

The seen-packet hash contains the payload type and exact payload bytes. It does
not contain the route type, either transport code, or the ordinary flood path.
For `TRACE` only, the encoded `path_len` byte is also included. Therefore an
unscoped packet and the same packet after this repeater adds a transport code
are the same duplicate. A later copy with a different scope is also the same
duplicate; changing or adding scope cannot evade the seen table.

When equivalent non-TRACE flood copies overlap in `rxdelay`, the normal receive-quality timing
still chooses the packet to process, but that winner takes a scope from the
queued copies whose transport code matches an allowed region in this repeater.
Unknown and denied scopes are ignored. If eligible copies have different
scopes, the shortest received path supplies the scope. Equal path lengths
prefer the deepest matching child region (the narrowest configured scope). A
remaining tie keeps queue order. The winner keeps its own path, SNR, and delay
schedule; only its route and transport codes can change, including replacement
of a less-preferred scope it already carried.

Scope selection happens at dequeue so the original scopes remain available for
comparison. It applies only while copies are queued and cannot alter a copy
already processed into the seen table. TRACE is excluded from scope arbitration
entirely, so rxdelay never adds or replaces a trace transport code.

## Filter by payload type and received hop count

Use `flood.filter` when the packet type and its current path length are enough
to make the decision:

```text
set flood.filter <type> [hops] [suspend=tempradio]
set flood.filter.<slot> <type> [hops] [suspend=tempradio]
get flood.filter
get flood.filter.<slot>
del flood.filter.<slot>
del flood.filter all
```

Without a slot number, `set` reuses an identical rule, including its suspension
setting, or selects the first empty slot. With a slot number, it replaces that
slot. Omitting the hop expression means `all` (`0-63`).

On first initialization, flood-filter slot 1 is seeded with:

```text
set flood.filter.1 0x0C all suspend=tempradio
```

This is a normal editable row. After the table has been saved, deleting it
remains persistent across reboot; the firmware does not recreate it. Run the
same command to restore the exact seeded row, or omit `.1` to preserve existing
slot assignments and use the first empty slot. Operators may add
`suspend=tempradio` to any other row that should be skipped while the radio is
on a temporary channel.

Suspension does not approve a packet or bypass the rest of the filter table. It
skips that row, then evaluation continues with the next row and the remaining
forwarding gates. An ordinary `any` row therefore still applies during the
temporary-radio window, subject to the short-path remote-admin protection
below. `repeat`, `flood.max*`, region, loop-detection, and the OTA subsystem's
own hop limit also remain in force.

Standard traceroute uses direct routing and never enters `flood.filter`. For a
custom flood-form trace, catch-all `any` rows are deliberately ignored; only an
explicit `trace` row can match it. The stock core does not normally
flood-forward TRACE packets.

### Remote administration cannot be type-filtered on short paths

`flood.filter` uses two minimum filterable hop counts:

- `anon_req`, `path`, and `response` cannot be blocked at received hops `0-6`;
  configured rules begin applying at hop `7`.
- Flood `txt_msg` cannot be blocked at received hops `0-4`; configured rules
  begin applying at hop `5`.

`req`, `ack`, and multipart ACK have no special floor and remain filterable from
hop `0`.

A flooded login starts as `ANON_REQ`; its reply is commonly a `PATH` packet
carrying an encrypted `RESPONSE`. Before a direct return path is established,
administrative replies and CLI text can also be flooded. Transit repeaters do
not have the session key and cannot distinguish those encrypted admin exchanges
from ordinary peer packets with the same outer type. Each hop floor therefore
covers the complete outer packet class, not only packets that ultimately
authenticate as administrators.

This protects only against the configurable `flood.filter` table. It does not
override `repeat`, `flood.max*`, region enforcement, loop detection, or other
forwarding gates.

Hop expressions are based on the path count when this repeater receives the
packet:

- `N` matches exactly `N` received hops.
- `N+` matches `N` or more received hops.
- `N-M` matches the inclusive range.
- `all` matches `0` through `63` hops.
- `0+`, `all`, and omitting the hop expression are equivalent. `get` reports
  the stored range using the canonical spelling `all`.

Examples:

```text
# Stop forwarding group data once it arrives with four or more path entries.
set flood.filter grp_data 4+

# Stop long adverts, while still allowing shorter adverts.
set flood.filter.2 advert 6+

# Keep LoRa OTA floods from crossing this repeater at path counts 2 through 4.
set flood.filter.3 ota 2-4

# Apply a hard ceiling to flood payload types at 12 or more received hops.
set flood.filter any 12+
```

### High-traffic mesh example

This preset limits request and group-data propagation early while allowing the
login-capable response, anonymous-request, and path types to travel farther:

```text
set flood.filter req 3+
set flood.filter response 9+
set flood.filter 0x06 3+
set flood.filter 0x07 9+
set flood.filter path 9+
get flood.filter
```

| Rule | Stops retransmission when received with |
| --- | --- |
| `req 3+` | 3 or more path entries |
| `response 9+` | 9 or more path entries |
| `0x06 3+` (`grp_data`) | 3 or more path entries |
| `0x07 9+` (`anon_req`) | 9 or more path entries |
| `path 9+` | 9 or more path entries |

On a new table, the factory OTA rule occupies slot 1, so these unnumbered
commands normally fill slots 2 through 6. Existing tables may choose different
free slots. The `response`, `anon_req`, and `path` thresholds are above their
protected `0-6` range, so all five rules take effect at the thresholds shown.
These rules affect only retransmission by the repeater; local reception and
logging remain unchanged.

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

Decimal values `0` through `15`, hexadecimal values `0x00` through `0x0F`, the
full `PAYLOAD_TYPE_*` names, and `any` are also accepted. Upstream currently
reserves `0x0C`; this fork assigns it to LoRa OTA. Rows are suspended during
temporary-radio operation only when explicitly configured that way.

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

- `drop` - do not retransmit any matching message
- `rate=X/min` - retransmit at most `X` messages per local 60-second window
- `hops=N` - do not retransmit when the received path count is `N` or higher
- `path=H1[,H2,H3]` - require the first one to three path hashes to match
- `path=*` - match every path; this is the default

At least one of `drop`, `rate=X/min`, or `hops=N` is required. Rate and hop
limits can be combined. `rate=0/min` is equivalent to `drop`.

### Per-user, per-channel rate limits

Rate limits require an exact username; `*` is not accepted for a rate rule.
Username comparison is ASCII case-insensitive, and names containing spaces must
be quoted. A rule's counter is independent from rules for the same name on
other channels, so this directly supports "X messages per minute from user X
on channel Y." Counters are local to this repeater and reset on reboot.

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

1. `flood.channel.scope` first rewrites a matching unscoped flood packet.
2. `repeat`, `flood.max*`, and the channel-data gate are checked.
3. `flood.filter` checks payload type and hop range, subject to the login floor
   of `7` and flood-text floor of `5` described above.
4. `flood.channel.block` checks keyed channels.
5. Region and loop-detection rules are checked.
6. `flood.moderation` checks decrypted group text, username, rate, hops, and path.

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

ACL permission `4`, the region/scope-manager role, can read, add, replace, and
delete `flood.channel.scope` rows and manage regions. This lets the same
delegate create target regions and assign them to forced-scope rows.

## Security limitations

Public and hashtag channels use shared, well-known keys. A valid channel MAC
proves that the sender knew the channel key; it does not identify a person.
The `<sender>` value is an unverified display name and can be spoofed. Path
hashes are truncated routing hints and can collide or be manipulated; they are
not authenticated user identities.

Use username and path rules as traffic moderation, not as an authorization
boundary. For a strict network boundary, combine these tools with region ACLs,
private transport/channel keys, and controlled device access.

## Restore the factory-seeded rows

The repeater's factory-seeded forwarding rows can be restored through the CLI:

```text
set flood.channel.block.1 #wardriving h=4
set flood.filter.1 0x0C all suspend=tempradio
```

Each command explicitly replaces slot 1 in its own table. Inspect the slot first
if it may now contain another rule. To preserve existing slot assignments, omit
`.1`; the command then reuses an identical row or uses the first empty slot.

## Remove the custom rules

To save both tables in an empty state:

```text
del flood.filter all
del flood.moderation all
get flood.filter
get flood.moderation
```

This does not change the older `flood.max*`, channel-block, loop-detection, or
region settings; inspect or reset those separately when troubleshooting.
