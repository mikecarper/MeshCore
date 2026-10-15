<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/tx_beacons/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Transmit-only text beacons

Up to 12 disabled-by-default slots can send a short text message using their
own frequency, bandwidth, spreading factor, coding rate, power, and preamble.
MeshCore beacons use the Public channel. Meshtext beacons use the public preset
channel, such as LongFast or MediumFast, with its standard public key.

The default cycle is **24 hours**. Choose 12, 24, 36, or 48 hours:

```text
get beacon.interval
set beacon.interval 24
```

Slots are spread evenly across the selected cycle: 1, 2, 3, or 4 hours apart.
Slot 1 is due after the first spacing interval, slot 12 after a full cycle,
then the cycle repeats. Empty slots are not compacted. Scheduling uses uptime,
not UTC; correcting the clock does not compress the spacing. Reboot starts a
fresh schedule, with no immediate startup burst.

## Configuration

```text
set beacon 1 meshcore 910.525,62.5,7,5,14,32 A MeshCore node is listening here
set beacon 2 meshtext us longfast 14 A MeshCore node is also on LongFast
set beacon 3 meshtext us mediumfast 14 A MeshCore node is also on MediumFast
get beacon
get beacon 2
delete beacon 2
delete beacon all
```

MeshCore tuple order is `MHz,kHz,SF,CR,dBm[,preamble]`; CR is 5 through 8,
representing 4/5 through 4/8. The default beacon preamble is 32 symbols.
Meshtext takes an explicit `us`, `eu868`, `eu433`, or `anz` region, a recognized
preset, and dBm. Its frequency is calculated using the stock preset channel;
its preamble is fixed at 16 symbols. Invalid modem, region, power, or text
settings are rejected. Use settings permitted for your hardware and location.

Each message body is limited to 80 bytes and is prefixed with the node name.
Settings are saved separately from ordinary preferences and channels.
Configuration changes do not immediately transmit a beacon.

## Radio ownership and limits

These are ordinary channel text messages, not MeshCore identity advertisements
or Meshtext NodeInfo. Meshtext beacons request no ACK and have no forwarding
budget. MeshCore Public uses one locally transmitted flood; other MeshCore
nodes may relay that flood according to their own policies.

The transmitter briefly borrows the physical radio, sends once, and restores
its normal receive settings, power, and RX power saving. It does not occupy,
replace, or persist changes to `radio2`, including a temporary second profile.
Meshtext beacon identity is separate from Companion Meshtext chat; beacon
packet IDs are committed to storage before use to avoid reusing chat nonces.
Keep manually assigned Meshtext node IDs unique; a manual chat ID can still
collide with another sender, including the separate beacon sender. A missing
or unreadable active beacon ID file stops Meshtext beacons rather than rolling
back to a potentially older backup. MeshCore beacons remain independent.

Beacons share the normal airtime budget and yield to due MeshCore traffic.
They do not interrupt a pending packet, Morse operation, carrier test, or
noise-floor calibration. Busy-channel, preparation, and transmission failures
skip that opportunity; there is no automatic RF retry or catch-up burst. A
separate transmit-start guard permits at most one beacon attempt per hour.
Receive restoration retains exclusive ownership until it is confirmed.
An unavailable/suspended radio does not transmit a beacon.

The feature is available on supported ESP32, nRF52, and RP2040 LoRa builds
where flash and runtime RAM qualification pass. Tiny STM32 images leave it
disabled to preserve existing features and filesystem space. The Meshtext
packet-format adapters cover SX1262 (including the Indicator LoRa variant),
SX1268, LLCC68, SX1276 (including external-PA variants), LR1110, LR1121,
LR2021, and STM32WLx. The STM32WLx adapter is available to explicitly enabled
builds, but does not enable beacons in the space-constrained STM32 artifacts.
An opt-in Wio-E5 Mini USB Companion link exceeded its unchanged application
limit by 19,820 bytes; the three existing STM32 Sensor targets exceeded theirs
by 37,000-42,736 bytes. Enabling those artifacts needs separate size work,
not just a radio capability flag or a smaller filesystem.
ESP-NOW-only boards have no LoRa transmitter and cannot send these packets.

Choose a region and preset the physical chip, board RF path, and antenna
support. In particular, SX1268 requires a compatible band such as EU433, not
US or ANZ. LLCC68 supports MediumFast (SF9/BW250), but not LongFast
(SF11/BW250). Invalid modem combinations are rejected, not silently changed
to a different preset or transmitted as MeshCore. Local RF rules and the
board's power limits still apply.

Protocol entry and restoration use each chip's checked sync-word, CRC,
explicit-header, and normal-IQ configuration. Beacon TX temporarily disables
LR2021 receive side detectors and restores their saved configuration afterward.
Enabling this feature does not enable Companion Meshtext receive/chat support
on other roles.

Schedules are best effort. A busy or sleeping node can miss an opportunity;
the feature does not promise delivery, RF sensitivity, or a particular
retuning duration on unmeasured hardware.
