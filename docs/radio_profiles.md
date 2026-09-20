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
devices reject LoRa profile commands. KISS is a raw host-controlled modem: it
exposes equivalent RAM-only binary `SetRadio2`/`SetTempRadio2` commands and
logical KISS Data ports, documented in [the KISS modem protocol](kiss_modem_protocol.md).
It does not use the Mesh CLI's persistent profile file or Mesh retry queues.

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
| `rx` | Yes | Only infrastructure replies explicitly configured with `tx.reply ... force` |
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
LoRa OTA requests use the temporary second profile. Infrastructure OTA responses
follow `tx.reply` below (both TX-capable profiles by default). Ordinary local traffic stays
on the primary profile by default. Use an OTA-capable build and the usual OTA
setup on every participating node; a temporary profile does not add OTA support
to firmware built without it.

Local settings take effect after a short reply allowance. On repeaters, immediate
remote `radio2`/`tempradio2` changes (including `off`) wait until their exact reply
copies finish, with at least one successfully transmitted. A reply that cannot
be queued or whose copies all fail cancels the change. Queued replies have a
five-minute deadline; an already-transmitting copy may finish under the normal
radio watchdog. This confirms transmission, not reception by the remote client.

Permanent remote changes are prepared in an uncommitted file before replying;
the saved configuration is replaced only after reply transmission. If that
replacement fails, the old channel remains active and the commit retries.
Local `get radio2.status` reports this pending state. Roles without the tracked remote
reply path require local USB for these immediate secondary-profile changes.

Temporary periods and schedules live in RAM and disappear on reboot. Waiting
for a reply does not extend a temporary lease. Timer expiry also uses monotonic
time, so setting the clock backwards cannot extend a temporary session.

## Repeater, room-server, and sensor replies

These roles default to **`tx.reply both`**: locally generated replies are queued
on `radio` and on an active `radio2` that permits TX. This includes replies to
remote CLI/login/telemetry requests, ACKs, returned paths, delayed GPIO command
completion, room subscription posts, and locally served LoRa OTA catalog,
manifest, data, proof, and leaves responses. Each copy has its own retry state.
Forwarded packets, periodic adverts, sensor pushes, and OTA requests continue
to use the normal crossing rules.

```text
get tx.reply
set tx.reply both
set tx.reply both force
set tx.reply radio
set tx.reply auto
```

| Choice | Replies transmit on |
| --- | --- |
| `both` | Primary plus an active `rxtx` second profile. Default. |
| `radio` | Primary only. |
| `radio2` | Second only; no primary fallback. |
| `auto` | Normal `radio2.cross` behavior, including permanent/temporary isolation. |
| `off` | Neither profile. This also suppresses remote CLI replies and ACKs. |

Append **`force`** to `both` or `radio2` to allow these replies on an active
second profile configured as `rx`. Without `force`, `rx` remains receive-only.
Sending the command again without `force` removes the override. It never turns
on `radio2 off`, changes the saved `rx` mode, or enables ordinary forwarding/TX
there. Existing crossover packet filters still apply to the extra copy.

The explicit choices override `radio2.cross`, including `off` and mixed
permanent/temporary profiles. `radio` means the active primary profile,
including `tempradio`; `radio2` also includes an active `tempradio2`.
For example, an OTA source can retain its normal channel while allowing OTA
and management responses on a temporary receive profile:

```text
set tempradio2 910.5,500,8,5,rx,120
set tx.reply both force
```

The source must have OTA support and an active temporary-radio session as
usual. A node fetching an update still needs `rxtx` for its OTA requests.
Forcing replies does not change OTA's receive gates, scan timing, or preambles.

This setting survives reboot; older saved profiles adopt `both` without force.
The getter reports when the chosen second profile cannot transmit. Already
queued replies retain their choice; disabling/changing a profile invalidates
stale queued packets. A parameterized repeater `tempradio` command waits for
both admitted reply copies to drain before changing the primary settings;
at least one must transmit successfully. Those tracked replies omit alternate
paths and transport retries so no late success copy can outlive the handoff.
Queue capacity and the shared transceiver's airtime budget still limit delivery.
OTA data/proof admission counts both copies against its queue credit and
reserves receive capacity before allowing the response.

Companions use the per-contact/channel choices below; `tx.reply` is an
infrastructure setting.

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

## Companion TX routing by contact or channel

Companions can save a transmit choice for each contact and each configured
channel. The commands apply to messages sent from the phone app, USB/BLE/TCP
Companion clients, and the text terminal:

```text
set tx.user "Alice Jones" radio2
get tx.user "Alice Jones"
set tx.channel 0 both
get tx.channel 0
set tx.channel #wardriving radio
get tx.channel #wardriving
```

Channel indices match the app and the terminal's `channels` list; Public is
normally slot `0`. Names must match exactly, including capitalization and any
leading `#`. Quotes always select a literal name, so `"1"` is a channel named
`1`, while unquoted `1` selects slot 1. Quote contact names beginning with
`key:` to distinguish them from public-key selectors. Duplicate names are
rejected: use the channel index or a contact's public key instead.

| Choice | Locally addressed traffic transmits on |
| --- | --- |
| `auto` | Existing behavior, including `radio2.cross` and temporary-profile isolation. This is the default. |
| `radio` | The primary profile only. |
| `radio2` | The second profile only; sending fails if it is off or RX-only. |
| `both` | The primary profile and the second profile whenever the second is enabled for TX. |
| `off` | Neither profile for this contact/channel. Reception stays enabled. |

Explicit choices override `radio2.cross`, including `off` and the isolation
between a permanent and temporary profile. They do not turn on radio2 or
change its `rx`/`rxtx` mode. For example, leave `radio2.cross auto` and use
`set tx.channel 0 both` to send Public messages on both `radio` and an active
`tempradio2`, while other channels keep their default routing.

`radio` means the currently active primary profile, whether permanent or
`tempradio`; `radio2` similarly includes `tempradio2`. There are still at most
two active profiles. Getters show the saved choice and `active=` for newly
initiated messages with the current profile configuration. A temporarily
unavailable second profile is reported explicitly.

Contact selection uses the full stored public key after resolving a unique
name or key prefix. It never routes by the short on-air destination hash.
For key selection, prefix 12–64 hexadecimal digits (whole bytes) with `key:`:

```text
set tx.user key:0123456789AB radio2
get tx.user key:0123456789AB
```

Replace the example prefix with your contact's key; use more digits if it is
ambiguous. `get tx.user` and `get tx.channel` report override counts and query
syntax. To return a target to the global behavior:

```text
set tx.user "Alice Jones" auto
set tx.channel 0 auto
```

A user's setting covers addressed texts, login/CLI/telemetry requests, replies,
ACKs, and returned paths to that contact. Thus `off` also prevents ACKs to
that contact. Channel settings cover group text and group data, including
an explicitly routed group datagram. Contact choices do not filter other
people's messages inside a group channel. Adverts, raw traces, raw packets,
and OTA packets without a contact/channel destination retain global routing.
Packets and their retries already queued retain the choice used when created;
changing or disabling the radio profile still invalidates stale queued work.
Message timeout estimates use the selected profiles' airtime, independently
of the scanner's current receive profile.

Choices survive reboot and normal phone-app updates to the same contact or
channel key. Replacing a channel's key or deleting/replacing a contact clears
its choice. They use reserved bytes in existing saved records, so record
sizes and the phone protocol are unchanged. Older firmware ignores these
bytes and may reset the choices when it saves the records.

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

These are the global rules used by Companion targets set to `auto`; an explicit
contact/channel choice above overrides crossing for its own outbound packets.
Ordinary crossing respects `rx`. The infrastructure-only `tx.reply ... force`
override above permits locally generated replies on that profile.
With crossing allowed, a packet received there may still be forwarded on the
primary profile. Existing routing, forwarding and packet-filter settings apply.
An RX-only `tempradio2` also blocks locally generated OTA requests under
`auto` or `off`; they do not fall back to the normal primary channel. Use `rxtx`
for a fetching node that needs to exchange requests and data. Infrastructure
OTA responses follow `tx.reply` independently of this crossing table.

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
32-symbol minimum. Each idle scan starts with **4.6 symbols on the slower
profile**, then spends the remaining budgeted time on the faster profile,
also with a **4.6-symbol floor**. Slower
means a longer LoRa symbol (`2^SF / bandwidth`), regardless of profile number.
The slower preamble sets the return deadline. The calculation allows two slow
visits within that preamble, reserving 0.6 ms per switch and 0.3 ms of main-loop
margin. Fast-channel transmit preambles include sixteen acquisition symbols.
For a primary SF7 / 62.5 kHz profile:

| Secondary profile | Automatic secondary preamble | Previously tested explicit preamble | Primary preamble |
| --- | ---: | ---: | ---: |
| SF9 / 500 kHz | 32 | 32 | 32 |
| SF8 / 500 kHz | 88 | 64 | 32 |
| SF7 / 500 kHz | 64 | 80 | 32 |

With preamble 32 on that slower profile, its visit is 9.421 ms and the faster
visit is 21.847 ms, plus the actual switching time. Increasing the slow preamble
also increases the time available on the fast channel. If the symbols are equal
in length, the primary profile goes first. Detected packets hold the current
channel until reception finishes.

The SF8 / 500 kHz value also retains the measured 88-symbol floor from the
production V4/XIAO tests, where 72 and 80 each missed a packet.
The explicit values come from the
[production validation](radio_profiles_validation.md): the final fast-channel
attempts each received 50/50 packets at 4.8 slow symbols. Automatic values use
the switching and loop-jitter allowances below.
Those explicit-preamble tests used the older 4.8-symbol policy; they do not
validate the new 4.6-symbol slow dwell / 0.3 ms reserve. The
[4.6/0.3 ms two-profile bench](radio_dwell_policy_validation.md#hardware-context) received 197/200
at SF7/62.5 + SF8/500 with explicit 32-symbol preambles. The newer four-channel
5.1/6.1/7.7 tests also had misses, so this is a timing policy, not a claim of zero packet loss.
The automatic fast preamble covers the blind interval during the slow visit,
both switches and the acquisition margin. These settings do not guarantee
reception during overlapping packets or long pauses in the firmware loop.
**Transmitters on each channel also need a long enough preamble.** Changing the
receiver's setting does not lengthen packets sent by other nodes.

### Chirp calculator and warnings

`get radio2.timing` (alias `get radio.timing`) reports the active pair in
`radio,radio2` order: dwell in chirps, recommended preambles in symbols, and
the switching/loop allowances. For SF7/62.5 on `radio` and SF7/500 on `radio2`:

```text
> chirps=4.60,85.34; need=32,64; switch=600us; loop=300us (estimate); WARN recommended preamble: radio2=64
```

`WARN recommended preamble` identifies **each** profile whose recommendation
exceeds the standard 32 symbols or its explicitly selected value. For example,
with SF7/62.5 on the primary profile, `set radio2 910.5,500,7,5,rx,32` replies:

```text
OK - radio2 rx; preamble=32; WARN recommended preamble: radio2=64; short override: radio2
```

Successful `set radio ...` replies also include the recommended preamble for
each affected profile, calculated with the requested settings. A `short override`
note identifies an explicit value below the estimate; it is not silently
rewritten. Warnings are also included in profile settings/getters, scan info,
temporary settings and schedule acknowledgments. `off` disables the dual-profile
warning. A timing readout does not change settings or transmit a packet.

The shared `RadioProfiles::calculateChirpTiming()` function accepts both tuples,
optional visit times in microseconds, a per-switch allowance and loop margin.
Using microseconds throughout:

```text
T[i] = 2^SF[i] * 1000 / BW_kHz[i]
slow = profile with the longer T (radio wins ties)
L[slow] = max(requested_visit_us[slow], ceil(4.6 * T[slow]))
L[fast] = max(requested_visit_us[fast], ceil(4.6 * T[fast]))
O    = 2 * switch_us + loop_margin_us
C    = L[radio] + L[radio2] + O

P[slow] = round_up_to_8(max(32, 2 * C / T[slow]))
P[fast] = round_up_to_8(max(32, (L[slow] + O) / T[fast] + 16))
```

The previous SF8/500 versus SF7/62.5 measured 88-symbol floor remains in force.
Automatic selection uses the minimum visits to choose a preamble, then allocates
the fast visit from half the slow preamble's duration. Diagnostics feed the
actual allocated visits back into the same function, including longer visits
caused by an explicit slow preamble. Too-short slow preambles that cannot fit
both minimum visits remain rejected by the existing configuration validation.

The automatic scheduler uses these same role-specific minima and computes:

```text
slow_dwell = ceil(4.6 * T[slow])
fast_dwell = max(ceil(4.6 * T[fast]),
                 floor(P_configured[slow] * T[slow] / 2 - slow_dwell - O))
```

For the tested SF7/62.5 + SF8/500 pair with slow preamble 32, this yields
9,421 us slow and 21,847 us fast (42.67 fast chirps). Swapping `radio` and
`radio2` swaps the allocations automatically. The 0.3 ms reserve is not a sleep.

The calculator accepts board-specific allowances, but production scheduling
uses the requested **600 us per switch plus 300 us per cycle**. This is a
nominal fast-switch allowance, not a worst-case bound. Diagnostics use the
larger of 600 us and the observed maximum
switch time; an overrun increases the warning estimate without automatically
changing wire preambles or saved configuration. Recent V4 tests peaked at
836 us (827 us in the 4.6/0.3 ms run), and Indicator reached 8428 us:
both can exceed the nominal allowance.
The model preserves two slow-profile return opportunities and the existing
16-symbol fast-profile acquisition allowance. These are conservative policies,
not a fitted success curve, and neither packet overlap nor arbitrary application
stalls can be made safe by a finite preamble recommendation.

See the [implementation and validation notes](radio_dwell_policy_validation.md).

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
[CAD/RX measurements](cad_scan_validation.md), with the updated 4.6-symbol dwell on the slower
channel, followed by the remaining fast-channel visit. It does not use CAD for scanning.
The separate CAD check before an initial transmission still follows `cad`.

SX1262 dual-profile mode keeps the oscillator running in XOSC standby between
visits, so each retune does not need to restart the TCXO. It restores the prior
standby policy when returning to one profile, together with the saved RXPS
setting. Radio reinitialization temporarily uses RC standby until the TCXO has
been configured again. Other radio families retain their existing behavior.
This does not disable the TCXO supply or its initial startup delay, and it is
not conditional on 500 kHz bandwidth. Keeping the oscillator on uses additional
power during the short standby intervals.

The requested 0.6 ms switch budget is a nominal scheduling allowance, not a
forced settling delay or a measured worst-case bound. The earlier standalone
[keep-warm scan experiment](mixed_scan_validation.md) averaged about 1.19 ms
of overhead per hop (scan-cycle time minus both receive windows, divided by
two). That experiment used a leaner receive path; it is not a measurement or
guarantee of the production implementation. Measure the target board before
shortening its timing margins or transmit preambles.

The [production-path XIAO timing test](radio_profile_switch_validation.md)
measured **1.434 ms average / 1.467 ms maximum** with warm standby, compared
with **3.166 ms average** using RC standby: about **1.732 ms saved per hop**.
It covered 12,000 retunes across SF7/8/9 at 500 kHz paired with SF7/62.5 kHz.
These measurements include observing BUSY low, but exclude application/UI
scheduling and are not a packet-delivery qualification or a guarantee for
other boards, especially those using I/O expanders.

A same-image follow-up batching SF/BW/CR into one modulation command reduced
warm switching from **1.444 ms to 1.158 ms average** (batched maximum **1.198 ms**),
saving another **0.286 ms / 19.8%**. RadioLib's modulation caches and LDRO
calculation remain synchronized; frequency, preamble, packet guards, and RX
restart are unchanged. A later [HIL-only screening](radio_profile_switch_validation.md#expanded-screening-redundant-rx-setup-and-spi-v5)
tested redundant standby/IRQ/buffer/packet setup and SPI transfer changes.
Its fastest candidate measured 0.535 ms mean on XIAO but 8.203 ms on the
Indicator, whose radio GPIOs go through an I2C expander. The validated production
implementation now opts XIAO S3 WIO and Indicator LoRa into buffered 8 MHz SPI
and a guarded fast-RX state machine. The `heltec_v4_kiss_modem` target uses the
same guarded path only for KISS dual-profile scanning; its normal V4 roles keep
their established HAL/timing policy. That host-powered KISS target uses the
existing V4 160 MHz role override, but still needs an exact-image hardware
timing check before its nominal 600 µs target is claimed. It skips redundant
standby, IRQ mapping,
buffer-base writes and modem queries only during an owned continuous RX-to-RX
retune. Preamble/packet parameters (including the IQ workaround), stale-IRQ
clearing, RX commands and BUSY waits remain. TX, CAD, sleep, reset, RXPS and
failed commands revoke reuse; failed RX startup rolls back the profile.
Sleep entry uses standby and wake restores the existing TCXO voltage/delay.
Other variants do not opt in to faster SPI or the fast-RX state machine.
Within a valid owned fast-RX retune, identical acknowledged SF/BW/CR/LDRO
settings now reuse the previous modulation command: frequency-only hops omit
`SetModulationParams` (0x8B). A changed effective LDRO value also forces a write,
including when automatic LDRO policy changes. Ordinary setters and lost RX
context invalidate that acknowledgement; initial setup, reset, sleep, failed
commands and full RX setup conservatively reapply the tuple. Frequency,
preamble/packet setup, IRQ clearing and BUSY checks are unchanged. This follow-up
has [same-image hardware measurements](separated_radio_modulation_cache_validation.md):
frequency-only hops averaged 0.452 ms on XIAO and 7.601 ms on Indicator.
The mixed-bandwidth timing figures above predate it. Four-channel SF6 scans
still missed packets; faster switching is not proof of loss-free acquisition.
See [production and USB validation](radio_profile_switch_validation.md#production-integration-and-usb-recovery-v8)
for current measurements, lifecycle tests and limitations.
The Indicator result also shows that the
nominal 0.6 ms allowance is not an upper bound for every board; its scheduling
and preambles need board-specific qualification. Measured overruns raise the
recommended preambles shown by the CLI without rewriting configured values.

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
