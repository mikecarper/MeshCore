<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/telemetry_decoder/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Telemetry decoder

Paste a Base64 CLI reply or raw packet hex to view telemetry and download CSV.
Decoding runs in your browser; pasted data is not uploaded.
[Jump to CLI commands](#cli-history-pages).

## Decode a packet

<div class="telemetry-tool" data-telemetry-decoder>
  <p>Enable JavaScript on the documentation website to use the decoder.</p>
</div>

<p class="meshcore-hosted-doc-link">GitHub's Markdown preview cannot run the decoder.
<a href="https://mikecarper.github.io/MeshCore/telemetry_decoder/#decode-a-packet">Open the working decoder on MeshCore Docs</a>.</p>

## CLI history pages

Run a command in the repeater or room-server administrator CLI, then paste the
reply (including the optional `>` prefix) or just the Base64 into the decoder.

| Data | Newest page | Older-page example | Samples per page |
|---|---|---|---|
| MCU temperature | `get telemetry.temp` | `get telemetry.temp 2` | 48 (24 hours) |
| Battery voltage | `get telemetry.volt` | `get telemetry.volt 3` | 48 (24 hours) |
| I2C voltage | `get telemetry.volt.i2c 2` | `get telemetry.volt.i2c 2 4` | 48 (24 hours) |
| GPS position | `get telemetry.gps` | `get telemetry.gps 2` | 24 (12 hours) |
| Main-radio noise floor | `get telemetry.noise` | `get telemetry.noise 2` | Up to 176 (44 hours) |
| Radio2 noise floor | `get telemetry.noise2` | `get telemetry.noise2 2` | Up to 176 (44 hours) |

Run `get telemetry.volt.i2c` without a channel to list connected channels.

For a compact seven-day view, request the daily minimum and maximum instead:

```text
get telemetry.temp minmax
get telemetry.volt minmax
get telemetry.minmax
```

Paste one reply into the decoder. It shows each rolling day's minimum and
maximum, their timestamps, and the samples immediately before and after each
extreme. Day 1 is the most recent 24 hours; Day 7 is the oldest. Samples are
30 minutes apart. A neighbor can fall in an adjacent day; missing or unretained
samples display as Missing. Temperature and battery history are held in RAM
and start fresh after a reboot.
`get telemetry.minmax` combines temperature and battery voltage for all seven
days in one response; the table and CSV identify each measurement separately.

Each individual reply fits one CLI response packet. Decode each min/max reply
separately and use Download CSV to save its table.

## Send raw telemetry packets

On a repeater, set the route to the MQTT observer, then send a batch. Use
`direct` for a zero-hop observer:

```text
set data.tx path direct
send telemetry.tx now
```

For a routed observer, provide its comma-separated hop hashes instead:

```text
set data.tx path A1B2,C3D4
send telemetry.tx now
```

Run `get telemetry.tx` to check the route and last completed send. Manual sends
work even with the automatic schedule off.

## Transmission reference

Transmission and packet-type settings are repeater-only. Room servers provide
local history and CLI reads without transmission batches.

Telemetry with a non-empty hop path uses the existing direct-message echo
retries, following `direct.retry`, `direct.retry.count`, `direct.retry.heard`,
and the direct-retry coding-rate policy. Retries stop when a matching downstream
forwarding echo is heard. The default zero-hop `direct` route does not use echo retries.
Updated relays can retry their own forwarding hop; stock relays remain compatible
and their forwarding can provide the echo. This adds no packet format or ACK:
an echo confirms forwarding, not final delivery to the observer. A queue failure
still retries only the packet that failed to queue, after 30 minutes.

The shared route does not enable telemetry. Automatic telemetry is off on a
fresh install; to explicitly keep the route manual-only before sending:

```text
set telemetry.tx schedule off
send telemetry.tx now
```

Choose which packet types to send with these saved settings:

| Setting | Data |
|---|---|
| `telemetry.tx.temp` | Temperature (`TTB1`) |
| `telemetry.tx.volt` | Battery voltage (`TVB1`) |
| `telemetry.tx.volt.i2c` | External I2C voltage (`IVB1`), all channels |
| `telemetry.tx.noise` | Main-radio noise floor (`N1B1`) |
| `telemetry.tx.noise2` | Radio2 noise floor (`N2B1`) |

All five default to `on`. Read one with `get telemetry.tx.temp`, change it
with `set telemetry.tx.temp <on|off>`, or inspect all five with
`get telemetry.tx.types`. `set telemetry.tx.types on|off` changes all five.
The choices apply to manual and scheduled sends. They do not enable the master
schedule or stop history collection and CLI reads. GPS is never included in
these packets.

For example, send only battery voltage:

```text
set telemetry.tx.types off
set telemetry.tx.volt on
send telemetry.tx now
```

With all types off, a manual send returns
`Err - no telemetry packet types enabled`. If the selected types have no data
available, it returns `Err - no enabled telemetry data available`.

With all types selected, `send telemetry.tx now` queues one `TTB1` temperature
packet, one `TVB1` battery-voltage packet, and `IVB1` chunks for every connected
I2C voltage channel. `TTB1` and `TVB1` carry up to 165 samples. Each `IVB1`
carries 64, so a full 192-point/four-day channel takes three packets. Channels
whose retained history is entirely zero are treated as disconnected and are
not sent. The
command works even when the automatic schedule is off. Selected I2C or noise
data can be sent without a collected temperature or battery sample.

Packets in manual and scheduled batches are queued 90 seconds apart. Channel
activity and airtime limits can delay actual transmission further. Turning a
type off cancels only its batch work that has not yet been queued; packets
already queued can still transmit. Remaining work keeps the same pacing.
Turning a type on includes it in the next batch.

When selected, it also queues a `N1B1` noise-floor packet once the main radio
has completed a 15-minute history bucket. With radio2 enabled and `noise2`
selected, it queues a separate `N2B1` packet once that profile has a completed
bucket. Each holds the latest 255 positions: up to 63 hours 45 minutes at
15-minute resolution. A position can be Missing; failed measurements do not
silently shorten the timeline.
The automatic transmission cadence and startup wait for 165 half-hour base
positions (about 82.5 hours) are unchanged, including when only noise or I2C
transmission is selected. Recording a new noise-floor position every 15 minutes
does not transmit a packet every 15 minutes.

Use `get telemetry.tx` to see the source ID, path, and `last=30s`, `last=30m`,
or `last=2h` for the age of this node's latest completed telemetry transmission.
The age includes completed retries and manual sends while the schedule is off,
but excludes queue acceptance and relayed packets; it does not confirm observer
receipt. It shows `last=never` until the first completed send after each reboot.
The age uses uptime rather than the clock, and durations of a day or more remain
in hours, such as `last=48h`.

Schedule changes are saved transactionally; a failed write does not truncate
the previous preferences. A legacy route must be successfully migrated into
`data.tx` before its schedule can run. Corrupt, unreadable, or ambiguously
committed preferences stop automatic and pending sends. `get telemetry.tx`
shows `fault=prefs`, and manual sends return an error instead of acknowledging
work that cannot run. Such a storage fault must be repaired before settings
can be changed; the firmware does not overwrite an unmigrated legacy file.

## Find telemetry packets

Copy raw packet hex from the [Let's Mesh packet analyzer](https://analyzer.letsmesh.net/packets)
or the filtered view below. The decoder accepts both a full MeshCore packet and
payload hex without the MeshCore packet header.

[Open the two-day telemetry view in CascadiaMesh Analyzer](https://analyzer.cascadiamesh.org/#/packets?timeWindow=2880&filter=type%20%3D%3D%20RAW_CUSTOM%20%26%26%20age%20%3C%202d%20%26%26%20%28payload.raw%20starts_with%20%2254544231%22%20%7C%7C%20payload.raw%20starts_with%20%2254564231%22%20%7C%7C%20payload.raw%20starts_with%20%2249564231%22%20%7C%7C%20payload.raw%20starts_with%20%224E314231%22%20%7C%7C%20payload.raw%20starts_with%20%224E324231%22%29).
The link selects RAW_CUSTOM packets whose data starts with
any of the five telemetry prefixes below. To use the filter box directly, paste
this as one line:

```text
type == RAW_CUSTOM && age < 2d && (payload.raw starts_with "54544231" || payload.raw starts_with "54564231" || payload.raw starts_with "49564231" || payload.raw starts_with "4E314231" || payload.raw starts_with "4E324231")
```

| Data | Payload tag | Hex prefix |
|---|---|---|
| Temperature | `TTB1` | `54544231` |
| Battery voltage | `TVB1` | `54564231` |
| External I2C voltage | `IVB1` | `49564231` |
| Main-radio noise floor | `N1B1` | `4E314231` |
| Radio2 noise floor | `N2B1` | `4E324231` |

`&&` requires every condition; `||` accepts any listed prefix.
`payload.raw` starts at the application data, after the routing bytes.

The URL uses `timeWindow=2880` (two days in minutes). The explicit
`age < 2d` condition preserves that cutoff when refreshing, since the analyzer's
time dropdown only offers fixed windows up to 24 hours. Packet retention and
loading limits still apply: the current server returns at most 10,000 packets,
and mobile clients load at most 1,000, so a busy network may show less than
two days.

## Analyzer hex examples

The buttons load synthetic, protocol-valid zero-hop RAW_CUSTOM packets. A real
snapshot can contain many more samples. Routed packets
also contain path bytes before the telemetry payload magic; the decoder
finds and validates the payload automatically.

### Temperature

```text
3E00545442311122334455667788800092651E0008000102354A4E5082
```

### Battery voltage

```text
3E00545642311122334455667788800092651E000800010264C8FEFFDC
```

### External I2C voltage

```text
3E00495642311122334455667788800092651E00020800000004019026927109C427107FFF
```

### Radio1 noise floor

```text
3E004E3142311122334455667788800092650F00080044CB77DF
```

All four examples identify the source as `1122334455667788`.

## CLI history reference

The decoder accepts the complete reply or Base64 alone. Noise replies also
accept the optional two-character CLI correlation prefix (`01|Base64` or
`01|> Base64`). Example battery-voltage reply:

```text
get telemetry.volt 1
> EkDUcWoeMAAB5+bl5eTj4uLh4ODf3t7d3Nvb2tnZ2NfX1tXU1NPS0tHQ0M/Ozc3My8vKycnI/w==
```

For each I2C channel, collect pages `1` through `4`. Paste all four reply
lines into the decoder at once; it merges their timestamps into one 192-point,
four-day table and downloads them as one CSV. The same merging works for the
three `IVB1` analyzer packets from a full channel. Inputs must have the same
telemetry type, source, LPP channel, radio profile, and sample interval.

Noise history retains eight days (768 quarter-hour positions) per profile in
RAM and resets on reboot. Page `1` is newest; up to five pages are available
as history fills. Paste the replies for one radio together to merge them.
Main-radio and radio2 pages remain separate. A full noise CLI page is 117
decoded bytes or 156 Base64 characters, fitting the current CLI reply buffer
over USB and administrator LoRa CLI. Radio2 history is read with
`get telemetry.noise2`, including when it is not being transmitted.

An INA3221 exposes its three enabled hardware inputs as three consecutive LPP
channels. When it is the only external sensor these are normally `2`, `3`, and
`4`, in hardware-input order. Other sensors can shift the numbers, so copy the
IDs reported by `get telemetry.volt.i2c` rather than assuming them.

## Reading the table

- The **Source ID** is the first eight bytes of the sending repeater's public
  key. Match those 16 hex characters against the repeater's public-key prefix.
  The ID comes from the payload, so it is present even without the packet header.
- Timestamps default to UTC. Select **Show browser-local time** to convert
  them for display and CSV export.
- `TTB1` means a raw temperature snapshot, `TVB1` a raw battery-voltage
  snapshot, and `IVB1` a packed external I2C-voltage chunk. The input summary
  also reports the MeshCore route and path-hop count when a complete packet was
  pasted.
- Temperature preserves exact whole degrees from `-50 C` through `+77 C`, plus
  missing, below-range, and above-range states.
- Voltage preserves hundredths of a volt from `1.88 V` through `4.40 V`, plus
  missing and out-of-range states.
- External I2C voltage uses a 15-bit code at `0.02 V` resolution. Code `0` is
  missing/disconnected; codes `1` through `32767` cover `0.02 V` through
  `655.34 V`. The LPP channel remains part of every page and raw chunk so
  multiple monitor inputs cannot be mixed accidentally.
- `N1B1` is main-radio noise history; `N2B1` is radio2 history. The chart and
  table use dBm. Codes `0`, `1`, and `2` mean `-120`, `-119`, and `-118 dBm`;
  codes `3` through `29` continue in 2 dB steps through `-64 dBm`. Code `30`
  is **MAX**, displayed as `>-64 dBm`, and code `31` is **Missing**. CSV uses
  separate `radio_profile`, `status`, and `noise_floor_dbm` columns. MAX and
  Missing have an empty numeric field and leave gaps in the chart.
- GPS positions are reconstructed from signed 10-meter differentials. A zero
  differential after the page origin is inherently ambiguous: it can represent
  an unchanged fix, movement below the encoded resolution, or no fix. The table
  labels those rows rather than inventing a coordinate.
- A GPS clipping warning means at least one movement exceeded the differential
  range, so positions after that point can be less accurate.

Each noise position combines three fresh noise-floor estimates collected
roughly five minutes apart within its UTC-aligned 15-minute bucket. If any
estimate is `-100 dBm` or lower, the minimum wins, including a bucket containing
both low and high values. Otherwise, if any estimate is `-80 dBm` or higher,
the maximum wins. Otherwise the three estimates are averaged. Minimums round
downward, maximums upward, and averages to the nearest encodable value. Values
below `-120 dBm` clamp to `-120 dBm`; values above `-64 dBm` become MAX.
Fewer than three fresh, valid estimates produce Missing. These are idle RSSI
noise estimates, not received-packet RSSI measurements.

GPS history remains available through the administrator CLI, but `telemetry.tx`
never puts GPS in RAW_CUSTOM packets. Location data therefore must come from a
CLI Base64 page rather than analyzer hex.

For the byte-level layouts, see
[Read repeater telemetry history](cli_commands.md#read-repeater-telemetry-history).
