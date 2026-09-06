# Halo and Keymind Branch Settings

This file covers only CLI settings and helper commands added by the Halo or
Keymind branches. Use [CLI commands](cli_commands.md) for the general infrastructure CLI.
For MQTT, logging, and former variant controls, use
[feature switches by role](role_feature_switches.md); Full Companion uses
different controls for some of these settings.
See [Repeater Flood Filtering and Moderation](flood_filtering.md) for a focused
filter setup and troubleshooting guide.

## Quick Start

```text
set retry.preset rooftop
set direct.retry.heard on
set flood.retry.advert off
set flood.retry.bridge off
set flood.retry.prefixes none
set flood.retry.ignore none
```

Then verify:

```text
get retry.preset
get direct.retry.heard
get flood.retry.advert
get flood.retry.prefixes
get flood.retry.ignore
```

Use prefixes from the analyzer or neighbors list or `get recent.repeater` after the repeater has been online for a few hours.

## Common Examples

Disable retrying advert packets:

```text
set flood.retry.advert off
get flood.retry.advert
```

Ignore a repeater as a successful flood retry echo:
Use this if you have a car repeater and a house repeater; have the house ignore the car.

```text
set flood.retry.ignore 71CE82,C7618C
get flood.retry.ignore
```

Only accept specific downstream relays as flood retry success:
You're in a hole and need to hit a mountain top repeater to get out; keep trying till one you see one of these send out your packet.

```text
set flood.retry.prefixes A58296,860CCA,425E5C
get flood.retry.prefixes
```

Bridge two groups of repeaters:

```text
set flood.retry.bridge on
set flood.retry.bucket 1 71CE82,C7618C
set flood.retry.bucket 2 BEEBB0,425E5C
get flood.retry.bucket.1
get flood.retry.bucket.2
```

Return to simple non-bridge flood retry:

```text
set flood.retry.bridge off
set flood.retry.prefixes none
set flood.retry.ignore none
```

## Added Settings

| Setting | What it does | How to use | Example |
| --- | --- | --- | --- |
| `telemetry.temp`, `telemetry.volt`, `telemetry.volt.i2c`, `telemetry.gps` | Repeater and room builds record 30-minute MCU temperature and battery samples for seven days. Detected INA voltage channels retain four days at 0.02 V resolution through 655.34 V; all-zero channels are omitted as disconnected. GPS-capable builds retain GPS separately. Repeater `telemetry.tx` sends temperature, battery, and per-channel I2C RAW_CUSTOM packets over a configured direct path on a persistent 1-30 day schedule (default two days) or immediately for testing; GPS is never included. History resets on reboot. | `get telemetry.temp [page]`, `get telemetry.volt [page]`, `get telemetry.volt.i2c [channel [page]]`, `get telemetry.gps [page]`, `set telemetry.gps <1-30>`, `get telemetry.tx`, `set telemetry.tx <off|direct|path>`, `set telemetry.tx schedule <off|1-30d>`, `send telemetry.tx now` | `get telemetry.volt.i2c 2 1` |
| `battery.alert` | Sends opt-in, region-scoped low-battery warnings to `#repeaters` after 30 minutes of uptime. | `get battery.alert`, `get battery.alert.region`, `set battery.alert on [region]`, `set battery.alert off` | `set battery.alert on sea` |
| `battery.alert.low` | Warning threshold percentage. Must be greater than `battery.alert.critical`. | `get battery.alert.low`, `set battery.alert.low <1-100>` | `set battery.alert.low 20` |
| `battery.alert.critical` | Critical threshold percentage. Critical and warning alerts use the same 12-hour resend cooldown. | `get battery.alert.critical`, `set battery.alert.critical <0-99>` | `set battery.alert.critical 10` |
| `recent.repeater` | Shows, searches, seeds, or clears the recent repeater prefix/SNR table used by direct retry and bridge freshness checks. Search results include the last-recorded age. Entries older than 24 hours are removed by a three-hour sweep. | `get recent.repeater [page]`, `get recent.repeaters search <2|4|6 hex> [page]`, `set recent.repeater <prefix> <snr_db>`, `clear recent.repeater` | `get recent.repeaters search A1B2` |
| `flood.channel.data` | Compatibility view for a visible FPF7 `type=grp_data` drop row on generalized repeaters. `off` creates the row and `on` removes it; compact FPF6 builds retain the legacy gate. Use an authenticated `channel=` row when only selected channels should be blocked. | `get flood.channel.data`, `set flood.channel.data on/off` | `set flood.channel.data off` |
| `flood.channel.data.hops` | Sets the compatibility row's hop range: `all` maps to `hops=all`; `1`-`7` repeats through that hop count and maps to `hops=N+1+`. | `get flood.channel.data.hops`, `set flood.channel.data.hops <all|1-7>` | `set flood.channel.data.hops 7` |
| `flood.channel.scope` | FPF7 rewrite-phase rows that add a transport scope to received unscoped floods or replace the scope of already-scoped floods. A bare target uses an existing flood-allowed region; `scope=<name>` derives a public hashtag target directly without creating a region, exactly like `flood.filter scope=`. By default, a changed packet bypasses inbound `rxdelay` and is forwarded at the highest outbound queue priority with zero initial `txdelay`, so the selected scope can win at the next hop. `tx=slow` uses an effective inbound `rxdelay` base of `max(2, configured rxdelay * 2)`, retains normal queue priority, and forces the maximum `txdelay` factor of `2.0`; its actual randomized transmit delay ranges from zero through ten packet airtimes. `path=blacklist` and `path=bucket:<1-6>` make a row path-qualified; bridge buckets remain usable while bridge retry is off. An already-matching scope is a no-op. Exact channel keys beat `txt:*`; path-qualified rows beat the ordinary channel fallback. `login:*` covers the remote-login family, and `other:*` covers every remaining flood type, including flood-form TRACE and OTA. Direct traceroute remains outside the flood table. Generalized builds commit these rows with the forward phase and blacklist; compact FPF6 builds retain separate storage. ACL permission `4` can manage the table. | `get flood.channel.scope[.n]`, `set flood.channel.scope[.n] <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]`, `del flood.channel.scope.<n>|all` | `set flood.channel.scope #rgdata scope=BlackHole86` |
| `flood.channel.scope.require` | Switches group-channel region enforcement to opt-in when the table has entries. Listed authenticated `GRP_TXT`/`GRP_DATA` channels must arrive already scoped to a locally allowed region; unscoped, unknown, or denied incoming scopes are dropped before any rewrite can rescue them. Unlisted group channels bypass only the region gate and retain all other forwarding controls. An empty table preserves global region behavior; non-channel payloads are unchanged. ACL permission `4` can manage the table. | `get flood.channel.scope.require[.n]`, `set flood.channel.scope.require[.n] <public|#channel|key>`, `del flood.channel.scope.require.<n>|all` | `set flood.channel.scope.require #bot` |
| `flood.filter` | Persistent flood-route rules selected by payload type and optional hop range. Generalized repeaters have 63 FPF7 forward slots; FULL ESP32 room servers have 31. Repeaters store their scope-rewrite phase, shared unordered blacklist, and channel-data compatibility state in the same atomic FPF7 image. `path=blacklist` is intended for forwarding abuse containment, including bulk internet-to-mesh dumping, but truncated path IDs are not authenticated identities. Fixed 240 KB STM32WL repeaters retain compact FPF6 filtering and separate blacklist storage. New generalized repeater tables seed slot 1 with `ota all suspend=tempradio` and slot 2 with an authenticated `#wardriving hops=5+` drop. Direct routes and local receive/logging are unchanged. | `get flood.filter[.n]`, `set flood.filter[.n] <type> [N|N+|N-M|all] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]`, `del flood.filter.<n>|all`; repeater only: `get/set/del flood.filter.blacklist[.n]` | `set flood.filter grp_txt all scope=local tx=slow` |
| `flood.rule` | Live alias for extended `flood.filter` on rule-engine repeaters and FULL ESP32 room servers. A row can AND packet type, hop range, optional channel match, ordered 1/2/3-byte pbyte source prefix, and original scope/region conditions, then drop, rewrite to a direct scope or configured region, enforce a per-row rate, allow flood retry, and/or stop lower-priority FPF7 rules. `channel=*` means no channel condition; `public`, `#name`, and keys authenticate; `hash:XX` is an unauthenticated one-byte fallback with collision/spoof risk. With no `retry` rows, global retry behavior is unchanged; once any exists, matching `retry` rows allow-list received floods while the global retry gates and `flood.retry.bridge` algorithm still apply. `scope=BlackHole86` directly derives a regionless sink scope; `region=BlackHole86` would require a configured flood-allowed region. All rows match the original receive-time packet. Higher numeric `priority` runs first; equal values automatically order authenticated channels before raw hashes before `channel=*`, then use lower slot as the final tie-break. Repeated rows with one channel key share a per-packet authentication result. Persistent FPF7 stores canonical region names, so region ID reorder or reuse cannot retarget a rule. A missing saved region makes its `region=` rewrite and paired `stop` inert until the name returns, allowing lower safety rows to run. Fixed 240 KB STM32WL profiles keep FPF6 and do not expose this alias; partition sizes are unchanged. | `get flood.rule[.n]`, `set flood.rule[.n] type=<type> [hops=...] [channel=...] [prefix=...] [in=...] <drop|scope=...|region=...|rate=N/min|retry|stop> [priority=0-255]`, `del flood.rule.<n>|all` | `set flood.rule.2 type=any channel=#hamradio retry` |
| `flood.moderation` | Decrypts keyed `GRP_TXT` channels and applies drop, per-username messages/minute, and maximum-hop controls, optionally matched against the first 1-3 path hashes. Supports `public`, `#channel`, and 128/256-bit channel keys. Sender names and truncated path hashes are moderation hints, not authenticated identities. | `get flood.moderation[.n]`, `set flood.moderation[.n] <channel> <sender> <drop|rate=X/min|hops=N> [path=...]`, `del flood.moderation.<n>|all` | `set flood.moderation public "Noisy User" rate=5/min hops=4` |
| `clock.sync.mesh` | Defaults on for all repeater, sensor, and room-server builds; a saved setting overrides that default. It estimates UTC as soon as the configured number of fresh signed-advert or valid Public-channel sources is collected, with a 30-minute bootstrap/retry timer when evidence is still insufficient, then repeats lazily seven days after each successful estimate. New evidence retriggers evaluation after a no-consensus result. `clock.sync.mesh now` queues an immediate LoRa-only attempt without bypassing quorum or source suppression. Only timestamps from firmware build time through build time plus ten years are recorded. Successful CLI, GPS, or WiFi/NTP clock updates suppress LoRa time collection until reboot; after reboot LoRa is the fallback if NTP cannot sync. Status reports the reason a clock was not set; its `.table` and `.1` through `.16` forms inspect collected samples. | `get clock.sync.mesh`, `set clock.sync.mesh <on|off>`, `clock.sync.mesh now`, `get clock.sync.status[.table|.1-.16]` | `set clock.sync.mesh on` |
| `clock.sync.mesh.edge` | Defaults on so edge infrastructure nodes can collect clock evidence when all packets arrive through one relay path. Verified evidence is observed before the forwarding decision, so disabled forwarding and forwarding filters do not prevent collection. Signed adverts are deduplicated by public key and Public-channel timestamps by case-insensitive display name; all may share one receive path. Public display names are unauthenticated and can be spoofed. Changing this setting clears current clock samples. | `get clock.sync.mesh.edge`, `set clock.sync.mesh.edge <on|off>` | `set clock.sync.mesh.edge on` |
| `clock.sync.internet` | Adds a read-only internet/NTP estimate at the same initial and seven-day checks on WiFi MQTT repeater-observer builds. Other builds retain the setting but report internet unavailable. | `get clock.sync.internet`, `set clock.sync.internet <on|off>` | `set clock.sync.internet on` |
| `clock.sync.drift` | Absolute correction threshold in seconds; the default is 600 seconds (10 minutes). The clock is moved forward or backward only when the estimate differs by more than this value. | `get clock.sync.drift`, `set clock.sync.drift <30-86400>` | `set clock.sync.drift 600` |
| `clock.sync.samples` | Minimum fresh evidence count before mesh time can be used: distinct receive paths in normal mode or distinct advert keys/Public-channel display names in edge mode. A strict majority of all fresh samples is also required. Range `3-16`; default `9`. | `get clock.sync.samples`, `set clock.sync.samples <3-16>` | `set clock.sync.samples 9` |
| `outpath` | Overrides the primary direct route used for replies to the current remote client. | `get outpath`, `set outpath <hops>`, `set outpath direct`, `set outpath clear`, `set outpath flood` | `set outpath A1B2C3,D4E5F6` |
| `altpath` | Adds a secondary direct route for repeater replies to the current remote client. | `get altpath`, `set altpath <hops>`, `set altpath direct`, `set altpath clear`, `set altpath flood` | `set altpath 71CE82,BA09F0` |

## Other Keymind Commands

| Command | What it does | How to use | Example |
| --- | --- | --- | --- |
| `send text.flood` | Sends a `#repeaters` flood text message formatted as `<node_name>: <message>`, with `:` in the node name sent as `;`. | `send text.flood <message>` | `send text.flood checking ridge link` |

## Battery Alerts

Battery alerts are off by default. Enabling requires a named region. With no
region argument, the repeater selects the single deepest (most narrow) region
in the hierarchy; if multiple regions tie, the command asks for an explicit
region. For example, after `region def west pnw wa w-wa sea`, `set
battery.alert on` selects `sea`, while `set battery.alert on w-wa` overrides
the default. Alerts are never sent as unscoped floods, and removing the selected
region stops alerts until a valid scope is selected again.

The repeater suppresses alerts for its first 30 minutes of uptime. It then
checks every 30 minutes and sends a flood text warning to `#repeaters` when
voltage is above `1 V` and the estimated battery percent is below
`battery.alert.low`.

Warnings and critical alerts both use a `12`-hour resend cooldown, beginning
only after the radio reports that the alert transmission completed.

Defaults:

| Setting | Default |
| --- | ---: |
| `battery.alert` | `off` |
| `battery.alert.region` | `<unset>` |
| `battery.alert.low` | `20` |
| `battery.alert.critical` | `10` |

Example:

```text
set battery.alert.low 20
set battery.alert.critical 10
set battery.alert on
get battery.alert
get battery.alert.region
```

CPU power saving remains compatible with the check. The battery timer never
requests a wake earlier than its 30-minute deadline; when the normal loop is
already awake after that deadline, no additional wake is needed. Sleeping time
counts toward the startup delay, and an outbound warning prevents another sleep
until the queued packet has been handled. This is separate from RX duty-cycle
power saving, which only cycles the LoRa receiver and does not stop the main
loop's battery timer.

## Recent Repeater Table

Direct retry uses the recent repeater table when `direct.retry.heard` is `on`.
Bridge buckets also use this table: a configured bucket prefix is active only
when it was heard within the last hour.

Show learned rows:

```text
get recent.repeater
get recent.repeater 2
get recent.repeaters 2
get recent.repeater page 3
get recent.repeaters search A1
get recent.repeaters search A1B2 page 2
```

Search matches overlapping 1-, 2-, and 3-byte path hashes. Each matching row
shows its stored SNR and a whole `s`, `m`, or `h` age measured from the most
recent recording. Search pages contain up to six rows.

Seed or correct a prefix:

```text
set recent.repeater A1B2C3 8.5
```

Clear learned and manually seeded rows:

```text
clear recent.repeater
```

Rows are sorted by prefix width, then SNR. A full direct retry failure lowers
the matching row by `0.25 dB`. Unfiltered paged replies contain up to `10`
rows; an unpaged local serial query prints the full table.

## Direct Path Overrides

`outpath` and `altpath` apply to the current remote client ACL entry. They need
remote client context, so they are not useful from the local serial CLI.

Set paths with comma-separated hop hashes. Each hop must be `2`, `4`, or `6`
hex characters, and all hops in one path must use the same width. Hex input is
case-insensitive. Replies use uppercase hex and retain the commas, so the value
can be copied directly into another `set outpath` or `set altpath` command.

```text
get outpath
get outpath path
set outpath A1B2C3,D4E5F6
set outpath path
set outpath direct
set outpath clear
set outpath flood
get altpath
set altpath 71CE82,BA09F0
set altpath clear
```

For example, both `set altpath 600000,0d2784,f8dada` and
`Set altpath 600000,0d2784,f8dada` store the same path and reply with:

```text
> 600000,0D2784,F8DADA
```

The first CLI word is case-insensitive (`set`, `Set`, and `SET` are the same,
as are `get`, `Get`, and the other command verbs). Argument case is preserved.

`get outpath path` reports the reciprocal path received after the latest flood
login without changing `outpath`. That packet arrives asynchronously, so retry
shortly if the first query reports `> path pending`; the observation window
expires after one minute. The login path is not automatically selected.
`set outpath path` copies the observed route into `outpath` and saves it; it
leaves the current route unchanged when no observed route is available.

`set outpath direct` sets a zero-hop direct route for a client reachable without
repeaters. `set outpath clear` forgets the override, replies
`> outpath cleared`, and lets normal path discovery fill it again.
`set outpath flood` forces replies to use flood packets until the client logs in
again.

When `outpath` is a valid direct path and `altpath` is also a valid, different
direct path, repeater DM replies send two packets: one on `outpath` and one on
`altpath`. The secondary `altpath` copy does not create its own direct-retry
state, so retry tracking stays attached to the primary `outpath` packet.
`altpath clear` disables the secondary direct reply. `altpath flood` is accepted
for command symmetry, but it does not create a second flood reply; only a valid
direct `altpath` sends the second packet.

## Direct Retry Settings

Direct retry applies to direct-routed packets. A queued resend is canceled when the next-hop echo is heard. Repeaters expose the settings below; non-repeater firmware uses the same packet-type timing rules with fixed shared base/step timing.

| Setting | What it does | How to use | Example |
| --- | --- | --- | --- |
| `retry.preset` | Applies shared direct and flood retry defaults. Values: `infra`, `rooftop`, `mobile` or `0`, `1`, `2`. | `get retry.preset`, `set retry.preset <value>` | `set retry.preset rooftop` |
| `direct.retry.heard` | Uses the recent repeater table as the direct retry eligibility gate. | `get direct.retry.heard`, `set direct.retry.heard on/off` | `set direct.retry.heard on` |
| `direct.retry.margin` | SNR margin in dB above the SF-specific receive floor. | `get direct.retry.margin`, `set direct.retry.margin <0-40>` | `set direct.retry.margin 5` |
| `direct.retry.count` | Maximum direct retry attempts after initial TX. Direct-routed type 2 text packets always use 21 attempts regardless of this setting or the short-path cap. | `get direct.retry.count`, `set direct.retry.count <1-15>` | `set direct.retry.count 15` |
| `direct.retry.base` | Base wait in milliseconds before retry; packet-length add-on is 3x for TRACE and ANON_REQ/type 7, 7x for TXT_MSG/type 2, and 6x for other direct retry packets. | `get direct.retry.base`, `set direct.retry.base <10-5000>` | `set direct.retry.base 175` |
| `direct.retry.step` | Milliseconds added per retry attempt after the base, packet-length add-on, and random forwarding jitter. | `get direct.retry.step`, `set direct.retry.step <0-5000>` | `set direct.retry.step 100` |
| `direct.retry.cr` | Adaptive coding-rate thresholds for repeater direct retry packets. Repeaters use `CR4`, `CR5`, `CR7`, or `CR8`, then escalate by attempt: CR4, CR5, CR7, CR7, then CR8 from a CR4 start; CR5, CR7, CR7, then CR8 from a CR5 start. Non-repeaters start at the current radio CR and follow the same escalation pattern, clamped at CR8. | `get direct.retry.cr`, `set direct.retry.cr <cr4_min>,<cr5_min>,<cr7_min>,<cr8_max>`, `set direct.retry.cr off` | `set direct.retry.cr 10.0,7.5,2.5,0` |

The default adaptive coding-rate profile is `10.0,7.5,2.5,2.5`.
SNR `10.0 dB` and up uses `CR4`, `7.5 dB` and up uses `CR5`,
`2.5 dB` and down uses `CR8`, and the middle band uses `CR7`. If no
recent repeater table entry is available, retry packets use `CR5`. Use
`set direct.retry.cr off` to disable adaptive coding-rate overrides. Repeater
attempts escalate from the adaptive starting CR: `CR4`, `CR5`, `CR7`, `CR7`,
then `CR8` from a `CR4` start; `CR5`, `CR7`, `CR7`, then `CR8` from a `CR5`
start. Non-repeaters use the current radio CR as the first retry CR and follow
the same pattern up to `CR8`.

Preset details:

| Preset | Base | Count | Step | SNR gate |
| --- | ---: | ---: | ---: | --- |
| `infra` | `275 ms` | `4` | `150 ms` | SF floor + `15 dB` |
| `rooftop` | `175 ms` | `15` | `100 ms` | SF floor + `5 dB` |
| `mobile` | `175 ms` | `15` | `50 ms` | SF floor |

Example for a quiet fixed repeater:

```text
set retry.preset rooftop
set direct.retry.heard on
set direct.retry.margin 5
```

Example for a moving or weak-link node:

```text
set retry.preset mobile
set direct.retry.margin 0
```

## Flood And Advert Settings

Flood retry applies to flood-routed packets. A queued retry is canceled when the
same packet is heard from a qualifying, non-ignored repeater. Bridge mode uses
the bucket rules below instead.

| Setting | What it does | How to use | Example |
| --- | --- | --- | --- |
| `flood.retry.count` | Base flood retry attempts after initial TX. Role path scaling happens first, then all builds apply payload caps: `REQ` is `0`; `GRP_TXT` keeps up to `15`; remote-login `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH` keep up to `15` at path count 0 and cap at `2` in transit; every other flood type caps at `1`. A lower calculated value is preserved, and `0` disables flood retry. | `get flood.retry.count`, `set flood.retry.count <0-15>` | `set flood.retry.count 7` |
| `flood.retry.path` | Maximum path hash count eligible for flood retry, or `off` to disable the gate. | `get flood.retry.path`, `set flood.retry.path <0-63/off>` | `set flood.retry.path 1` |
| `flood.retry.group.path` | Additional path gate for group data (`type=6`) flood retries. The stricter of this and `flood.retry.path` applies; `off` disables only this additional gate. Setting the general path gate to `0` forces this setting to `off`; a named preset restores the default of `1`. | `get flood.retry.group.path`, `set flood.retry.group.path <0-63/off>` | `set flood.retry.group.path 1` |
| `flood.retry.advert` | Allows or blocks retry for node advert packets (`type=4`). Default is `off`. | `get flood.retry.advert`, `set flood.retry.advert on/off` | `set flood.retry.advert off` |
| `flood.retry.prefixes` | Target prefixes. If set, only same-packet echoes from matching last-hop prefixes cancel a retry. | `get flood.retry.prefixes`, `set flood.retry.prefixes <prefixes/none/off>` | `set flood.retry.prefixes BEEBB0,425E5C` |
| `flood.retry.ignore` | Ignored prefixes. In non-bridge retry, ignored last-hop echoes do not cancel retry. | `get flood.retry.ignore`, `set flood.retry.ignore <prefixes/none/off>` | `set flood.retry.ignore 71CE82,C7618C` |
| `flood.retry.bridge` | Enables bucket-based bridge retry logic. | `get flood.retry.bridge`, `set flood.retry.bridge on/off` | `set flood.retry.bridge on` |
| `flood.retry.bucket.<n>` | Shows one bridge bucket. Buckets are numbered `1`-`6`. | `get flood.retry.bucket.<n>` | `get flood.retry.bucket.1` |
| `flood.retry.bucket` | Sets bridge bucket prefixes. | `set flood.retry.bucket <1-6> <prefixes/none/off>` | `set flood.retry.bucket 1 71CE82,C7618C` |

Extended `flood.rule` rows can select which received floods may use these
settings. For example, enable bridge completion only for Public and
`#hamradio` with `set flood.rule type=any channel=public retry` and a second
row using `channel=#hamradio`. Adding the first active `retry` row changes
received retry selection into an allow-list; deleting the last restores the
legacy global selection. `channel=hash:XX` is available when only the visible
one-byte hash is known, but it is not authenticated and is collision-prone.
For example, `hash:11` includes Public and any other packet using `0x11`;
`channel=public` alone performs the Public-key MAC/decrypt check.

Bridge buckets may also be reused as passive `flood.channel.scope`
path selectors with `path=bucket:<1-6>`. This does not require
`flood.retry.bridge` to be enabled and does not depend on recent-repeater
freshness or the retry ignore list.

Forwarded neighbor adverts also use an automatic echo guard in every build. If this node hears a downstream, longer-path echo after transmitting an advert whose signed timestamp is less than six hours old, it will not forward that exact advert again during the six-hour age window. This guard works even when `flood.retry.advert` is off and does not affect self-originated adverts.

Self-originated advert retries are deliberately slow: the first retry waits at least one extra minute beyond the normal airtime-aware delay. Queueing a newer self advert retires queued and future retry attempts for older self adverts without disturbing other flood retry sequences. Companion firmware allows the one slow retry for its own adverts but not for neighbor adverts it relays.

The shared retry preset sets these flood defaults:

| Preset | Retry count | Path gate | Group-data path gate |
| --- | ---: | ---: | ---: |
| `infra` | `1` | `1` | `1` |
| `rooftop` | `3` | `2` | `1` |
| `mobile` | `15` | `1` | `1` |

Example for path-gated retry:

```text
set retry.preset rooftop
set flood.retry.path 1
set flood.retry.group.path 1
set flood.retry.advert off
set flood.retry.ignore 71CE82,C7618C
```

## North South Buckets

Buckets describe groups of repeaters on different sides of this relay. Bucket
numbers do not have built-in meanings; this example uses bucket `1` for North
and bucket `2` for South.

```text
              North bucket 1
        +-----------------------+
        | A1B2C3       D4E5F6   |
        | North A      North B  |
        +-----------+-----------+
                    |
                    v
              +-----------+
              | This node |
              +-----------+
                    ^
                    |
        +-----------+-----------+
        | 71CE82       C7618C   |
        | South A      South B  |
        +-----------------------+
              South bucket 2
```

Configure the buckets:

```text
set flood.retry.bridge on
set flood.retry.bucket 1 A1B2C3,D4E5F6
set flood.retry.bucket 2 71CE82,C7618C
set flood.retry.ignore none
```

Packet heard from the North:

```text
     heard source
         |
         v
  +--------------+        retry targets
  | North bucket | -----> South bucket
  | bucket 1     | -----> Other fresh/unbucketed relays
  +--------------+
```

Packet heard from the South:

```text
     heard source
         |
         v
  +--------------+        retry targets
  | South bucket | -----> North bucket
  | bucket 2     | -----> Other fresh/unbucketed relays
  +--------------+
```

Packet heard from an unbucketed or pathless source:

```text
     heard source
         |
         v
  +--------------+        retry targets
  | Other bucket | -----> North bucket
  | implicit     | -----> South bucket
  +--------------+
```

Bridge retry stays eligible until every target bucket has been heard or
`flood.retry.count` is exhausted. A configured bucket is a target only when at
least one of its prefixes is fresh in `recent.repeater`. Prefixes in
`flood.retry.ignore` never count as bucket hits.

Configuration reports a warning when prefixes in different buckets, including
bucket 7 (`flood.retry.prefixes`), share the same first byte. A 1-byte path cannot
distinguish those buckets. Bridge mode therefore excludes every matching bucket
when that short prefix is the source, and credits every matching target bucket
when it is heard as an echo. This prevents an ambiguous short prefix from keeping
an impossible target outstanding through every retry.

Each flood retry wait retains the fixed maximum-frame plus 20 packet-airtime
delay, then adds random jitter from zero to 200 percent of one additional packet
airtime. This keeps nearby repeaters from repeating a collision on fixed timing
while capping the added wait at two frames.

Only one enhanced retry sequence can be active for the same logical flood
packet. Identical floods still receive their normal transmission, but do not
multiply the extra attempts. Evicted queued retries release their bridge state,
and the final echo wait does not reserve a packet-pool entry.

Earlier path hops from a successful bridge echo refresh a separate per-bucket
reachability cache without an SNR value. Only the final hop, which actually sent
the received RF frame, updates `recent.repeater` and its SNR. Indirect path hops
therefore cannot change direct-retry SNR gating or coding-rate selection.

## Troubleshooting

If advert packets are still retrying:

```text
get flood.retry.advert
set flood.retry.advert off
```

If ignored prefixes still appear in `flood retry good` logs:

```text
get flood.retry.ignore
set flood.retry.ignore <prefix>
```

The ignored prefix must match the last hop shown as `heard=<prefix>`. For example, this log needs `C7618C` in the ignore list:

```text
flood retry good (... path=7773D0>C7618C, heard=C7618C ...)
```

If retries are too aggressive:

```text
set flood.retry.count 1
set flood.retry.path 1
set direct.retry.count 4
```

If retries are too sparse:

```text
set flood.retry.count 7
set flood.retry.path 2
```
