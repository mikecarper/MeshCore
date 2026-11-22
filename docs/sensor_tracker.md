<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/sensor_tracker/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Sensor pet tracker

Pet tracker mode is an optional mode in the Sensor firmware. It acquires a new
GPS position, sends an encrypted check to one configured owner's Companion,
and waits for that owner's saved lost status. The owner's answer controls the
tracker's reporting rate and awake window.

Using the combined RAK WisMesh Tag firmware? Jump to
[Find your RAK tag from a phone](#find-your-rak-tag-from-a-phone) for Apple,
Google and Sidewalk setup and maps.

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

Use `set lost.reply yes` when the pet is lost. Use `set lost.reply off` to
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
set tracker.mode pet
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
| `set tracker.mode pet` | Enable automatic checks and tracker power control. |
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

## RAK WisMesh Tag Companion as a collar

The RAK WisMesh Tag USB and BLE Companion builds also accept the local tracker
commands above. Save the owner's Companion as a contact on the Tag before
enabling pet mode. The Tag retains its normal Companion receive and control
interfaces between checks. Use `tracker check` to request a check immediately
and `get tracker status` to see the last accepted owner response.

Two additional experimental RAK images run the tracker with Nordic's native
Apple Find My accessory service. Apple-issued provisioning is required for
iPhone enrollment. The build and bench instructions cover both tracker roles.
[RAK Find My tracker instructions](https://github.com/mikecarper/MeshCore/tree/keymindCascade/examples/rak_findmy)

Experimental RAK Sidewalk and Google Find Hub images are also available in both
tracker roles. The Sidewalk profile adds gateway location to the collar's LoRa
GPS checks. The Google profile supports Android item-finding tests. Each
individual image enables one commercial network and stores its own configuration.
[Build and test the RAK location profiles](https://github.com/mikecarper/MeshCore/tree/keymindCascade/examples/rak_location)

## Find your RAK tag from a phone

Use the **combined Sensor** or **USB tracker Companion** firmware. Both select
A (Apple), B (Google) and C (Sidewalk) on first boot. On the tag’s USB console,
run `pings status` to see which are enabled. To enable one, for example C, run
`pings on C` and restart the tag. Each network needs its own setup below.
[Combined firmware and device provisioning](https://github.com/mikecarper/MeshCore/tree/keymindCascade/examples/rak_location/native_pings#prepare-a-tag-for-live-tests)

The default ping interval is 10 seconds. To change it, enter `pings interval 30`
and restart the tag. The range is 1–300 seconds. `finder ping` shows local
Bluetooth activity. The **time on the map** tells you when the network last
located the tag, which can be much less frequent.

<a id="motion-gps-and-box-alerts"></a>

### Motion GPS and geofence alerts

The combined RAK Sensor and USB tracker Companion can wake GPS when the
collar moves and send you a private MeshCore message when it leaves or
returns to a circle, box or polygon. Both features start off. The collar uses its
own GPS. The owner's Companion receives alerts over direct LoRa, independently
of A, B and C. On an iPhone, open the collar's conversation in the MeshCore app
connected to your owner Companion.

1. On the collar's USB console, enter `pet radio <Hz> <bandwidth-kHz> <SF> <CR>`
   using the owner's radio settings. Enter `pet owner <owner-public-key>` with
   the full 64-character key. Outdoors, run `pet advert` and save the collar
   as a contact on the owner Companion. If the receiver is another combined
   USB tracker Companion, enter `pet watch <collar-public-key>` on that receiver.
2. On the collar, enter one of the commands below with your own coordinates,
   then enter `pet on`. Each point is **latitude longitude** in decimal degrees.
   Separate the numbers with spaces. A saved fence enables motion-triggered GPS.

    | Shape | What to enter | Example |
    | --- | --- | --- |
    | Circle | Center point, then a positive whole-number radius in meters | `pet fence 47.610 -122.210 100` |
    | Box | Two opposite corners, in either order | `pet fence 47.610 -122.210 47.612 -122.206` |
    | Polygon | 3–9 points around the perimeter, clockwise or counterclockwise | `pet fence 47.610 -122.210 47.612 -122.210 47.611 -122.206` |

    The collar closes the polygon automatically. Enter each corner once and
    keep the edges from crossing. Local polygons can cross the date line and
    must span less than 180° of longitude. A box uses the shorter longitude span.
    Coordinates on the boundary count as inside.

3. Start inside with a clear sky view. Run `pet fence` until it shows
   `side=inside`. The first two fresh fixes establish a baseline silently.
   Walk well past an edge and back, allowing two fresh fixes on each side.
   The owner receives **Collar left the fence** and **Collar returned to the fence**,
   each with the crossing coordinates. On a USB tracker Companion receiver,
   run `pet last` to read the most recent alert and its UTC timestamp.

Run `pet fence` to display the saved shape and points, including the radius for a circle.

For motion-triggered GPS alone, use `pet motion on` and `pet on`. Run
`pet motion` to see the last fix observed while armed. Scheduled collar checks
continue to report fresh positions. `pet off` pauses tracking and retains
settings. `pet fence off` clears the fence and pending alerts. To stop motion-triggered fixes while keeping
normal collar checks, clear the fence and enter `pet motion off`.

The fence and its last confirmed side survive a restart. The collar retains the
eight most recent undelivered crossings and retries the oldest about once per
minute until the owner's radio acknowledges it. `queued` in `pet fence` shows
the number waiting. Out of radio range, the message can arrive later. Without
a GPS fix, a crossing can be missed. Leave space between a fence edge and places
where the pet normally rests, since GPS readings can drift near the edge.

### A. Apple Find My on iPhone

1. **Prepare the tag.** The combined image needs your Find My product plan
   and this tag’s Apple-issued credentials. Run `tag status` over USB and look
   for `ready=1`. If it reports `plan=absent`, install a build with your plan
   before trying to pair.
2. **Pair once.** With the iPhone near the tag, enter `tag pair` on the USB
   console. On the iPhone, open **Find My → Items → + → Add Other Item**.
   Select the tag, tap **Connect**, name it and finish registration with your
   Apple Account. Confirm `paired=1` in `tag status`.
3. **Check the location.** Open **Find My → Items** and tap the name you gave
   the tag. Read the time shown with its map location. To test a remote
   sighting, check for a newer time after another participating Apple device
   passes the tag outside your iPhone’s Bluetooth range. [Apple’s Find My item guide](https://support.apple.com/guide/iphone/add-a-third-party-item-iph09cb07b40/ios)

### B. Google Find Hub on Android or iPhone

1. **Prepare the tag.** Load the provisioning record for your registered Google
   model. Run `findhub status` on the USB console and look for `Ready`. For
   a development model, use its authorized Google test account. [Google model setup](https://github.com/mikecarper/MeshCore/tree/keymindCascade/examples/rak_location#google-find-hub-item-test)
2. **Pair once on Android.** Turn on Bluetooth and bring the owner’s Android
   phone near the tag. Accept the **Fast Pair** prompt. If the unpaired tag
   shows `pairing_advertising=0`, tap and release its button to make it
   discoverable. After pairing, `findhub status` should show `provisioned=1`.
   [Google’s locator tag setup](https://support.google.com/accounts/answer/3265955?hl=en)
3. **Check the location on Android.** Open **Find Hub** with the account used
   to pair the tag and select it in the device list. Check its map position and
   last-seen time. To test a remote sighting, move the paired phone away and
   check for a newer time after another participating Android device passes.
4. **To check from an iPhone**, the unofficial **Tagpin** viewer requires
   iOS 17 or later and a build from source on a Mac using Xcode. Pair on
   Android first, then sign in to Tagpin with that same Google account, select
   the tag and check the report time on its map. This viewer has yet to be
   verified with the RAK build. [Tagpin install guide](https://github.com/darthkubox/Tagpin#install)

### C. Amazon Sidewalk in iPhone Safari

1. **Enable positioning.** In Safari, sign in to the AWS account and region
   where the tag was registered. Open the Sidewalk devices list and select
   your tag. Under **Edit → Geolocation**, select **Activate positioning**,
   choose the **Position data destination**, and save. The tag needs its own
   AWS manufacturing record. [Open your Sidewalk devices](https://console.aws.amazon.com/iot/home#/wireless/devices?tab=sidewalk) · [AWS positioning setup](https://docs.aws.amazon.com/iot-wireless/latest/developerguide/sidewalk-location-devices.html)
2. **Request a position.** The tag requests positions automatically near a
   compatible gateway with Community Finding enabled. To test now, run
   `sidewalk status` over USB. Look for `mode=location`, `registered=1`,
   `cloud_ble=1` and `gateway_location=1`, then run `sidewalk locate`.
   If disconnected, try `sidewalk connect` near a gateway and check status
   again. `sent` counts uplinks completed by the tag.
3. **Check the map.** In Safari, open the tag’s AWS device page and tap
   **Position**. After the first resolved report, this tab shows the map,
   coordinates and timestamp. Refresh and look for a newer timestamp to
   confirm a sighting. The pin is an estimate based on the gateway that heard
   the tag. Bookmark the device page for next time. [Sidewalk map guide](https://docs.silabs.com/amazon-sidewalk/2.9.2/sidewalk-getting-started/provision-your-device)
4. **If Position is missing or empty**, confirm positioning and the destination
   are saved, then check the tag’s gateway status. A `gateway_location=0`
   means that gateway reported location unavailable. Check again after the tag
   reaches a participating gateway and AWS receives a location report.
