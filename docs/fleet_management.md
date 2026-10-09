---
layout: default
title: Private fleet management
---
<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/fleet_management/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->


# Private fleet management

Fleet control lets one publisher send filtering, radio schedules, secondary-radio
changes, and clock commands to
all enrolled infrastructure nodes, regions, a geographic radius, one node, or a list of targets over a
private LoRa channel.
It is disabled until configured. It is supported on repeaters and room servers;
fixed-size STM32 images omit it because of their flash limit.
Both the publishing Companion and receivers need firmware built with fleet-control
support; the existing app can use its local Command Line screen.

## Read-only receivers

Commands use two separate credentials:

- A random **128-bit channel key** encrypts the traffic. Receivers need this key
  to read the private channel.
- The publisher's **Ed25519 public key** verifies commands. Only the publisher
  holds the corresponding private key and can authorize changes.

Giving a repeater the channel key and publisher public key does **not** give it
the publisher's signing key. An ordinary channel message, a copied display name,
or possession of the channel key cannot authorize a fleet command. Each command
signature also binds the channel, target, sequence, expiry, and exact command.

This makes management commands read-only for receivers. It does not turn ordinary
MeshCore chat into a signed, read-only announcement channel: stock apps do not
verify signatures on ordinary channel text. Fleet acknowledgements are convenient
channel text, not authenticated inventory evidence. Use existing individually
authenticated administration for sensitive confirmation.

## Enroll nodes

Use an existing authenticated admin connection or the local console. Generate a
random private channel key; for example, `openssl rand -hex 16`. Do not use Public
or a predictable hashtag key. Add that same key as a private channel on the
publishing Companion, and note its channel index.

Get the publishing Companion's full public identity key from its device information
or existing identity command. On every managed repeater or room server, run:

```text
set fleet.controller <publisher-public-key-64-hex>
set fleet.channel <private-channel-key-32-hex>
get fleet.channel
get fleet.controller
get fleet.stats
```

`get fleet.channel` reports enrollment, storage health, a channel hash, and the
last accepted sequence. It does not reveal the channel key. `set fleet.channel
off` disables reception; `set fleet.controller off` revokes the publisher. Key
changes use the existing individually authenticated admin path, never fleet
commands.
`get fleet.stats` includes `parts`, the number of fragments currently buffered
for an incomplete two-packet command.

Nodes need a clock set to current UTC. Commands expire after ten minutes and may
be at most one minute ahead of a receiver's clock. A bad clock fails closed;
correct it through the normal administration path.

## Send commands from the app

On the publishing Companion, use the existing local Command Line screen. The
Companion signs with its own identity. No publisher private key is copied to a
repeater, and no app protocol change is required.

Examples use private channel index `3`:

```text
fleet send 3 get radio2
fleet send 3 set tempradio2 910.5,500,8,5,rxtx,120
fleet send 3 set radio2.cross auto
fleet send 3 set flood.rule.4 type=grp_txt hops=6+ drop
fleet send 3 del flood.rule.4
```

Targets are optional: omitting the target sends to **all enrolled nodes** on the
private fleet channel. Explicit `all` still works, for example
`fleet send 3 all get radio2`.

To address particular nodes, insert **8 or 12 hexadecimal characters from the
start of a node's public key**, or its **complete 64-character public key**, before
the command. Separate multiple targets with semicolons, without spaces. Lengths can
be mixed:

```text
fleet send 3 1257aee5 get radio2
fleet send 3 1257aee5a754;9abc0123 get radio2
fleet send 3 1257aee5;9abc01234567 set radio2.cross auto
```

These are example public-key prefixes; substitute your nodes' actual keys.
**A prefix addresses every enrolled node whose public key starts with it.**
Use all 64 characters when you need one exact identity, especially in a large
fleet. Names and short LoRa path IDs are not accepted as target identities.
`all` must appear alone. A node that matches several entries still executes the
command only once, and the signature protects the entire list.

### Target existing regions

Fleet commands read each receiver's **existing region map**. Two selectors are
available, and they can be mixed with public-key targets:

| Selector | Which receivers execute |
| --- | --- |
| `sea` or `region:sea` | Receivers with an exact `sea` entry in their configured region list |
| `home:sea` | Receivers whose home region is `sea`, or whose home has `sea` as a parent or ancestor |

```text
fleet send 3 sea get radio2
fleet send 3 sea;pdx clock sync
fleet send 3 region:sea set radio2.cross auto
fleet send 3 home:sea get tempradio2
fleet send 3 home:sea;home:pdx clock sync
fleet send 3 sea;home:pdx;1257aee5 get radio2
```

The semicolon-separated list is an **OR**: a receiver that matches any entry executes
once, even if it matches several entries. Names match exactly and are case
sensitive; `sea` does not match `seattle`. One leading `#` is an alias, as in the
existing region commands. `$` remains significant for private region names.
Names may contain up to 30 printable ASCII bytes using region-name characters.
Use `region:` or `home:` explicitly for names with 8 or 12 characters, names
made entirely of hexadecimal characters, or reserved command words such as
`get`, `set`, `del`, `time` and `clock`. Bare `all` still means the entire fleet;
`region:all` selects a region actually named `all`.

Inspect the existing configuration with `region` and `region home`. To choose a
home from regions already defined on that receiver, use:

```text
region home sea
region save
```

List matching includes a configured region even when forwarding for it is
denied. Home matching follows the selected home's ancestry; a wildcard or unset
home matches no named home target. The default transmit scope and the incoming
packet's scope do not determine target membership. Region selection is checked
against the live map when the complete command is received.

Targets select which nodes execute. Delivery still follows the Companion's
channel scope and the intervening forwarding rules. Unknown names match no
receivers and never become a whole-fleet request.

### Target a GPS center and radius

Use `gps:latitude,longitude:radius-km` to select receivers within a circle. For
example, this selects nodes within **25 km** of the given Seattle coordinates:

```text
fleet send 3 gps:47.6062,-122.3321:25 get radio2
fleet send 3 gps:47.6062,-122.3321:25 set radio2.cross auto
fleet send 3 sea;home:pdx;gps:47.6062,-122.3321:25;1257aee5 clock sync
```

GPS, region, home and key selectors can share the same semicolon-separated list.
They use the same OR rule: any matching entry selects the node, and the command
executes once. The center, radius and complete target list are signed.
The comma is only for the coordinate pair; `;` is the only target-list separator.

Coordinates use ordinary decimal degrees, with latitude from -90 to 90 and
longitude from -180 to 180, and at most six decimal places. Radius is in
kilometers, from **0.001 to 20050**, with at most three decimal places. For
example, `0.5` means 500 meters. Scientific notation, excess decimal places,
missing fields and out-of-range values are rejected without sending. Distance
uses the great-circle calculation, including across the date line and near the
poles; a node on the radius boundary is included.

Each receiver prefers its configured `lat` and `lon`. A stationary radio can
have a fixed position even without GPS hardware; configure it through its
normal administration connection:

```text
set lat 47.6062
set lon -122.3321
get lat
get lon
```

When both configured coordinates are zero, the receiver can use an existing
valid GPS cache no older than 12 hours. Matching never wakes GPS, takes a UART
or requests telemetry. A missing, expired, invalid or default `(0,0)` position
matches no GPS selector; it can still match another entry in the list. Invalid
nonzero configured coordinates are rejected rather than falling back to GPS.
For a stationary radio that lets GPS sleep for longer than 12 hours, configure
its fixed coordinates.

Private fleet matching uses this position even when location is hidden from
public adverts. It reads the position when the complete command arrives,
including after the second fragment of a two-packet command, so a moving node
is selected using its position at completion.

On air, full keys use a 128-bit SHA-256 digest; prefixes use their original
public-key bytes. Whole-fleet commands use the compact FMC2 format with no target
records, whether the CLI target is omitted or explicitly `all`. This reduces the
header from 29 to 14 bytes. A single complete key uses the smaller fixed-target
FMC1 format. Region, home and GPS selectors use separate signed record types in FMC2.
Separate fleets use separate channel keys.

Only local commands on the publishing Companion can originate fleet sends. A
remote CLI command cannot turn that Companion into a multicast signing proxy.
An `OK` means the Companion queued the command, not that every receiver applied
it. Wait for acknowledgements and confirm the result on important nodes.

Use at most one new command per second. The Unix-second sequence is reserved in
durable storage **before** dispatch. The same sequence is never executed twice,
including after reboot or a lost reply. Retry an uncertain command with a fresh
sequence; the operation should be chosen to be idempotent.

## Supported controls

The Companion automatically uses **one or two LoRa packets**, depending on the
signed command's size. Whole-fleet commands fit **87 ASCII bytes** in one packet
or **230 bytes** across two. Targets consume some of that allowance:

| Targets | One packet: command bytes | Two packets: command bytes |
| --- | ---: | ---: |
| Omitted or `all` | 87 | 230 |
| One 8-character public-key prefix | 82 | 225 |
| One 12-character public-key prefix | 80 | 223 |
| One complete 64-character public key | 72 | 215 |
| One three-character region or home name, such as `sea` | 82 | 225 |
| Two three-character region or home names, such as `sea;pdx` | 77 | 220 |
| One GPS center and radius | 74 | 217 |
| Two 8-character public-key prefixes | 77 | 220 |
| Two complete public keys | 53 | 196 |

Each prefix in a list uses 5 or 7 bytes; each complete key in a list uses 17.
Each region or home selector uses two bytes plus its canonical name length.
Each GPS selector uses 13 bytes: its type, latitude and longitude in millionths
of a degree, and radius in meters.
A single complete key uses the smaller fixed-target format with a 29-byte header,
so its limits come from packet space. Lists are capped at 17 entries and 86 bytes
of encoded target records; for example, at most six GPS selectors fit. Requests
exceeding their target-specific budget are rejected before signing or sending,
with no partial target list.

The existing app/console input limit also applies. The app's normal 176-byte
command frame fits **161 command bytes** after `fleet send 3 `; a correlated
request uses three more bytes, and explicit targets reduce the space further.
The shorter USB rescue console has an 80-byte input buffer. The 230-byte limit
is the fleet protocol's capacity, rather than a promise that every console can
enter that much text. Oversized or invalid lists are rejected as a whole,
without sending or truncating any targets.

### Two-packet delivery

Longer commands keep **one 64-byte signature over the entire command**, including
its channel, targets, sequence and expiry. The signed envelope is split into two
`FMP1` fragments. Each fragment has an 11-byte collection header and up to 154
envelope bytes, within the existing 165-byte group-data limit. Together they can
carry a 308-byte signed envelope. No third packet is used.

The Companion reserves both packets before sending. If either cannot be queued,
it cancels the queued part and any radio copy, reports an error, and leaves the
sequence available for a retry. Successful queueing still does not guarantee
delivery over LoRa.

A receiver accepts either arrival order and identical duplicates. It executes
and acknowledges **only after both parts arrive and the complete signature,
target, clock and replay checks pass**. Missing or altered parts cannot execute
a partial command. One bounded assembly is kept for up to five minutes from the
first part; duplicates do not extend that deadline. A fresh command with a newer
eligible sequence replaces an incomplete older transfer, so a lost part does
not block retries. Reboot or changing enrollment discards the partial transfer.

| Control | Fleet commands |
| --- | --- |
| Saved second profile | `get radio2`, `set radio2 <tuple|off>` |
| Temporary second profile | `get tempradio2`, `set tempradio2 <tuple|off>` |
| Scheduled primary profile | `get/set/del radioat`, `get/set/del tempradioat` |
| Scheduled second profile | `get/set/del radioat2`, `get/set/del tempradioat2` |
| UTC clock | `clock`, `clock sync`, `time <epoch-seconds>` |
| Profile diagnostics | `get radio2.status`, `get radio2.timing` |
| Crossover | `get radio2.cross`, `set radio2.cross <auto|on|off>` |
| Filter rules | `get/set/del flood.filter[.n]`, `flood.rule[.n]` |
| Path blacklist | `get/set/del flood.filter.blacklist[.n]` |
| Channel moderation | `get/set/del flood.moderation[.n]` |
| Scope policy | `get/set/del flood.channel.scope[.n]`, `flood.channel.scope.require[.n]` |
| Group-data forwarding | `get/set flood.channel.data`, `flood.channel.data.hops` |
| Flood hop limits | `get/set flood.max`, `flood.max.unscoped`, `flood.max.advert` |

Each role still checks whether it supports the requested setting. Immediate
primary radio changes, identity export, passwords, ACL edits, filesystem operations, firmware
installation, reboot, and unrestricted CLI are outside fleet capability.

## Schedule radio changes and set clocks

Use the normal schedule syntax, with UTC epoch seconds or `+N` whole minutes.
Primary schedules are supported by repeaters; a role without a primary scheduler
replies that the command is unsupported. Second-profile schedules use `rx` or
`rxtx` after the coding rate:

```text
fleet send 3 clock
fleet send 3 clock sync
fleet send 3 set radioat 910.5,500,8,5,+5
fleet send 3 set tempradioat 910.5,500,8,5,+5,+15
fleet send 3 set radioat2 910.5,500,8,5,rxtx,+5
fleet send 3 set tempradioat2 910.5,500,8,5,rxtx,+5,+15
fleet send 3 1257aee5 get tempradioat2 1
fleet send 3 1257aee5 del tempradioat2 all
```

Both relative endpoints use the receiver's clock at command acceptance. For a
coordinated fleet switch, use the same absolute UTC start/end timestamps on all
nodes. An optional final preamble value or `auto` uses the existing radio syntax.
Schedule capacity, radio-range validation, and overlap checks remain unchanged;
pending schedules still do not survive reboot.

`clock sync` advances a receiver to the publishing Companion's signed timestamp
plus one second. `time <epoch-seconds>` sets an explicit UTC timestamp. Both use
the existing forward-only clock rule. Initial setup or a clock far out of sync
still requires local or individually authenticated clock correction: fleet clock
commands retain the signature, expiry, replay, and maximum-clock-lead checks.

Radio mutations and schedule edits keep the old state until their exact acknowledgement
packets drain, with at least one successful physical transmission. Queue failure,
failed transmissions, or an acknowledgement timeout cancels the staged mutation.
Temporary-radio leases and scheduled windows retain their original monotonic end time; waiting for an
acknowledgement does not lengthen them.

Exact single-node acknowledgements wait 0.5–1.5 seconds; prefix, multiple-target,
and whole-fleet replies are spread over 0.5–60.5 seconds (0.5–15.5 seconds for
radio mutations, to preserve short
temporary leases). This reduces simultaneous responses but is not a reliable
delivery protocol for an arbitrarily large fleet. Use individual requests for
confirmation and divide very large fleets into separately keyed channels.

## Filtering and recovery

Local fleet reception happens before a node's forwarding decision, so that node
can still process an authenticated command when a filter or `repeat off` blocks
forwarding. **Intermediate nodes can still block delivery to downstream nodes.**
Management traffic has no automatic exemption from forwarding filters. Preserve
an appropriate group-data/channel forwarding rule and an existing direct-admin
recovery path before changing network-wide filters. Primary-radio schedules can
move nodes off the normal network; use a temporary window or a planned recovery
path for experiments. A forwarding policy can also partition the fleet.

The receiver is allocated lazily when fleet settings are present or queried, with
one bounded mailbox and one 308-byte fragment assembly. Cryptographic work and storage writes occur after the receive
stack unwinds. Signature-validation
attempts are bounded to protect the radio loop from matching-hash junk. A corrupt
or unreadable fleet store denies commands rather than discarding its replay state.

## Ideas for packet-abuse containment

A packet-count chart can show a burst, but cannot establish intent, an authenticated
source, or the airtime consumed. A large Trace spike deserves investigation;
MeshCore already rejects flood-form Trace and Control packets. Valid direct Trace
requests need different treatment from flood forwarding rules.

Useful next additions, rather than features already implemented here:

- **Temporary containment with automatic rollback:** deploy a drop/rate policy
  for a bounded period and restore the previous policy unless confirmed.
- **Direct-request rate limits:** separately bound Trace, anonymous requests,
  discovery, login, and expensive response generation without breaking legitimate
  management, ACKs, or ongoing OTA exchanges.
- **Policy preview and revisions:** validate complete rule bundles, detect board
  limitations, apply atomically, and report the active revision before rollout.
- **Better evidence:** report per-type receive/drop counts, airtime, queue pressure,
  invalid signatures, duplicate rate, and radio recoveries. Truncated path IDs
  remain observations, not authenticated attacker identities.
- **Authenticated inventory:** stagger signed status replies with firmware version,
  uptime/reset reason, battery, memory/storage headroom, and radio errors.
- **Maintenance windows:** coordinated secondary-profile experiments and OTA
  readiness checks, keeping existing firmware-signing policy intact.
- **Per-controller roles and revocation:** multiple public verification keys with
  separate filter-only/radio permissions and independent replay state.

Start with observation, then a narrow temporary rule, then verify neighboring
nodes still communicate. Software filtering can reduce forwarding and response
amplification; it cannot recover airtime already occupied by an RF transmitter.

See [flood filtering](flood_filtering.md), [radio profiles](radio_profiles.md), and
[ACL administration](cli_commands.md#acl).
