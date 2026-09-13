# Dual-profile receive validation

These measurements exercise the production two-profile implementation on one
SX1262 transceiver, using a Heltec V4 repeater as receiver and a XIAO ESP32-S3
with a Wio LoRa module as transmitter. They follow the dedicated
[mixed-bandwidth scan experiment](mixed_scan_validation.md).

Counts, diagnostic replies and firmware/source hashes are in
[radio_profiles_results.json](../tools/hil/radio_profiles_results.json).

The primary profile is 909.5 MHz, SF7, 62.5 kHz, CR 4/5, preamble 32.
The secondary is 910.5 MHz, 500 kHz, CR 4/5, with SF9, SF8 or SF7.
Scanning uses normal RX, starts with the slower profile, and reserves two slow
visits per slow preamble. Transmitters must use the corresponding preamble too.

## Method and limits

The transmitter sends MeshCore adverts at varied intervals. The receiver's
per-profile MeshCore RX counters are compared with the transmitter's completed
TX counters before and after each run. Initial OTA announcements can add packets
beyond the manually requested adverts; the table includes those transmissions.
These are aggregate counters, not sequence-matched payload captures.

Runs use one transmitting profile at a time and a strong signal. They do not
measure simultaneous traffic, weak-signal reception, interference or months of
uptime. A single transceiver remains unavailable to the other profile while
receiving or transmitting a packet. A clean finite run is evidence for a timing
choice, not a guarantee that it cannot drop packets.

## Five-symbol baseline

Slow visits were 10.240 ms and fast visits 6.528 ms, plus actual switching time.
The longest observed profile switch was 4.416 ms; no switch failures were reported.

| Fast profile | Fast preamble | Fast RX / TX | Slow RX / TX |
| --- | ---: | ---: | ---: |
| SF9 / 500 | 48 | 54 / 54 | 30 / 30 |
| SF8 / 500 | 72 | 49 / 50 | — |
| SF8 / 500 | 80 | 49 / 50 | — |
| SF8 / 500 | 88 | 50 / 50 | 30 / 30 |
| SF7 / 500 | 120 | 50 / 50 | 30 / 30 |

The SF8 automatic preamble retains a measured floor of 88 symbols. Automatically
selected preambles always round up in steps of eight.

## Fractional-symbol tuning

The initial sweep started at 4.2 slow symbols, with an increase of 0.1 planned
for any slow-channel loss. That first sweep passed all three fast settings.
Each completed run used 50 requested adverts per profile. The SF8 fast run was
interrupted to check SF9 preamble 32, then restarted; only its completed run is
included below.

| Slow symbols | Fast profile | Fast preamble | Fast RX / TX | Slow RX / TX |
| ---: | --- | ---: | ---: | ---: |
| 4.2 | SF9 / 500 | 48 | 50 / 50 | 50 / 50 |
| 4.2 | SF9 / 500 | 32 | 50 / 50 | 50 / 50 |
| 4.2 | SF8 / 500 | 88 | 50 / 50 | 50 / 50 |
| 4.2 | SF7 / 500 | 120 | 50 / 50 | 50 / 50 |

The four completed pairings received 200/200 packets on the slower channel and
200/200 on the faster channel. No switch failures were reported; the longest
measured switch during the 4.2-symbol sweep was 4.407 ms.

The explicit SF9 preamble-32 check uses the same 8.602 ms slow and 8.166 ms fast
windows as the preamble-48 check. Only the fast transmitter's preamble changes.
The automatic SF9 calculation still reserves a 48-symbol preamble for the
configured switching and loop-jitter budgets.

## Shorter fast preambles

The follow-up starts with preamble 48 on both fast settings. Each miss advances
the next candidate by eight symbols. The receive-window budgets stay at 4.2
slow symbols and the same remaining fast-channel time.

| Fast profile | Preamble | Fast RX / TX |
| --- | ---: | ---: |
| SF8 / 500 | 48 | 49 / 50 |
| SF8 / 500 | 56 | 50 / 50 |
| SF7 / 500 | 48 | 42 / 50 |
| SF7 / 500 | 56 | 41 / 50 |
| SF7 / 500 | 64 | 45 / 50 |
| SF7 / 500 | 72 | 50 / 50 |

The slower channel passed 50/50 paired with SF8/56, but then received **49/50**
paired with SF7/72. Across the completed 4.2-symbol slow-channel runs, that is
299/300. This later miss invalidated the provisional 4.2-symbol choice and
triggered the next headroom test at 4.3 symbols. Fast-preamble baselines carry
forward as 32 for SF9, 56 for SF8 and 72 for SF7, advancing by eight on a miss.

At 4.3 symbols, the SF9/32 pairing received 100/100 slow packets and 50/50 fast
packets. The next SF8/56 pairing received only 42/50 slow packets, so that
candidate also failed. A control run with scanning disabled received 50/50 on
the slow channel. At 4.4, SF8/56 passed 100/100 slow and 50/50 fast, but the
SF7/72 pairing then received 49/50 slow packets.

At 4.5, the SF7 pairing passed 100/100 slow packets. Fast preamble 72 received
47/50, so the next baseline increased to 80 and passed 50/50. The following
SF8/56 slow run received 49/50, triggering 4.6 symbols. Baselines now carry
forward as SF9/32, SF8/56 and SF7/80.

At 4.6, both SF8/56 and SF7/80 passed 100/100 slow packets and 50/50 fast
packets. The following SF9/32 slow run received 49/50, triggering 4.7 symbols.
There were no switch failures; the longest observed switch was 4.414 ms.

At 4.7, the SF9/32 slow run passed its first 50 packets but received 99/100
over the full run. This triggered 4.8 symbols, with the same fast preamble
baselines. No fast-channel run was started for the 4.7 candidate.

## Selected bench baseline: 4.8 slow symbols

The 4.8-symbol sweep passed all three slow-channel checks, totaling 300/300.
Slow visits are 9.831 ms and fast visits 6.937 ms, plus actual switching time.
The longest measured switch was 4.491 ms, with no switch failures.

| Fast profile | Fast preamble | Fast RX / TX | Slow RX / TX |
| --- | ---: | ---: | ---: |
| SF9 / 500 | 32 | 50 / 50 | 100 / 100 |
| SF8 / 500 | 56 | 49 / 50 | 100 / 100 |
| SF8 / 500 | 64 | 50 / 50 | — |
| SF7 / 500 | 80 | 50 / 50 | 100 / 100 |

The SF8 miss advanced its baseline from 56 to 64, exactly eight symbols. Its
slow-channel check preceded that increase; changing the fast preamble leaves
the idle receive-window calculation unchanged. The final fast-channel attempts
received 150/150, using explicit preambles **32 / 64 / 80** for SF9 / SF8 / SF7.
These become the starting values for further tests, increasing by eight on a
fast-channel miss. A slow-channel miss instead advances the listen window by
0.1 symbol.

The production automatic preambles remain **48 / 88 / 120**, retaining the
switching, loop-jitter and acquisition budgets. The shorter explicit values are
bench results under the conditions above, not a guarantee for other boards or
traffic patterns.

## Transmit policy

Hardware checks send three zero-hop adverts from the V4 with the XIAO listening
on profile 2. OTA startup traffic is allowed to settle before taking the counter
snapshots. The completed cases are:

| V4 profiles and policy | TX on primary / secondary | Received by XIAO |
| --- | ---: | ---: |
| Permanent / permanent, `auto` | 3 / 3 | 3 |
| Permanent / permanent, `off` | 3 / 0 | 0 |
| Permanent / temporary, `auto` | 3 / 0 | 0 |
| Permanent / temporary, `on` | 3 / 3 | 3 |
| Secondary RX only, `on` | 3 / 0 | 0 |
| Temporary / temporary, `auto` | 3 / 3 | 3 |

A separate isolation check issued three explicit OTA announce commands with
normal `radio`, RX-only `tempradio2` and `radio2.cross auto`. The completed TX
counters stayed unchanged on both profiles (0 / 0), confirming that locally
generated OTA traffic does not fall back to the normal channel in that mode.

Turning `radio2` off stopped scanning. A one-minute `tempradio2` session also
returned to one profile on expiry. Rebooting with a saved SF8 RX profile and an
active temporary SF9 RX/TX profile cleared the temporary session and restored
the saved SF8 profile, including its automatic 88-symbol preamble.

## OTA discovery while retaining the main channel

Both boards ran normal SF7 / 62.5 kHz on the primary and temporary SF8 / 500 kHz
RX/TX on the secondary, using `radio2.cross auto` and automatic preambles 32/88.
The XIAO discovered the V4's firmware catalogue over profile 2, and both boards
recorded received and transmitted OTA packets there. Ten V4 zero-hop adverts
then arrived on the XIAO's primary profile (10/10), with both temporary second
profiles still active. The XIAO reported no switch failures and a longest switch
of 4.547 ms during this check.

This hardware check covers discovery, bidirectional OTA traffic and concurrent
availability of the main channel between packets. It does not perform a complete
firmware transfer or install. Byte-exact transfer validation is provided by the
shared OTA software tests described below.

## Software coverage

Native tests cover identical-packet fanout, separate retry ownership and coding
rates, channel-busy backoff, generation changes, temporary-session isolation,
deferred replies and stale-packet removal. Tests using the production CLI cover
parsing, persistence failures, schedules, expiry and clock rollover. A physical
radio mock exercises the production scan transitions, pending RX interrupts,
RX power-saving restoration and failed profile switches.

The full native suite passed 1,422 cases after the RX-only OTA isolation fix;
all eight KISS modem tests also passed. Subsequent timing changes through 4.8
symbols passed the 63-case retry/profile suite and production CLI and scan
harnesses. Shared OTA transfer tests passed byte-exact checks with
AddressSanitizer and UndefinedBehaviorSanitizer for ESP32 and nRF52 variants.

Build coverage includes the Heltec V4 repeater, room server, sensor and terminal
chat, the Full XIAO Companion, and the nRF52 T1000-E repeater. The nRF52 result
is a build check; these receive measurements use the ESP32/SX1262 boards above.

## Hardware restored after validation

Both boards were returned to their original B soak images after the checks:
`v1.17.1-soak-v4-B` and `v1.17.1-soak-xiao-B`. The V4 retained 909.5 MHz,
SF7 / 62.5 kHz, CR 4/5 and TX 0; the XIAO retained 910.525 MHz with the same
modulation and TX 22. Both reconnected to Wi-Fi and their custom MQTT service.
The soak logs resumed under `B-post-radio2-soak`. These restored images are the
earlier memory-soak firmware; the new dual-profile code is validated by the
finite tests above, not by a months-long uptime run.
The Pi's soak service and Indicator telemetry were also healthy after restoration.
