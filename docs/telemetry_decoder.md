# Telemetry history decoder

Decode the Base64 reply from MeshCore repeater telemetry commands into a
timestamped table. Decoding happens entirely in this browser; the pasted reply
is not uploaded or sent anywhere.

## Commands to run

Run one of these commands in a local serial CLI or a remote administrator CLI
session, then copy the complete reply beginning with `> ` into the decoder.

| Data | Newest page | Older-page example | Samples per page |
|---|---|---|---|
| MCU temperature | `get telemetry.temp` | `get telemetry.temp 2` | 48 (24 hours) |
| Battery voltage | `get telemetry.volt` | `get telemetry.volt 3` | 48 (24 hours) |
| GPS position | `get telemetry.gps` | `get telemetry.gps 2` | 24 (12 hours) |

Page `1` is always the newest. Temperature and voltage support pages `1`-`7`.
GPS normally supports pages `1`-`6`; its configured retention can be changed
from one through 30 days:

```text
set telemetry.gps 7
get telemetry.gps 1
get telemetry.gps 14
```

The GPS setter reports the number of days and pages the device could actually
allocate. Telemetry history is boot-local, so a freshly rebooted repeater may
reply that its history is empty.

## Decode a reply

<div class="telemetry-tool" data-telemetry-decoder>
  <div class="telemetry-examples" aria-label="Load an example reply">
    <strong>Try an example:</strong>
    <button type="button" data-telemetry-example="temperature">Temperature</button>
    <button type="button" data-telemetry-example="voltage">Voltage</button>
    <button type="button" data-telemetry-example="gps">GPS</button>
  </div>

  <label for="telemetry-reply-input">CLI reply or Base64 payload</label>
  <textarea
    id="telemetry-reply-input"
    data-role="input"
    spellcheck="false"
    autocomplete="off"
    placeholder="> paste the Base64 telemetry reply here"
    aria-describedby="telemetry-input-help"
  ></textarea>
  <p class="telemetry-tool-help" id="telemetry-input-help">
    Paste either the complete <code>&gt; ...</code> response or Base64 alone.
    Press Ctrl/Command+Enter to decode.
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

## Example calls and replies

The buttons above load these synthetic but protocol-valid examples. Real
responses have the same `> ` prefix and are auto-detected from payload type
`0x11`, `0x12`, or `0x13`.

### Temperature

```text
get telemetry.temp
> EUDUcWoeMAVZVVVVUVVVVVXVVQACTJlSwEyZMmTJkuXKkyZEeNFARIcOFChQoUOHDiRY0aPI/ypUuXMmTA==
```

### Battery voltage

```text
get telemetry.volt 1
> EkDUcWoeMAAB5+bl5eTj4uLh4ODf3t7d3Nvb2tnZ2NfX1tXU1NPS0tHQ0M/Ozc3My8vKycnI/w==
```

### GPS

```text
get telemetry.gps 1
> EwB9cmoeGIChAxwAR0i3AgAAAAAAAAAAAAAAAAKAAAAAAIAAAAD/9ABAAX/+AAgAYAAAB//wAD/+gAAAA/+wAP/8ABwAEAEABgAAAA//gAX/7AAv/3/8AAf/oAF//QAYACAAgAc=
```

## Reading the table

- Timestamps default to UTC. Select **Show browser-local time** to convert
  them for display and CSV export.
- Temperature preserves exact whole degrees from `-50 C` through `+77 C`, plus
  missing, below-range, and above-range states.
- Voltage preserves hundredths of a volt from `1.88 V` through `4.40 V`, plus
  missing and out-of-range states.
- GPS positions are reconstructed from signed 10-meter differentials. A zero
  differential after the page origin is inherently ambiguous: it can represent
  an unchanged fix, movement below the encoded resolution, or no fix. The table
  labels those rows rather than inventing a coordinate.
- A GPS clipping warning means at least one movement exceeded the differential
  range, so positions after that point can be less accurate.

For the byte-level layouts, see
[Read repeater telemetry history](cli_commands.md#read-repeater-telemetry-history).
