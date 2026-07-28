# Repeater Flood Filtering and Moderation

This guide explains the Keymind repeater forwarding filters. The filters decide
whether this repeater retransmits a packet and can assign a transport scope
before that decision. They do not stop local reception, packet logging, or MQTT
observation.

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
get flood.channel.scope.require
get flood.filter
get flood.moderation
```

The `flood.filter` and `flood.moderation` tables each have 16 persistent slots.
A new `flood.filter` table starts with `ota all suspend=tempradio` in slot 1;
`flood.moderation` starts empty. A row can opt into `suspend=tempradio`;
temporary radio is not synonymous with OTA and can carry normal packet types
too. A corrupt or truncated table fails open, so corrupt storage does not
silently enable blocking.

## Force floods into a transport scope

`flood.channel.scope` can add a scope to a received unscoped flood or replace
the scope of a transport-scoped flood before this repeater forwards it:

```text
set flood.channel.scope <channel|txt:*|login:*|other:*> <region> [tx=slow]
set flood.channel.scope.<slot> <channel|txt:*|login:*|other:*> <region> [tx=slow]
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

On a successful match, an unscoped route changes from `ROUTE_TYPE_FLOOD` to
`ROUTE_TYPE_TRANSPORT_FLOOD`; an already-scoped route remains transport-flood
but receives replacement codes. Transport code 0 is calculated with the target
region key over the payload type and payload, and code 1 becomes zero. The
change happens before region enforcement, forwarding filters, and
deduplication. Consequently `flood.max.unscoped` no longer applies to a packet
converted from unscoped, while `flood.max`, target-region permissions,
`flood.filter`, channel blocking, loop detection, and moderation still apply
to every rewritten packet. By default, if the selected scope differs and the
rewritten packet passes those checks, its initial retransmission uses zero
`txdelay` and the highest outbound queue priority so the newly scoped copy can
win at the next hop. Add `tx=slow` to use an effective inbound `rxdelay` base
of `max(2, configured rxdelay * 2)`, retain normal outbound queue priority, and
force the maximum `txdelay` factor of `2.0`. The actual transmit delay is still
randomized, from zero through ten packet airtimes.
It does not preempt an active radio transmission or bypass CAD and
airtime-budget limits. Selecting the scope already present is a no-op and does
not grant special treatment.

Direct routes are never rewritten. TRACE is never rewritten even in flood
form; its existing code, if any, is preserved and it bypasses
region/unknown-code enforcement. Scope assignment also does not override
normal payload validation or make an otherwise non-forwardable packet type
forwardable.

LoRa OTA (`0x0C`) falls under `other:*`. A matching row adds the selected
transport code or replaces the existing one, but OTA still operates normally
during the temporary-radio window because the OTA handler accepts both
unscoped and transport-scoped flood routes. The target region must allow
flooding. A new repeater also seeds `ota all suspend=tempradio` in flood-filter
slot 1. That visible rule blocks OTA forwarding at every received hop outside
temporary-radio operation and is skipped while temporary radio is active.
Independently, the OTA core refuses OTA receive, relay, and transmit outside an
actually active temporary-radio window, even if the seeded row is deleted or
replaced.

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

## Require valid incoming scopes only on selected channels

`flood.channel.scope.require` changes region enforcement for received flood
`GRP_TXT` and `GRP_DATA` packets from a global policy to a channel opt-in
policy:

```text
set flood.channel.scope.require <public|#channel|128/256-bit-key>
set flood.channel.scope.require.<slot> <public|#channel|128/256-bit-key>
get flood.channel.scope.require
get flood.channel.scope.require.<slot>
del flood.channel.scope.require.<slot>
del flood.channel.scope.require all
```

An empty table preserves the normal global region behavior. Once at least one
row exists, a group-channel packet that authenticates against a listed key must
arrive as `ROUTE_TYPE_TRANSPORT_FLOOD` with a transport code matching a locally
flood-allowed region. An unscoped packet, an unknown transport code, or a code
for a denied region is not retransmitted. The check uses the original incoming
scope before `flood.channel.scope` or `flood.filter scope=` can rewrite it.
Those rewrite actions are skipped for a rejected listed channel, so they
cannot rescue it or grant special receive/transmit timing.

Group-channel packets that do not authenticate against any listed key bypass
the region/unknown-code forwarding gate. They still pass through `repeat`,
`flood.max*`, `flood.filter`, `flood.channel.block`, loop detection, payload
validation, and moderation. Non-channel flood payload types retain the normal
global region behavior. A one-byte channel-hash collision is only a prefilter;
the packet must also pass MAC validation/decryption with the configured key.

Without `.slot`, setting an existing key updates its row and a new key uses the
first empty row. Numbered `set` replaces that slot. Detail output displays only
the first four derived hash bytes and key size, never the secret. The table has
the same build-dependent slot count as `flood.channel.scope`; each row consumes
34 bytes of RAM and storage, plus a five-byte file header. ACL permission `4`
can manage it.

For example, this requires an allowed incoming scope on `#bot`, while every
other group channel bypasses region enforcement:

```text
set flood.channel.scope.require #bot
get flood.channel.scope.require
```

### Interaction with duplicate detection

The seen-packet hash contains the payload type and exact payload bytes. It does
not contain the route type, either transport code, or the ordinary flood path.
For `TRACE` only, the encoded `path_len` byte is also included. Therefore an
unscoped packet and the same packet after this repeater adds a transport code
are the same duplicate. A later copy with a different scope is also the same
duplicate; changing or adding scope cannot evade the seen table.

When equivalent non-TRACE flood copies overlap in `rxdelay`, the normal
receive-quality timing still chooses the packet to process, but that winner
takes a scope from the queued copies whose transport code matches an allowed
region in this repeater.
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

A packet that already matches a configured fast `flood.channel.scope` or
`flood.filter scope=` action and needs its scope changed bypasses this inbound
`rxdelay` queue entirely. A `tx=slow` row remains in the queue with twice the
configured base, floored at `2.0`, and participates in normal queued-copy
scope arbitration.

## Filter by payload type, received hop count, and path

Use `flood.filter` when the packet type, current path length, or listed path
identifiers are enough to make the decision:

```text
set flood.filter.blacklist <ID[,ID...]>
set flood.filter.blacklist.<slot> <ID[,ID...]>
get flood.filter.blacklist
get flood.filter.blacklist.<slot>
del flood.filter.blacklist
del flood.filter.blacklist.<slot>
set flood.filter <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]
set flood.filter.<slot> <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]
get flood.filter
get flood.filter.<slot>
del flood.filter.<slot>
del flood.filter all
```

The blacklist holds up to 255 unique 3-byte repeater IDs on ESP32 builds and
18 on other builds. Each is written as six hexadecimal digits. For example:

```text
set flood.filter.blacklist A1B2C3,D4E5F6,112233
set flood.filter.blacklist.4 445566
set flood.filter any all path=blacklist
```

An unnumbered `set` replaces the list with up to 18 IDs, the largest command
that fits every CLI transport. A numbered `set` writes a batch of up to 18 IDs
beginning at an existing slot or the next consecutive slot. This is how an
ESP32 list grows beyond 18. Deleting a numbered entry compacts the entries
after it. Unnumbered `get` reports the total and prints the leading IDs that
fit; numbered `get` retrieves one specific entry.

`path=blacklist` is an unordered precondition on that row. With 3-byte path
hashes, one or more exact blacklist hits qualifies the packet. With 2-byte
path hashes, two or more received path entries must match the first two bytes
of listed IDs. Each received entry is counted at most once. A 1-byte path
never qualifies. The IDs may occur anywhere in the received path; neither
their list order nor their path order matters.

Without a slot number, `set` reuses a rule with the same match, scope,
requirement, and suspension settings, or selects the first empty slot. This
lets `tx=slow` or `tx=fast` change that rule's timing without creating a
duplicate. With a slot number, it replaces that slot. Omitting the hop
expression means `all` (`0-63`).

A row without `scope=` is the existing drop action. A row with `scope=` is a
scope-setting action instead: it adds transport scope to an unscoped packet or
replaces the codes on an already-scoped packet. The scope name is normalized
with a leading `#`, and the 128-bit transport key is derived directly from that
hashtag. The name does not need to exist in the region list and is not added to
it. Public names up to 30 characters are accepted; private `$` scopes are not.

Add `require=region` to a scope row when rewriting must not rescue a packet
that the incoming-region gate would reject. The repeater evaluates the packet's
original route before any rewrite in that receive pass. An incoming transport
scope must match a locally allowed region; an unscoped flood must be allowed by
the wildcard region. If the check fails, that scope row is skipped, the filter
does not grant its region bypass, and the unchanged packet is allowed to fail
normal region enforcement. Other independently configured scope rows still
apply in their normal order.

When multiple scope rows match, the lowest-numbered row wins. Scope rows do not
approve a packet: any matching drop row and every remaining forwarding gate can
still reject it. A filter-assigned scope is trusted without local region-list
validation, but `repeat`, `flood.max`, channel blocking, loop detection, and
moderation still apply. By default, a changed scope bypasses inbound `rxdelay`,
then is retransmitted with zero `txdelay` and the highest outbound queue
priority. Add `tx=slow` to use an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, retain normal queue priority, and force the
maximum `txdelay` factor of `2.0`; the randomized transmit delay ranges from
zero through ten packet airtimes. `tx=fast` explicitly restores the default.
Selecting the scope already present does not grant special treatment. Active
radio transmission, CAD, and airtime-budget limits are unchanged.

The blacklist and rule table are persisted separately. Deleting the blacklist
leaves `path=blacklist` rows in place but dormant until IDs are configured
again. Path hashes are truncated routing identifiers and are not authenticated
proof that a particular repeater handled a packet.

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
forwarding gates. An ordinary drop `any` row therefore still applies during
the temporary-radio window, subject to the short-path remote-admin protection
below. `repeat`, `flood.max*`, region handling, loop detection, and the OTA
subsystem's own hop limit also remain in force.

Standard traceroute uses direct routing and never enters `flood.filter`. For a
custom flood-form trace, catch-all `any` rows are deliberately ignored; only an
explicit `trace` row can match it. The stock core does not normally
flood-forward TRACE packets.

### Remote administration cannot be type-filtered on short paths

`flood.filter` drop actions use two minimum filterable hop counts:

- `anon_req`, `path`, and `response` cannot be blocked at received hops `0-6`;
  configured rules begin applying at hop `7`.
- Flood `txt_msg` cannot be blocked at received hops `0-4`; configured rules
  begin applying at hop `5`.

`req`, `ack`, and multipart ACK have no special floor and remain filterable from
hop `0`. Scope-setting rows do not block traffic and may apply within the
protected ranges.

A flooded login starts as `ANON_REQ`; its reply is commonly a `PATH` packet
carrying an encrypted `RESPONSE`. Before a direct return path is established,
administrative replies and CLI text can also be flooded. Transit repeaters do
not have the session key and cannot distinguish those encrypted admin exchanges
from ordinary peer packets with the same outer type. Each hop floor therefore
covers the complete outer packet class, not only packets that ultimately
authenticate as administrators.

This protects only against configurable `flood.filter` drop actions. It does
not override `repeat`, `flood.max*`, loop detection, or other forwarding gates.

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

# Assign #local scope to group text without requiring #local in the region map.
set flood.filter grp_txt all scope=local

# Rewrite only packets whose incoming region was already acceptable.
set flood.filter grp_data all scope=local require=region

# Rewrite matching blacklisted paths without fast-tracking their retransmission.
set flood.filter grp_data all path=blacklist scope=local tx=slow

# Drop matching flood types after the unordered path blacklist qualifies.
set flood.filter.blacklist A1B2C3,D4E5F6,112233
set flood.filter any all path=blacklist

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
set flood.filter control 1+
get flood.filter
```

| Rule | Stops retransmission when received with |
| --- | --- |
| `req 3+` | 3 or more path entries |
| `response 9+` | 9 or more path entries |
| `0x06 3+` (`grp_data`) | 3 or more path entries |
| `0x07 9+` (`anon_req`) | 9 or more path entries |
| `path 9+` | 9 or more path entries |
| `control 1+` | 1 or more path entries |

On a new table, the factory OTA rule occupies slot 1, so these unnumbered
commands normally fill slots 2 through 7. Existing tables may choose different
free slots. The `response`, `anon_req`, and `path` thresholds are above their
protected `0-6` range, so all six rules take effect at the thresholds shown.
The Control rule allows a flood received with path count `0` to be forwarded
once, then stops it at the next repeater. Normal node-discovery Control packets
are direct zero-hop packets and never enter `flood.filter`. These rules affect
only retransmission by the repeater; local reception and logging remain
unchanged.

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

1. `flood.channel.scope.require` evaluates a listed group channel against the
   original incoming scope; unlisted group channels bypass the later region
   gate while the table is active.
2. `flood.channel.scope` adds or replaces the scope of a matching flood packet.
3. A matching `flood.filter scope=` row may replace that result; its scope does
   not require a region-list entry.
4. `repeat`, `flood.max*`, and the channel-data gate are checked.
5. `flood.filter` drop rows check payload type and hop range, subject to the
   login floor of `7` and flood-text floor of `5` described above.
6. `flood.channel.block` checks keyed channels.
7. Region and loop-detection rules are checked; a filter-assigned scope is
   already trusted when it has no region-list match, except that it cannot
   rescue a channel rejected by `flood.channel.scope.require`.
8. `flood.moderation` checks decrypted group text, username, rate, hops, and
   path.

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
unrelated administrator settings. Because `flood.filter scope=` derives a
public hashtag key directly, a filter manager can configure that action without
region-manager permission; it still cannot edit the region hierarchy.

ACL permission `4`, the region/scope-manager role, can read, add, replace, and
delete `flood.channel.scope` and `flood.channel.scope.require` rows and manage
regions. This lets the same delegate create target regions, assign forced
scopes, and select the channels that require valid incoming scopes.

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
