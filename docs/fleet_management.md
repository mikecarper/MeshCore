---
layout: default
title: Private fleet management
---
<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/fleet_management/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->


# Private fleet management

Fleet control lets one publisher send filtering and secondary-radio commands to
all enrolled infrastructure nodes, one node, or a list of nodes over a private
LoRa channel.
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

Nodes need a clock set to current UTC. Commands expire after ten minutes and may
be at most one minute ahead of a receiver's clock. A bad clock fails closed;
correct it through the normal administration path.

## Send commands from the app

On the publishing Companion, use the existing local Command Line screen. The
Companion signs with its own identity. No publisher private key is copied to a
repeater, and no app protocol change is required.

Examples use private channel index `3`:

```text
fleet send 3 all get radio2
fleet send 3 all set tempradio2 910.5,500,8,5,rxtx,120
fleet send 3 all set radio2.cross auto
fleet send 3 all set flood.rule.4 type=grp_txt hops=6+ drop
fleet send 3 all del flood.rule.4
```

Replace `all` with **8 or 12 hexadecimal characters from the start of a node's
public key**, or its **complete 64-character public key**. Separate multiple
targets with commas, without spaces. Lengths can be mixed:

```text
fleet send 3 1257aee5 get radio2
fleet send 3 1257aee5a754,9abc0123 get radio2
fleet send 3 1257aee5,9abc01234567 set radio2.cross auto
```

These are example public-key prefixes; substitute your nodes' actual keys.
**A prefix addresses every enrolled node whose public key starts with it.**
Use all 64 characters when you need one exact identity, especially in a large
fleet. Names and short LoRa path IDs are not accepted as target identities.
`all` must appear alone. A node that matches several entries still executes the
command only once, and the signature protects the entire list.

On air, full keys use a 128-bit SHA-256 digest; prefixes use their original
public-key bytes. Multiple-target commands and prefix targets require updated
publisher and receiver firmware. `all` and a single complete key retain the
previous wire format, which updated receivers also accept. Separate fleets use
separate channel keys; named subgroups are not part of this first version.

Only local commands on the publishing Companion can originate fleet sends. A
remote CLI command cannot turn that Companion into a multicast signing proxy.
An `OK` means the Companion queued the command, not that every receiver applied
it. Wait for acknowledgements and confirm the result on important nodes.

Use at most one new command per second. The Unix-second sequence is reserved in
durable storage **before** dispatch. The same sequence is never executed twice,
including after reboot or a lost reply. Retry an uncertain command with a fresh
sequence; the operation should be chosen to be idempotent.

## Supported controls

The command body is limited to **72 ASCII bytes**. Targets and command must also
fit one signed **165-byte envelope**. An 8-character target uses 5 bytes, a
12-character target uses 7, and a complete key uses 17. The fixed envelope and
signature use 78 bytes, leaving 87 bytes for the target list and command together.
For example, two complete keys leave 53 bytes for the command. Lists are capped
at 17 entries; packet-size limits usually allow fewer. `all` and a single complete
key retain the previous format's full 72-byte command allowance.
The local app/console's command-length limit also applies. Oversized or invalid
lists are rejected as a whole, without sending or truncating any targets.
The short legacy USB rescue console cannot fit a full-key command; use the app
or normal terminal for longer lists.

| Control | Fleet commands |
| --- | --- |
| Saved second profile | `get radio2`, `set radio2 <tuple|off>` |
| Temporary second profile | `get tempradio2`, `set tempradio2 <tuple|off>` |
| Profile diagnostics | `get radio2.status`, `get radio2.timing` |
| Crossover | `get radio2.cross`, `set radio2.cross <auto|on|off>` |
| Filter rules | `get/set/del flood.filter[.n]`, `flood.rule[.n]` |
| Path blacklist | `get/set/del flood.filter.blacklist[.n]` |
| Channel moderation | `get/set/del flood.moderation[.n]` |
| Scope policy | `get/set/del flood.channel.scope[.n]`, `flood.channel.scope.require[.n]` |
| Group-data forwarding | `get/set flood.channel.data`, `flood.channel.data.hops` |
| Flood hop limits | `get/set flood.max`, `flood.max.unscoped`, `flood.max.advert` |

Each role still checks whether it supports the requested setting. Primary radio
changes, identity export, passwords, ACL edits, filesystem operations, firmware
installation, reboot, and unrestricted CLI are outside fleet capability.

Secondary-radio mutations keep the old profile until their exact acknowledgement
packets drain, with at least one successful physical transmission. Queue failure,
failed transmissions, or an acknowledgement timeout cancels the staged mutation.
Temporary-radio leases retain their original monotonic end time; waiting for an
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
recovery path before changing network-wide filters. The primary radio is retained
by this capability, but a forwarding policy can still partition the fleet.

The receiver is allocated lazily when fleet settings are present or queried, with
one bounded mailbox. Cryptographic work and storage writes occur after the receive
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
