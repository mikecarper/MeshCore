<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/meshtext/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Meshtext: Meshtastic channel text

Meshtext lets a compatible Full Companion exchange channel text with
Meshtastic devices, while still using MeshCore. A repeater or room server can
send scheduled Meshtext beacons on supported builds, but cannot receive,
relay, or host Meshtext chat. Meshtext and MeshCore are separate networks;
this does not bridge messages between them.

## Easy setup: rooftop and handheld

This example uses the public US LongFast channel. Both devices need a
supported **Full Companion** firmware build and a radio that supports
LongFast. Set up the rooftop device before installing it. A device running
Repeater firmware cannot repeat Meshtext; Full Companion firmware does not
make the rooftop device a MeshCore repeater.

For each device, connect the MeshCore app to that device, then open
**Settings > Command Line** to enter the commands. Add channels in the app's
channel editor. Connect to one device at a time.

### 1. Rooftop device

1. Connect the app to the rooftop device. In its Command Line, enter:

   ```text
   set mt.region us
   ```

2. Add the channel `#mt-longfast` in the app. Return to Command Line and enter:

   ```text
   set mt.rebroadcast on
   get mt.status
   ```

3. Check that the status says `active` and `rebroadcast=on`. Leave the device
   powered on; the setting is saved even when the app disconnects.

### 2. Handheld device

1. Connect the app to the handheld. In its Command Line, enter:

   ```text
   set mt.region us
   ```

2. Add the same channel: `#mt-longfast`. Return to Command Line and enter:

   ```text
   get mt.status
   get mt.hops
   ```

3. Check that the status says `active` and hops is greater than zero. If hops
   is zero, enter `set mt.hops 7`. You do not need to enable rebroadcasting on
   the handheld to send messages.

For another region or channel, select the matching region and preset on both
devices and on the Meshtastic radios you want to reach. See the
[supported regions and presets](radio_profiles.md#meshtext-channel-chat).
If the channel cannot be added or status is not active, check the status
message and any existing radio2 profile. If radio2 was manually turned off,
`set radio2 auto` enables automatic setup and clears a saved manual profile.

### 3. Test

Send a message on `#mt-longfast` from the handheld. On the rooftop device,
compare `get mt.status` RX and TX counters before and after the message.
Other Meshtext traffic also changes these counters, so test on a quiet channel
if possible. `get mt.retry` on the handheld can show a heard repeat, but does
not identify which device repeated it. A separate receiver or packet monitor
can give stronger evidence.

Only decoded channel text with hops remaining is eligible; DMs, GPS and
NodeInfo are not repeated. The rooftop device may miss a message while tuned
to MeshCore, or skip its repeat after hearing another device forward it.

## Companion: send and receive messages

Use a **Full Companion** build on a supported ESP32 or nRF52 device. The
radio must also support the selected preset. Meshtext is not included in all
older or smaller builds; for example, an LLCC68 radio cannot use LongFast.

1. Open the [Companion's local CLI](companion_cli.md) in a compatible app or
   terminal and select the region used by the Meshtastic devices. For example,
   in the US:

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

   `get mt.status` must report that Meshtext is active. If radio2 was manually
   turned off, use `set radio2 auto` and check again after two seconds. This
   clears any saved manual radio2 profile. If another feature owns radio2,
   resolve that conflict first. The status message explains why it cannot start.

4. Open the new channel in the app and send a message. The Meshtastic devices
   must use the same region, preset, frequency slot, channel name, and key.
   A queued message does not guarantee delivery.

For private channels or other frequency slots, see the
[full Meshtext reference](radio_profiles.md#meshtext-channel-chat).

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
