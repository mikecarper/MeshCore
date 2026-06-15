# Halo Direct Message Retry Settings

This file covers only CLI settings and helper commands added for Halo direct-message retry behavior. Use `docs/cli_commands.md` for the general MeshCore CLI.

Halo retry applies to direct-routed packets. A queued resend is canceled when the next-hop echo is heard.

## Quick Start

```text
set retry.preset rooftop
set direct.retry.heard on
get retry.preset
get direct.retry.heard
get direct.retry.count
get direct.retry.base
get direct.retry.step
```

Use prefixes from the analyzer, neighbors list, or `get recent.repeater` after the repeater has been online for a few hours.

## Added Halo Settings

| Setting | What it does | How to use | Example |
| --- | --- | --- | --- |
| `recent.repeater` | Shows, seeds, or clears the recent repeater prefix/SNR table used by direct retry. | `get recent.repeater`, `get recent.repeater <page>`, `set recent.repeater <prefix> <snr_db>`, `clear recent.repeater` | `set recent.repeater A1B2C3 -8.5` |
| `outpath` | Overrides the primary direct route used for replies to the current remote client. | `get outpath`, `set outpath <hops>`, `set outpath direct`, `set outpath clear`, `set outpath flood` | `set outpath A1B2C3,D4E5F6` |
| `altpath` | Optional second direct route used for duplicate response attempts to the current remote client. | `get altpath`, `set altpath <hops>`, `set altpath clear` | `set altpath A1B2C3,D4E5F6` |
| `retry.preset` | Applies direct retry defaults. Values: `infra`, `rooftop`, `mobile` or `0`, `1`, `2`. | `get retry.preset`, `set retry.preset <value>` | `set retry.preset rooftop` |
| `direct.retry.heard` | Uses the recent repeater table as the direct retry eligibility gate. | `get direct.retry.heard`, `set direct.retry.heard on/off` | `set direct.retry.heard on` |
| `direct.retry.margin` | SNR margin in dB above the SF-specific receive floor. | `get direct.retry.margin`, `set direct.retry.margin <0-40>` | `set direct.retry.margin 5` |
| `direct.retry.count` | Maximum direct retry attempts after initial TX. | `get direct.retry.count`, `set direct.retry.count <1-15>` | `set direct.retry.count 15` |
| `direct.retry.base` | Base wait in milliseconds before retry. | `get direct.retry.base`, `set direct.retry.base <10-5000>` | `set direct.retry.base 175` |
| `direct.retry.step` | Milliseconds added per retry attempt. | `get direct.retry.step`, `set direct.retry.step <0-5000>` | `set direct.retry.step 100` |
| `direct.retry.cr` | Adaptive coding-rate thresholds for direct retry packets. Uses `CR4`, `CR5`, `CR7`, or `CR8`; `CR6` is never selected. | `get direct.retry.cr`, `set direct.retry.cr <cr4_min>,<cr5_min>,<cr7_min>,<cr8_max>`, `set direct.retry.cr off` | `set direct.retry.cr 10.0,7.5,2.5,0` |

## Recent Repeater Table

Direct retry uses the recent repeater table when `direct.retry.heard` is `on`.

Show learned rows:

```text
get recent.repeater
get recent.repeater 2
get recent.repeater page 3
```

Seed or correct a prefix:

```text
set recent.repeater A1B2C3 8.5
```

Clear learned and manually seeded rows:

```text
clear recent.repeater
```

Rows are sorted by prefix width, then SNR. A full direct retry failure lowers the matching row by `0.25 dB`.

Serial CLI pages contain up to `128` rows. Remote LoRa CLI pages contain up to `7` rows.

## Direct Path Overrides

`outpath` and `altpath` apply to the current remote client ACL entry. They need remote client context, so they are not useful from the local serial CLI.

Set paths with comma-separated hop hashes. Each hop must be `2`, `4`, or `6` hex characters, and all hops in one path must use the same width.

```text
get outpath
set outpath A1B2C3,D4E5F6
set outpath direct
set outpath clear
set outpath flood

get altpath
set altpath A1B2C3,D4E5F6
set altpath clear
```

`set outpath direct` sets a zero-hop direct route for a client reachable without repeaters. `set outpath clear` forgets the override and lets normal path discovery fill it again. `set outpath flood` forces replies to use flood packets until the client logs in again. `altpath` sends a duplicate reply over a second direct route; clearing it returns replies to a single route.

## Direct Retry Details

The default adaptive coding-rate profile is `10.0,7.5,2.5,2.5`. SNR `10.0 dB` and up uses `CR4`, `7.5 dB` and up uses `CR5`, `2.5 dB` and down uses `CR8`, and the middle band uses `CR7`. If no recent repeater table entry is available, retry packets use `CR5`.

Use `set direct.retry.cr off` to disable adaptive coding-rate overrides. If adaptive selection chooses `CR4`, retries after the third attempt use `CR5`.

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

## Troubleshooting

If direct retries are too aggressive:

```text
set direct.retry.count 4
set direct.retry.margin 10
```

If direct retries are too sparse:

```text
set direct.retry.count 15
set direct.retry.margin 0
```

If direct retry is skipping a path you expect it to retry:

```text
get direct.retry.heard
get recent.repeater
```

Either disable the heard gate with `set direct.retry.heard off`, or seed the next-hop prefix with `set recent.repeater <prefix_hex_6> <snr_db>`.
