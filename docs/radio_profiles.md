# Two LoRa profiles on one radio

`radio2` lets one LoRa transceiver alternate between two frequencies or modulation
settings. It is useful for listening to two networks, or keeping your usual
channel available during a LoRa firmware update.

There are **at most two active profiles**. `tempradio` temporarily replaces
`radio`; `tempradio2` temporarily replaces `radio2`. These are time-shared receive
windows, not simultaneous receivers. Receiving or transmitting a packet on one
profile makes the other unavailable until that packet finishes.

The second-profile commands are shared by repeater, Companion, room server,
sensor and standalone terminal-chat firmware with a LoRa radio. ESP-NOW-only
devices reject LoRa profile commands. KISS is a raw host-controlled modem and
does not expose these Mesh CLI commands or Mesh retry queues.

## Quick setup

Keep the primary channel configured with `radio`, then add the second:

```text
set radio2 910.5,500,8,5,rx
get radio2
get radio2.status
```

The tuple is **frequency in MHz, bandwidth in kHz, spreading factor, coding-rate
denominator, mode, optional preamble in symbols**. `5` means coding rate 4/5.

| Mode | Receive on profile 2 | Transmit on profile 2 |
| --- | --- | --- |
| `rx` | Yes | No |
| `rxtx` (also `rx&tx`) | Yes | Yes |
| `off` | No | No |

For transmission on both permanent profiles:

```text
set radio2 910.5,500,8,5,rxtx
```

The same encoded message is queued separately for each allowed transmit profile.
Each has its own direct/flood retry slots and channel-busy backoff. A forwarding
echo on one profile cannot cancel the other profile's retry. Packet storage and
the physical transceiver's airtime budget are shared; exhausted queues can still
drop packets.

## Keep the main channel during an update

```text
set tempradio2 910.5,500,8,5,rxtx,120
get tempradio2
```

This enables the second profile for 120 minutes (2 hours). Locally generated
LoRa OTA packets use the temporary second profile. Ordinary local traffic stays
on the primary profile by default. Use an OTA-capable build and the usual OTA
setup on every participating node; a temporary profile does not add OTA support
to firmware built without it.

New settings take effect after a short reply allowance. Temporary periods and
schedules live in RAM and disappear on reboot. Timer expiry also uses monotonic
time, so setting the clock backwards cannot extend a temporary session.

## Companion messages on both profiles

To send ordinary Companion messages on both `radio` and an active `tempradio2`,
configure the temporary profile in `rxtx` mode and enable crossing:

```text
set radio2.cross on
get tempradio2
get radio2.cross
get radio2.status
```

`rxtx` permits transmission on the second profile; `radio2.cross on` copies new
messages between the two profiles. `TX=a,b` reports primary and secondary
transmit counts. Crossing also applies to OTA traffic.

The crossing setting is saved and remains enabled after the temporary session
ends or the node reboots. Run `set radio2.cross auto` to restore the default
isolation between permanent and temporary profiles. The firmware picker's
Companion tempradio2 instructions include both choices and the status commands.

## Choose whether traffic crosses between profiles

```text
get radio2.cross
set radio2.cross auto
set radio2.cross on
set radio2.cross off
```

| Active primary | Active secondary | `auto` (default) | `on` | `off` |
| --- | --- | --- | --- | --- |
| `radio` | `radio2` | Cross | Cross | Isolated |
| `tempradio` | `tempradio2` | Cross | Cross | Isolated |
| `radio` | `tempradio2` | Isolated | Cross | Isolated |
| `tempradio` | `radio2` | Isolated | Cross | Isolated |

Crossing always respects `rx`: nothing transmits on an RX-only second profile.
With crossing allowed, a packet received there may still be forwarded on the
primary profile. Existing routing, forwarding and packet-filter settings apply.
An RX-only `tempradio2` also blocks locally generated OTA transmissions under
`auto` or `off`; they do not fall back to the normal primary channel. Use `rxtx`
for an update session that needs to exchange requests and data.

## Preamble

Append the preamble to the end of a command:

```text
set radio2 910.5,500,8,5,rxtx,88
set tempradio2 910.5,500,7,5,rxtx,120,128
set radio 909.5,62.5,7,5,32
set tempradio 909.5,62.5,7,5,120,32
```

Existing primary `radio`, `tempradio`, `radioat` and `tempradioat` commands accept
the optional trailing preamble where those primary commands are supported by
the role. Getters include the preamble. Omit it, or use `auto`/`0`, to calculate
it automatically. Explicit values are used as entered, subject to the radio's
limits and safe airtime arithmetic.

Automatic dual-profile preambles round **up to a multiple of eight**, with a
32-symbol minimum. Each idle scan starts with **4.8 symbols on the slower
profile**, then spends the remaining safe time on the faster profile. Slower
means a longer LoRa symbol (`2^SF / bandwidth`), regardless of profile number.
The slower preamble sets the return deadline. The calculation allows two slow
visits within that preamble, reserving 6 ms per switch and 4 ms of main-loop
margin. Fast-channel transmit preambles include sixteen acquisition symbols.
For a primary SF7 / 62.5 kHz profile:

| Secondary profile | Automatic secondary preamble | Tested explicit preamble | Primary preamble |
| --- | ---: | ---: | ---: |
| SF9 / 500 kHz | 48 | 32 | 32 |
| SF8 / 500 kHz | 88 | 64 | 32 |
| SF7 / 500 kHz | 120 | 80 | 32 |

With preamble 32 on that slower profile, its visit is 9.831 ms and the faster
visit is 6.937 ms, plus the actual switching time. Increasing the slow preamble
also increases the time available on the fast channel. If the symbols are equal
in length, the primary profile goes first. Detected packets hold the current
channel until reception finishes.

The SF8 / 500 kHz value also retains the measured 88-symbol floor from the
production V4/XIAO tests, where 72 and 80 each missed a packet.
The shorter explicit values come from the
[production validation](radio_profiles_validation.md): the final fast-channel
attempts each received 50/50 packets at 4.8 slow symbols. Automatic values retain
the additional switching and loop-jitter margin.
The automatic fast preamble covers the blind interval during the slow visit,
both switches and the acquisition margin. These settings do not guarantee
reception during overlapping packets or long pauses in the firmware loop.
**Transmitters on each channel also need a long enough preamble.** Changing the
receiver's setting does not lengthen packets sent by other nodes.

## Schedule the second profile

Use UTC Unix timestamps (seconds):

```text
set radioat2 910.5,500,8,5,rxtx,START[,PREAMBLE]
set tempradioat2 910.5,500,8,5,rxtx,START,END[,PREAMBLE]
get radioat2
get radioat2 1
get tempradioat2
get tempradioat2 1
del radioat2 1
del tempradioat2 all
```

Replace `START`, `END` and the optional `PREAMBLE` with numbers; omit the square
brackets. Each schedule family has four slots. Times must be in the future and
within 24 days. Overlapping temporary second-profile sessions are rejected.
`radioat2` changes the saved second profile when it starts; `tempradioat2`
restores the saved profile at its end. Pending schedules do not survive reboot.

## Return to one profile

```text
set radio2 off
```

This saves the disabled state and cancels all second-profile temporary activity
and schedules.

```text
set tempradio2 off
```

This ends temporary second-profile activity, cancels its temporary schedules and
restores saved `radio2`. If the saved second profile is off, this returns to one
profile. Packets/retries bound to an expired or changed session are discarded.

## Power saving and diagnostics

Dual mode suspends RX power saving and MCU idle sleep so receive visits can run
promptly. The saved RX power-saving setting returns when profile 2 is off.
The scan uses **normal receive**, following the
[CAD/RX measurements](cad_scan_validation.md): 4.8 symbols on the slower
channel, followed by the remaining fast-channel visit. It does not use CAD for scanning.
The separate CAD check before an initial transmission still follows `cad`.

Single-channel noise-floor calibration and RSSI interference comparison are
suspended while scanning. Retries use the radio's preamble/header activity
indicators. LR2021 extra-SF side detectors are suspended and restored as well.

`get radio2.status` reports the active second-profile mode, MeshCore RX/TX counts
for each profile, switch count, switch failures, longest measured switch and
both effective preambles. A long switch time or rising error count indicates
that the chosen settings need investigation on that board.

`get radio2.scan` shows which profile goes first, the two idle receive-window
lengths in microseconds, and both effective preambles. It reports `off` in
single-profile mode.
