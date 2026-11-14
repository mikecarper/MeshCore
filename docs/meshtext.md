<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/meshtext/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Meshtext: Meshtastic channel text

Meshtext lets a compatible Full Companion exchange channel text with
Meshtastic devices, while still using MeshCore. A repeater or room server can
send scheduled Meshtext beacons on supported builds, but cannot receive,
relay, or host Meshtext chat. Meshtext and MeshCore are separate networks;
this does not bridge messages between them.

## Companion: send and receive messages

Use a **Full Companion** build on a supported ESP32 or nRF52 device. The
radio must also support the selected preset. Meshtext is not included in all
older or smaller builds; for example, an LLCC68 radio cannot use LongFast.

1. In the Companion's local CLI, select the region used by the Meshtastic
   devices. For example, in the US:

   ```text
   set mt.region us
   ```

   Other supported values are `eu868`, `eu433`, `anz`, and `auto`. Auto can
   infer some regions from the primary frequency; if it is ambiguous, choose
   the correct region explicitly. Choose a preset, band, and transmit power
   permitted for your location and supported by your radio.

2. In the MeshCore app's channel editor, add `#mt-longfast` for the standard
   public LongFast channel, or use another [supported preset](radio_profiles.md#meshtext-channel-chat)
   matching the Meshtastic devices. The preset selects the matching public
   channel name, public key, and radio2 settings automatically when radio2 is
   available for automatic setup.

3. In the Companion CLI, check:

   ```text
   get mt.status
   get radio2
   ```

   `get mt.status` must report that Meshtext is active. If radio2 says
   `off (manual)`, use `set radio2 auto` and check again after two seconds.
   If a manually configured radio2 or another feature owns it, resolve that
   conflict first. The status message explains why it cannot start.

4. Open the new channel in the app and send a message. The Meshtastic devices
   must use the same region, preset, frequency slot, channel name, and key.
   A queued message does not guarantee delivery.

For private channels or other frequency slots, see the
[full Meshtext reference](radio_profiles.md#meshtext-channel-chat). A Companion
can optionally forward decoded channel text on Meshtext with
`set mt.rebroadcast on`; this is off by default. It forwards eligible text
within Meshtext, not between Meshtext and MeshCore.

Radio1 and radio2 share one physical radio and take turns listening. Messages
can be missed on either network while the radio is listening to the other.

## Repeater: scheduled transmit-only beacon

A supported repeater can periodically send a public text beacon to a
Meshtastic channel. It does not receive or repeat Meshtext traffic, and it
does not bridge that traffic into MeshCore.

From its USB CLI or an authorized remote admin session, for example:

```text
set beacon 1 meshtext us longfast 14 A MeshCore repeater is listening here
get beacon 1
get beacon.interval
```

Replace `us`, `longfast`, `14` (transmit power in dBm), and the text for your
region, supported preset, hardware, and local rules. Settings are saved.
The beacon is scheduled, not sent immediately. At the default 24-hour cycle,
slot 1 is due every 24 hours, beginning two hours after reboot.
`delete beacon 1` removes this example.
The feature is available on supported ESP32, nRF52, and RP2040 LoRa builds;
if the command is unavailable, that firmware does not include it.

## Room server: scheduled transmit-only beacon

On a supported room server, use the same beacon command from its CLI:

```text
set beacon 1 meshtext us longfast 14 A MeshCore room is available here
get beacon 1
```

This is an occasional public announcement only. It does not add a Meshtastic
room, message history, Meshtext reception, or forwarding to the room server.
See [transmit-only text beacons](tx_beacons.md) for scheduling, supported
radios, and all commands.
