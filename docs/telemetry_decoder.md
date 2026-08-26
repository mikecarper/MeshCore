# Telemetry decoder

Paste raw hexadecimal packet data copied from the
[Let's Mesh packet analyzer](https://analyzer.letsmesh.net/packets) to decode a
repeater's scheduled temperature, battery-voltage, or external I2C-voltage
snapshot. The decoder also accepts the payload hex without its MeshCore packet
header and the Base64 pages returned by repeater or room-server CLI commands.
Decoding happens entirely in this browser; pasted data is not uploaded or sent
anywhere.

The **Source ID** in a raw-packet result is the first eight bytes of the sending
repeater's public key. Match that 16-character hex value against the public-key
prefix recorded for your repeaters. It comes from the telemetry payload itself,
so it is available even when only the payload was copied.

## Send raw telemetry packets

From an administrator CLI session, first configure a direct route to the MQTT
observer that will receive and upload the raw packets. Use `direct` when the
observer is a zero-hop neighbor:

```text
set telemetry.tx direct
send telemetry.tx now
```

For a routed observer, provide its comma-separated hop hashes instead:

```text
set telemetry.tx A1B2,C3D4
send telemetry.tx now
```

Configuring the route also enables the default two-day schedule. To keep the
route but use it only for manual tests, turn off the schedule before sending:

```text
set telemetry.tx schedule off
send telemetry.tx now
```

The `send telemetry.tx now` command queues one `TTB1` temperature packet, one
`TVB1` battery-voltage packet, and `IVB1` chunks for every connected I2C voltage
channel. `TTB1` and `TVB1` carry up to 165 samples. Each `IVB1` carries 64, so a
full 192-point/four-day channel takes three packets. Channels whose retained
history is entirely zero are treated as disconnected and are not sent. The
command requires at least one collected base sample and works even when the
automatic schedule is off.

## Decode a packet

<div class="telemetry-tool" data-telemetry-decoder>
  <div class="telemetry-examples" aria-label="Load an example reply">
    <strong>Try an analyzer example:</strong>
    <button type="button" data-telemetry-example="packetTemperature">Temperature packet</button>
    <button type="button" data-telemetry-example="packetVoltage">Voltage packet</button>
    <button type="button" data-telemetry-example="packetExternalVoltage">I2C voltage packet</button>
    <button type="button" data-telemetry-example="externalVoltage">I2C voltage CLI page</button>
  </div>

  <label for="telemetry-reply-input">Raw packet or payload hex</label>
  <textarea
    id="telemetry-reply-input"
    data-role="input"
    spellcheck="false"
    autocomplete="off"
    placeholder="Paste hexadecimal Raw Data from the analyzer packet page"
    aria-describedby="telemetry-input-help"
  ></textarea>
  <p class="telemetry-tool-help" id="telemetry-input-help">
    Spaces, line breaks, colons, dashes, a leading <code>0x</code>, and a quoted
    JSON field are accepted. CLI Base64 replies are also auto-detected. Paste
    multiple compatible packet or reply lines together to merge them by
    timestamp before downloading one CSV. Press Ctrl/Command+Enter to decode.
  </p>

  <div class="telemetry-actions">
    <button class="telemetry-primary-action" type="button" data-role="decode">Decode telemetry</button>
    <button type="button" data-role="clear">Clear</button>
    <label class="telemetry-local-time">
      <input type="checkbox" data-role="local-time">
      Show browser-local time
    </label>
  </div>

  <div class="telemetry-error" data-role="error" role="alert" aria-live="polite" hidden></div>

  <section class="telemetry-results" data-role="results" aria-live="polite" hidden>
    <div class="telemetry-results-header">
      <h2 data-role="result-title">Decoded telemetry</h2>
      <button type="button" data-role="download">Download CSV</button>
    </div>
    <dl class="telemetry-summary" data-role="summary"></dl>
    <div class="telemetry-warnings" data-role="warnings" hidden>
      <strong>Decode notes</strong>
      <ul data-role="warning-list"></ul>
    </div>
    <div class="telemetry-table-wrap">
      <table class="telemetry-table" data-role="table"></table>
    </div>
  </section>
</div>

## Analyzer hex examples

The buttons load synthetic, protocol-valid zero-hop RAW_CUSTOM packets. A real
scheduled snapshot normally has 165 samples and is much longer. Routed packets
also contain path bytes before the `TTB1` or `TVB1` payload magic; the decoder
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

All three examples identify the source as `1122334455667788`.

## CLI history pages

The same page continues to decode the padded Base64 returned by these
administrator commands:

| Data | Newest page | Older-page example | Samples per page |
|---|---|---|---|
| MCU temperature | `get telemetry.temp` | `get telemetry.temp 2` | 48 (24 hours) |
| Battery voltage | `get telemetry.volt` | `get telemetry.volt 3` | 48 (24 hours) |
| I2C voltage | `get telemetry.volt.i2c 2` | `get telemetry.volt.i2c 2 4` | 48 (24 hours) |
| GPS position | `get telemetry.gps` | `get telemetry.gps 2` | 24 (12 hours) |

Paste either the complete reply beginning with `> ` or Base64 alone. For
example:

```text
get telemetry.volt 1
> EkDUcWoeMAAB5+bl5eTj4uLh4ODf3t7d3Nvb2tnZ2NfX1tXU1NPS0tHQ0M/Ozc3My8vKycnI/w==
```

Run `get telemetry.volt.i2c` without a channel first to list connected
channels. For each channel, collect pages `1` through `4`. Paste all four reply
lines into the decoder at once; it merges their timestamps into one 192-point,
four-day table and downloads them as one CSV. The same merging works for the
three `IVB1` analyzer packets from a full channel. Inputs must have the same
telemetry type, source, LPP channel, and sample interval.

An INA3221 exposes its three enabled hardware inputs as three consecutive LPP
channels. When it is the only external sensor these are normally `2`, `3`, and
`4`, in hardware-input order. Other sensors can shift the numbers, so copy the
IDs reported by `get telemetry.volt.i2c` rather than assuming them.

## Reading the table

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
- GPS positions are reconstructed from signed 10-meter differentials. A zero
  differential after the page origin is inherently ambiguous: it can represent
  an unchanged fix, movement below the encoded resolution, or no fix. The table
  labels those rows rather than inventing a coordinate.
- A GPS clipping warning means at least one movement exceeded the differential
  range, so positions after that point can be less accurate.

GPS history remains available through the administrator CLI, but `telemetry.tx`
never puts GPS in RAW_CUSTOM packets. Location data therefore must come from a
CLI Base64 page rather than analyzer hex.

For the byte-level layouts, see
[Read repeater telemetry history](cli_commands.md#read-repeater-telemetry-history).
