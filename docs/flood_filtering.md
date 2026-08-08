# Flood Filtering and Moderation

This guide explains the Keymind forwarding filters. Repeaters expose the full
set of channel, rule, blacklist, and moderation controls described here.
FULL-profile ESP32 room servers expose the generalized `flood.rule` table (and
its `flood.filter` alias) with 31 forward-rule slots, but not the repeater's
scope-rewrite, passive-blacklist, or text-moderation phases. Standard room-server profiles do
not compile the rule table. Filters decide whether the node retransmits a
packet and can assign a transport scope before that decision. They do not stop
local reception, packet logging, or MQTT observation.

Only flood routes are filtered:

- `0x00` / `ROUTE_TYPE_TRANSPORT_FLOOD` - flood routing with transport codes
- `0x01` / `ROUTE_TYPE_FLOOD` - unscoped flood routing

Direct routes `0x02` and `0x03` are never affected by these rules.
The route and payload values follow the upstream
[packet-format reference](https://docs.meshcore.io/packet_format/) and
[payload layouts](https://docs.meshcore.io/payloads/), with this fork's LoRa
OTA assignment noted below.

## Before making changes

On a repeater, show the current forwarding controls:

```text
get repeat
get flood.max
get flood.max.unscoped
get flood.max.advert
get flood.channel.data
get flood.channel.data.hops
get flood.channel.scope
get flood.channel.scope.require
get flood.filter
get flood.rule
get flood.filter.blacklist
get flood.moderation
```

`flood.rule` is an alias for `flood.filter`, not another table. Generalized
repeater FPF7 has 32 forward-rule slots plus scope-rewrite and shared-blacklist
sections in the same atomic policy file. FULL room servers have 31 forward
slots and empty repeater-only sections. Compact target profiles retain their
separate FPF6-era controls. `flood.moderation` has 16 slots. A new
repeater FPF7 table starts with `ota all suspend=tempradio` in slot 1 and an
authenticated `#wardriving hops=5+` drop in slot 2; FULL room servers seed only
the OTA row. `flood.moderation` starts empty. A row can opt into
`suspend=tempradio`;
temporary radio is not synonymous with OTA and can carry normal packet types
too. A corrupt or truncated table fails open, so corrupt storage does not
silently enable blocking.

On a FULL ESP32 room server, use `get flood.rule` (or `get flood.filter`) for
the available table. Remote rule changes require room-server administrator
access. `flood.filter.blacklist*` and `path=blacklist` are repeater-only; use
the ordered `prefix=` condition on a room server.

## Force floods into a transport scope

`flood.channel.scope` can add a scope to a received unscoped flood or replace
the scope of a transport-scoped flood before this repeater forwards it:

```text
set flood.channel.scope <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]
set flood.channel.scope.<slot> <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]
get flood.channel.scope
get flood.channel.scope.<slot>
del flood.channel.scope.<slot>
del flood.channel.scope all
```

The channel may be `public`, `#channel`, or a 128/256-bit hex key. A bare
target names an existing region with a usable transport key. Use
`scope=<name>` instead to derive a regionless public hashtag scope exactly as
`flood.filter scope=<name>` does. The direct name is normalized with a leading
`#`, may contain up to 30 characters, and does not need a region-list entry.
Keyed rules first check the one-byte channel hash carried in the packet, then
validate the MAC by decrypting with the configured channel key. A hash
collision alone cannot force a scope.

For example, this authenticates only `#rgdata` and rewrites it to
`#BlackHole86` without creating a region:

```text
set flood.channel.scope #rgdata scope=BlackHole86
get flood.channel.scope.1
```

If that channel arrives scoped to `#usa`, the rule replaces `#usa` with
`#BlackHole86`. It also handles unscoped packets and replaces any other
incoming scope; the source scope is not a condition on the rule.

Add `path=blacklist` to make a channel-scope row eligible only when the
received path matches the passive `flood.filter.blacklist` ID table. It does
not require an enabled `flood.filter` drop row. With 3-byte paths, one exact
listed ID qualifies. With 2-byte paths, two received path entries must match
the first two bytes of listed IDs. A 1-byte path never qualifies.

Use `path=bucket:<1-6>` to match one of the existing
`flood.retry.bucket` tables instead. Each bridge bucket holds up to 17
three-byte IDs and remains usable by channel scoping while
`flood.retry.bridge` is off. Bucket matching uses the same thresholds as the
blacklist: one exact hit for 3-byte paths, two qualifying entries for 2-byte
paths, and no matches for 1-byte paths. Channel scoping reads the configured
IDs directly; `recent.repeater` freshness and `flood.retry.ignore` do not
change this match.

There are three independent wildcard classes:

- `txt:*` handles otherwise-unmatched `GRP_TXT` and `GRP_DATA`; plain `*` is
  its alias.
- `login:*` handles `REQ`, `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH`.
- `other:*` handles every remaining flood payload type, including flood-form
  TRACE and OTA.

`login:*` and `other:*` classify only the visible outer payload type; they do
not authenticate its contents. Exact channel rows with usable targets
always take precedence over `txt:*`, even if that wildcard has a lower slot
number. Within the exact class, matching path-qualified rows are tried before
ordinary fallback rows. The same qualified-then-fallback order applies within
each wildcard class. A missing or unusable target is skipped, so later rows
remain eligible. The lowest usable slot wins within each priority tier.

For example, this uses bridge bucket 1 to assign `east` to `public` packets
whose received 3-byte path contains `7576FB`, and assigns `west` to all other
authenticated `public` packets:

```text
set flood.retry.bucket 1 7576FB
set flood.channel.scope public west
set flood.channel.scope public east path=bucket:1
```

More 3-byte IDs can be added to bucket 1 later. Any one of them qualifies the
`east` row. Replacing or clearing that bucket changes which paths qualify but
leaves both channel-scope rows intact. Bridge retry does not need to be
enabled.

On a successful match, an unscoped route changes from `ROUTE_TYPE_FLOOD` to
`ROUTE_TYPE_TRANSPORT_FLOOD`; an already-scoped route remains transport-flood
but receives replacement codes. Transport code 0 is calculated with the target
region or direct hashtag key over the payload type and payload, and code 1 becomes zero. The
change happens before region enforcement, forwarding filters, and
deduplication. Consequently `flood.max.unscoped` no longer applies to a packet
converted from unscoped, while `flood.max`, `flood.filter`, loop detection, and
moderation still apply to every rewritten packet. A region
target must be flood-allowed. A regionless target is trusted for this matched
receive pass, but it neither creates a region nor changes how unrelated packets
with the same transport code pass the region gate. By default, if the selected scope differs and the
rewritten packet passes those checks, its initial retransmission uses zero
`txdelay` and the highest outbound queue priority so the newly scoped copy can
win at the next hop. Add `tx=slow` to use an effective inbound `rxdelay` base
of `max(2, configured rxdelay * 2)`, retain normal outbound queue priority, and
force the maximum `txdelay` factor of `2.0`. The actual transmit delay is still
randomized, from zero through ten packet airtimes.
It does not preempt an active radio transmission or bypass CAD and
airtime-budget limits. Selecting the scope already present is a no-op and does
not grant special treatment.

Direct routes are never rewritten. Standard traceroute is direct-routed and
therefore remains outside this flood-only table. A custom flood-form TRACE is
treated like every other flood: an applicable wildcard may rewrite it and the
normal region/unknown-code gates still apply. Scope assignment does not
override normal payload validation or make an otherwise non-forwardable
packet type forwardable.

LoRa OTA (`0x0C`) falls under `other:*`. A matching row adds the selected
transport code or replaces the existing one, but OTA still operates normally
during the temporary-radio window because the OTA handler accepts both
unscoped and transport-scoped flood routes. A region target must allow
flooding; a direct target uses the regionless trust behavior above. A new
repeater also seeds `ota all suspend=tempradio` in flood-filter
slot 1. That visible rule blocks OTA forwarding at every received hop outside
temporary-radio operation and is skipped while temporary radio is active.
Independently, the OTA core refuses OTA receive, relay, and transmit outside an
actually active temporary-radio window, even if the seeded row is deleted or
replaced.

Capacity is selected at build time:

- Roomy ESP32 builds: 255 rewrite slots and 32 regionless-target slots, 10,204
  bytes RAM.
- DRAM-tight classic ESP32 LoRa-OTA repeaters, nRF52, and other normal
  constrained builds: 31 rewrite and regionless-target slots, 2,108 bytes RAM.
- Very-tight STM32WL builds: 15 rewrite slots and one reusable regionless-target
  slot, 572 bytes RAM.
- The no-PSRAM LilyGo T-LoRa V2.1 repeater/observer: 4 rewrite and
  regionless-target slots, 272 bytes RAM. This minimum holds the
  three wildcard classes and one exact channel mapping.

Each rule retains its 36-byte record. A separate table holds 32-byte normalized
names for up to the smaller of the rule count or 32 distinct regionless
targets, except that very-tight STM32WL builds retain one reusable direct
target. Both configured regions and regionless targets can be reused by any
number of rules.

On generalized builds these records are the FPF7 rewrite phase, and the file
stores only through the highest occupied slot. Compact FPF6 builds retain the
standalone FCS5 file and the file sizes described by their build profile.

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
`flood.max*`, `flood.filter`, loop detection, payload
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
already processed into the seen table. Flood-form TRACE participates in the
same arbitration. Direct traceroute never enters this flood queue.

A packet that already matches a configured fast `flood.channel.scope` or
`flood.filter scope=` action and needs its scope changed bypasses this inbound
`rxdelay` queue entirely. A `tx=slow` row remains in the queue with twice the
configured base, floored at `2.0`, and participates in normal queued-copy
scope arbitration.

## Runtime flood rules

On repeaters with the rule engine enabled and on FULL-profile ESP32 room
servers, `flood.rule` and `flood.filter` are two names for the same persistent
table. The evaluator is fixed firmware, but every row is data, so an
authenticated operator can add, replace, inspect, or delete a row without an
OTA or reboot. Existing `flood.filter` commands remain compatible. Only FPF6
and FPF7 files are accepted; FPF1-FPF5 files are rejected and filtering fails
open. A row saved by the extended engine uses FPF7.

The former `flood.channel.block` table is now represented by ordinary FPF7
rows. On a generalized repeater, an existing FCB2 file is imported once into
free FPF7 slots and then removed. For example, an old `#wardriving h=4` row
becomes `type=any channel=#wardriving hops=5+ drop`. The 32nd forward slot
guarantees room to migrate the old global `flood.channel.data` gate even when
all 31 former general/channel slots were occupied. Compact STM32WL FPF6 builds
cannot match authenticated channels and retain the older separate gate.

On generalized repeaters, `flood.channel.data*` is a compatibility view over
one ordinary visible FPF7 `type=grp_data ... drop` row. Turning it off creates
or updates that row; turning it on removes the row. Its hop setting maps to
`hops=all` or `hops=N+1+`. There is no hidden GRP_DATA forwarding check ahead
of FPF7. Normal ordering applies, so a matching higher-priority `stop` row can
exempt selected traffic. The compact rule list marks the managed row with
`~data`.

FPF7 binds `in=region:<name>` and `region=<name>` to canonical region names,
not numeric region IDs. Removing, reordering, or reusing a region ID cannot
silently redirect a rule. If the saved name is missing, an input-region match
does not match and a target-region rewrite is skipped. Re-adding the same name
reactivates the rule.

The extended form is:

```text
set flood.rule[.<slot>] type=<type> [hops=<range>] [channel=<channel>]
    [prefix=<ID[,ID...]>] [in=<input-scope>] <action> [rate=<N>/min]
    [priority=<0-255>] [stop] [tx=fast|slow] [suspend=tempradio]
get flood.rule
get flood.rule.<slot>
del flood.rule.<slot>
del flood.rule all
```

The command must be entered on one line. Match fields in one row are ANDed.
Every row is matched against the same immutable packet state captured on
receive, before any rule rewrites its scope. Matching rows are then processed
by descending `priority`; lower slot number wins a priority tie. Priority
defaults to `0`.

The first matching `stop` row ends the FPF7 forward phase after that row. Higher-order
matches and the stop row still apply; lower-order matches do not. A stop-only
row is therefore an exception to lower-priority FPF7 rows. It cannot undo a
higher-priority drop and it does not bypass hard forwarding gates or the
scope-rewrite and moderation phases. Without a stop row, matching drop and rate rows remain
independent and the highest-order matching scope or region rewrite wins.

Match fields:

- `type=` accepts the same packet names and numeric values as legacy
  `flood.filter`. The positional form remains accepted.
- `hops=` accepts `all`, `N`, `N+`, or `N-M`. The positional form remains
  accepted. Received hops over 3 are written as `hops=4+`.
- `channel=*|public|#name|128-bit-key|256-bit-key` optionally narrows by
  channel. `channel=*` is an unconstrained wildcard: it performs no channel
  authentication and matches every payload selected by `type=`. Thus
  `type=any channel=*` means every flood payload type. `public`, `#name`, and
  raw keys authenticate one channel and narrow the row to `GRP_TXT` or
  `GRP_DATA`.
- `prefix=` is a source-path prefix containing one to three comma-separated
  pbyte IDs. Every ID must use the packet's pbyte width: 2, 4, or 6 hex
  characters for 1-, 2-, or 3-byte paths. Order matters and matching begins at
  the first received path entry. `path=<prefix>` is an alias;
  `path=blacklist` retains its separate unordered-list behavior.
- `in=any|none|scoped|allowed|unknown|scope:<name>|region:<name>` tests the
  original incoming route before any rewrite. `none` means an unscoped flood;
  `scoped` means any transport flood; `scope:name` compares the exact public
  hashtag-derived scope; and `region:name` compares an allowed configured
  region. `allowed` is the legacy `require=region` test and includes an
  unscoped packet when the wildcard region allows it. `unknown` means a scoped
  packet that does not resolve to an allowed local region.

Actions:

- `drop` prevents retransmission when the row matches. The strict
  `flood.rule` form requires an explicit action. For backward compatibility,
  only a legacy `flood.filter` row with no rewrite, rate, or stop action means
  drop implicitly.
- `scope=<name>` derives a public transport scope directly from the name; no
  region entry is consulted. `scope=BlackHole86` is therefore a valid
  regionless sink. `region=<name>` is different: it resolves a configured,
  flood-allowed region and one of that region's transport keys.
- A `stop` on a row whose `region=` target is currently unusable is also inert,
  allowing lower-priority safety rules to run. Direct `scope=` targets do not
  have this configuration dependency.
- `rate=N/min` is a per-node, per-row fixed one-minute forwarding limit. It can
  stand alone or accompany a scope/region rewrite. Quota is charged only after
  every other forwarding gate, including moderation, accepts the packet. It is
  not keyed per sender; use `flood.moderation` when a group-text rate must be
  tied to an exact display name.
- `priority=0-255` controls processing order. Higher values run first; lower
  slot number breaks ties. `pri=` is the compact alias.
- `stop` (or `action=stop`) applies this row and prevents lower-order FPF7 rows
  from acting. It can stand alone or accompany drop, rewrite, or rate.

When several rows use the same channel key, authentication is performed once
for that packet and reused by those rows. This cache lives only for the current
receive evaluation; it is not persisted and never stores plaintext or a
password.

The exact requested examples are:

```text
# If #rgdata arrives unscoped with more than 3 received hops, add
# the regionless #BlackHole86 scope.
set flood.rule.2 type=grp_data hops=4+ channel=#rgdata in=none scope=BlackHole86

# Rewrite the exact incoming #usa scope to #BlackHole86 for #rgdata.
set flood.rule.3 type=grp_data channel=#rgdata in=scope:usa scope=BlackHole86

# Match a two-byte source-path prefix and cap forwarding at 10 per minute.
set flood.rule.4 type=any prefix=860C rate=10/min

# Keep authenticated #rgdata at two hops or less out of lower-priority FPF7
# rules. Hard gates and separate tables still apply.
set flood.rule.5 type=grp_data hops=0-2 channel=#rgdata priority=200 stop

get flood.rule.2
get flood.rule.3
get flood.rule.4
```

The 240 KB STM32WL profiles keep `MESH_ENABLE_FLOOD_RULE_ENGINE=0` and retain
the persistent compact FPF6 `flood.filter` and blacklist syntax below. They
still perform filtering, but omit the generalized `flood.rule` parser and
extended fields. No partition size is changed by this feature.

The compatible filter and blacklist commands are:

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

This is intended for abuse containment, such as refusing to retransmit floods
that repeatedly enter the mesh through known internet gateways dumping bulk
traffic. The list is shared by every FPF7 row and scope-rewrite row that uses
`path=blacklist`; it is not copied into each rule.

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

Without a slot number, `set` reuses an identical row or selects the first empty
slot. Use a slot number to replace a row whose match or action is changing.
Omitting the hop expression means `all` (`0-63`).

Numbered `get` normally uses the long field names. If a rule containing
several maximum-length names would exceed one CLI reply, it switches to a
non-truncating compact spelling that `set` also accepts: `c=` is `channel=`,
`p=` is `prefix=`, `i=*|n|s|a|u|s:<scope>|r:<region>` represents `in=`, `q=N`
is `rate=N/min`, and `f=st` combines slow timing (`s`) and temporary-radio
suspension (`t`). The fallback prints packet type numerically.

A legacy row without `scope=` is the existing drop action. On extended builds,
an explicit `drop` has the same result, while `rate=` by itself creates a
rate-only row. A row with `scope=` adds transport scope to an unscoped packet
or replaces the codes on an already-scoped packet. The scope name is normalized
with a leading `#`, and the 128-bit transport key is derived directly from that
hashtag. The name does not need to exist in the region list and is not added to
it. Public names up to 30 characters are accepted; private `$` scopes are not.

`require=region` is the legacy spelling of `in=allowed`. The repeater evaluates the packet's
original route before any rewrite in that receive pass. An incoming transport
scope must match a locally allowed region; an unscoped flood must be allowed by
the wildcard region. If the check fails, that row is skipped, the filter does
not grant a region bypass, and the unchanged packet is allowed to fail
normal region enforcement. Other independently configured scope rows still
apply in their normal order.

When multiple scope or region rows match, the highest-priority row wins; lower
slot number breaks a priority tie.
Rewrite rows do not approve a packet: any matching drop row and every remaining forwarding gate can
still reject it. A filter-assigned scope is trusted without local region-list
validation, but `repeat`, `flood.max`, loop detection, and
moderation still apply. By default, a changed scope bypasses inbound `rxdelay`,
then is retransmitted with zero `txdelay` and the highest outbound queue
priority. Add `tx=slow` to use an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, retain normal queue priority, and force the
maximum `txdelay` factor of `2.0`; the randomized transmit delay ranges from
zero through ten packet airtimes. `tx=fast` explicitly restores the default.
Selecting the scope already present does not grant special treatment. Active
radio transmission, CAD, and airtime-budget limits are unchanged.

On generalized repeaters, forward rules, `flood.channel.scope` rewrite rows,
the shared blacklist, and the `flood.channel.data` compatibility state are one
atomic FPF7 policy image. Existing `/flood_ch_scope`, `/flood_filter_bl`, FPF6,
and FCB2 data is imported once; the old files are removed only after the new
image verifies and commits. Compact FPF6 repeaters retain separate files.
Deleting the blacklist leaves `path=blacklist` rows in place but dormant until
IDs are configured again. Path hashes are truncated routing identifiers and
are not authenticated proof that a particular repeater—or a particular
person—handled a packet. FULL room servers reject blacklist commands.

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
the temporary-radio window unless an earlier matching stop row ends FPF7
processing. `repeat`, `flood.max*`, region handling, loop detection, and the OTA
subsystem's own hop limit also remain in force.

Standard traceroute uses direct routing and never enters `flood.filter`. For a
custom flood-form trace, `type=any`, explicit `trace`, scope, region, rate, and
drop rows all behave normally. The stock core does not normally flood-forward
TRACE packets.

### Remote-administration lockout warning

There are no hidden payload-type or short-hop exemptions in FPF7. Drop and
rate rules can block `req`, `response`, `txt_msg`, `anon_req`, `path`, ACK, and
multipart traffic beginning at hop `0` when their match fields say so.

A flooded login starts as `ANON_REQ`; its reply is commonly a `PATH` packet
carrying an encrypted `RESPONSE`. Before a direct return path is established,
administrative replies and CLI text can also be flooded. Transit repeaters do
not have the session key and cannot distinguish those encrypted admin exchanges
from ordinary peer packets with the same outer type. A rule therefore affects
the complete outer packet class, not only packets that ultimately authenticate
as administrators. Keep a serial or other recovery path and stage broad
deny/rate rules carefully.

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
free slots. All six rules take effect at the thresholds shown; there are no
hidden short-hop exceptions. The Control rule allows a flood received with
path count `0` to be forwarded
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
2. `flood.channel.scope` tries a path-qualified channel row before that
   channel's ordinary fallback, then adds or replaces the scope from either a
   configured region or a direct `scope=<name>` target.
3. All extended `flood.rule` match fields are evaluated against the same
   original incoming packet. Matches are ordered by descending priority and
   then ascending slot. The first matching `stop` row removes every later FPF7
   match. The highest-order remaining `scope=` or `region=` row may replace the
   channel-scope result; a direct scope does not require a region-list entry.
4. `repeat` and `flood.max*` are checked.
5. The FPF7 forward phase, including any row managed through
   `flood.channel.data*`, applies drop and rate decisions using that saved
   match result. No packet type or short-hop range is silently exempted.
6. Region and loop-detection rules are checked; a regionless scope assigned by
   either table is already trusted when it has no region-list match, except
   that it cannot rescue a channel rejected by
   `flood.channel.scope.require`.
7. `flood.moderation` checks decrypted group text, username, rate, hops, and
   path. If it accepts the packet, matching general-rule rate counters are
   charged immediately before retransmission is approved.

The first denial is enough to prevent retransmission. A packet that is denied
can still appear in local logs or MQTT output. Moderation runs last because its
rate counters are charged only for packets that pass every other forwarding
control and will actually be retransmitted.

## Delegate filter management

On repeaters, ACL permission `5` is the filter-manager role:

```text
setperm <companion-public-key-hex> 5
```

A filter manager can read non-secret operational status and manage `repeat`,
`loop.detect`, `flood.max*`, `flood.channel.data*`,
`flood.filter*`, `flood.rule*`, and `flood.moderation*`. Delegated `get` access uses an
explicit allowlist: it cannot retrieve guest, WiFi, MQTT, bridge, or other
credentials, and it cannot change regions, ACL entries, radio settings, or
unrelated administrator settings. Because `flood.filter scope=` derives a
public hashtag key directly, a filter manager can configure that action without
region-manager permission; it still cannot edit the region hierarchy.

FULL ESP32 room servers use their existing administrator check for remote
`flood.rule` and `flood.filter` commands; they do not grant this table through
permission `5`.

ACL permission `4`, the region/scope-manager role, can read, add, replace, and
delete `flood.channel.scope` and `flood.channel.scope.require` rows and manage
regions. This lets the same delegate create target regions, assign forced
scopes from regions or direct hashtag names, and select the channels that
require valid incoming scopes.

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
set flood.rule.1 type=ota hops=all drop suspend=tempradio
set flood.rule.2 type=any channel=#wardriving hops=5+ drop
```

These commands explicitly replace the two seeded generalized-repeater slots.
Inspect them first if they may now contain other rules. Compact FPF6 builds use
only the first command's `flood.filter.1 0x0C all suspend=tempradio` form.

## Remove the custom rules

To save both tables in an empty state:

```text
del flood.filter all
del flood.moderation all
get flood.filter
get flood.moderation
```

This does not change the older `flood.max*`, loop-detection, or region settings;
inspect or reset those separately when troubleshooting.
