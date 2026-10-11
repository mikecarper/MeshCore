<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/sensor_tracker/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Sensor dog tracker

Dog tracker mode is an optional mode in the Sensor firmware. It acquires a new
GPS position, sends an encrypted check to one configured owner's Companion,
and waits for that owner's saved lost status. The owner's answer controls the
tracker's reporting rate and awake window.

Tracker mode starts off. It is compiled by default for GPS Sensor builds on
nRF52, ESP32, and RP2040; compact STM32 builds exclude it by default. A build
can override `MESH_ENABLE_SENSOR_TRACKER`. GPS hardware must be detected and
available for position acquisition.

## Set up the owner

Use a Companion build with lost replies enabled. Add the tracker as a saved
contact on the owner's Companion, using its full public key. The responder
accepts saved Chat or Sensor contacts for the encrypted tracker check.

Set the answer locally on the owner's Companion:

```text
set lost.reply no
get lost.reply
```

Use `set lost.reply yes` when the dog is lost. Use `set lost.reply off` to
return an unknown status. These are owner settings; an incoming tracker check
cannot change them. A normal private `Am I lost?` message also receives the
saved answer where the text responder is available, but the automatic Sensor
tracker uses an encrypted request with a matching response tag.

The owner must be reachable during the tracker's receive window. A Room or
public message board does not substitute for the owner's Companion in this
mode.

## Set up the tracker

Configure the Sensor through its local text console. Replace the key below
with the owner's full 64-character hexadecimal public key:

```text
set tracker.owner <owner-public-key>
set tracker.interval 1800
set tracker.lost.interval 60
set tracker.path direct
set tracker.mode dog
get tracker status
```

Check `clock` on both devices before enabling the mode. If needed, use
`time <current-UTC-epoch-seconds>` to set the tracker clock. The owner's
Companion accepts map coordinates only when the request time is within five
minutes of its own clock. Tracker acquisition waits for the provider's validated
multi-sample GPS clock sync as well as its position, allowing cold-start clock
recovery within the acquisition window. If clock sync cannot finish, an operator
can still set the clock locally. Saved routing limits do not reset to grant
floods after clock loss.

`direct` selects a direct radio path with no repeaters. If a repeater path is
known, use comma-separated repeater prefixes, for example
`set tracker.path aa,bb` or `set tracker.path aabb,ccdd`. All prefixes must have
the same length: one, two, or three bytes. `set tracker.path clear` keeps the
path unknown; the flood fallback delay
still applies. Encrypted path replies can update the retained path.

| Command | Meaning |
| --- | --- |
| `get tracker status` | Show mode, saved owner status, intervals, flood lock, clock and storage state. |
| `get tracker.owner` | Show the configured owner's full public key. |
| `get tracker.path` | Show whether the route is direct, saved, or unknown, and its hop count. |
| `set tracker.owner <full-key>` | Save the destination identity. |
| `set tracker.mode dog` | Enable automatic checks and tracker power control. |
| `set tracker.mode off` | Stop automatic checks and restore normal Sensor operation. |
| `set tracker.interval <seconds>` | Normal interval, 60 to 86400 seconds; default 1800. |
| `set tracker.lost.interval <seconds>` | Lost interval, 60 to 86400 seconds; default 60. |
| `set tracker.path <hex\|direct\|clear>` | Save a known path or mark it unknown. |
| `tracker check` | Schedule a check now, if no check is already pending. |

Tracker configuration and routing checkpoints use a separate durable store.
Mode changes do not replace the saved manual GPS or power-saving preferences.
If storage cannot safely retain configuration or a flood reservation, automatic
tracker checks fail closed. Volatile filesystem recovery is unsuitable for this
mode because a restart could otherwise reset the flood limits.

## Reporting and sleep

Each check asks the GPS manager for a new position and validated GPS clock
sync. It discards the previous parser fix and previous time-validation samples,
then stops once both are ready or after 120 seconds.
An old cached position is not reported as a new tracker fix. If acquisition
gets a position but clock sync times out, the fresh position is retained. If no
position is acquired, the encrypted check marks the failure and carries no
position. A retained parser fix or one unvalidated time sample cannot set the
tracker clock.

The tracker then allows 20 seconds for the tagged owner response. `No` uses the
normal reporting interval. `Yes` uses the shorter lost interval and requests a
120-second awake window, renewed by further `Yes` responses. This permits the
tracker to remain reachable while the owner continues to report it as lost.
An unknown answer does not replace the last saved `Yes` or `No`. Intervals run
from the completed check; GPS acquisition and the response window add time.

Between active windows, tracker mode pauses radio reception when there is no
queued radio work, temporary radio setting, update operation, or active console
that needs service. The radio resumes before the next check. Unsolicited
messages sent while reception is paused cannot be received; the owner should
set the status and wait for the next check. A delivery ACK alone does not
contain the owner's lost answer.

Tracker mode temporarily suppresses ordinary periodic advertisements,
subscriptions, and Sensor alerts. Manual requests can still be served while
the radio is awake. Leaving tracker mode restores the normal Sensor behavior.

This uses idle or light sleep while retaining runtime state. It does not use
the nRF52 battery-protection SYSTEMOFF path described in
[nRF52 power management](nrf52_power_management.md). Receiver power control
depends on the hardware: T1000-E retains the GPS backup supply for faster fixes,
and shared GPS power rails may prevent physically cutting receiver power.

nRF52 tracker sleep arms a real one-shot software timer before each System ON
wait. Failure to allocate or queue that timer keeps the loop polling. Sleep
chunks are at most 30 seconds so clock and watchdog housekeeping continue.
ESP32 light sleep uses the board's existing timer wake. RP2040 uses the core's
interrupt-driven idle wait; its physical tracker wake behavior is not yet
qualified by these host tests.

## Flood fallback

Automatic tracker checks retain a known direct path and try it while the
owner cannot be reached. A failed check starts an outage; further failures do
not restart that outage's six-hour clock.

- A recovery flood is eligible only after six hours without a qualifying owner
  response, six hours without hearing a repeater, and six hours since the last
  recovery flood.
- Hearing a repeater starts another six-hour wait. It does not clear the
  four-day outage limit.
- At most one automatic recovery flood is reserved per six hours. It does not
  schedule additional flood retries.
- After four days without qualifying owner success, automatic floods
  stop. Known direct paths can still be tried. An authenticated matching direct
  owner response can clear the lockout. A bare delivery ACK cannot do this.

The cutoff and flood checkpoint survive sleep and restart. Invalid or backward
clock changes prevent fresh flood eligibility. A forward GPS or manual clock
correction starts another conservative six-hour quiet wait; it does not reset
the outage or the flood checkpoint. Repeater observations and
unrelated public traffic cannot unlock a stopped tracker. Ordinary Room
responses, login traffic, and manual messaging keep their existing routing.

## Encrypted check format

This uses the existing encrypted peer `PAYLOAD_TYPE_REQ` and
`PAYLOAD_TYPE_RESPONSE` datagrams. Request subtype `0x0C` is inside the
encrypted body; it is separate from the packet header's OTA payload type.
All multi-byte fields are little-endian.

| Request field | Bytes | Meaning |
| --- | --- | --- |
| Tag | 4 | RTC-based request identity, durably increasing across reboot. |
| Subtype | 1 | `0x0C`. |
| Version | 1 | `1`. |
| Flags | 1 | `1` for fresh GPS, `2` for failed acquisition, or `0` for no position; other flags are rejected. |
| Latitude | 4 | Signed degrees times 10,000,000. |
| Longitude | 4 | Signed degrees times 10,000,000. |
| Battery | 2 | Millivolts; carried for reporting, not a lost-status decision. |

The request has 17 logical plaintext bytes and zero padding to 32 bytes.
Latitude and longitude must be zero when no fresh position is present.

| Response field | Bytes | Meaning |
| --- | --- | --- |
| Tag | 4 | Exact request tag. |
| Subtype | 1 | `0x0C`. |
| Version | 1 | `1`. |
| Status | 1 | `0` Unknown, `1` Safe, `2` Lost. |
| Awake lease | 2 | Seconds; the Companion requests 120 for Lost and zero otherwise. |

The response has nine logical plaintext bytes and zero padding to 16 bytes.
The client authenticates the configured owner's complete public key, requires
the pending tag within its receive window, and caps an awake lease at 300
seconds. Short, oversized, nonzero-padded and unknown-version bodies are
rejected. An encrypted returned-path packet may bundle the same response and
provide a route for subsequent direct checks.

## Remaining work

This mode implements GPS checks and an owner's saved lost answer. It does not
provide Bluetooth pings every 30 seconds, an automatic Room mailbox poller,
public-board subscriptions, or a mailbox command interpreter. Room private
mailboxes and the public board remain separate features. Mail text is not
executed as a tracker or console command.

Battery life and reliable wake behavior still need measurements on the actual
hardware, including GPS reception, repeater availability, and the chosen
reporting intervals.
