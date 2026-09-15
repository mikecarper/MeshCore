# Equal-symbol-time mixed SF/BW, four-channel test

2026-09-14. **Promising, but no strict 400/400 pass.** With no added settling,
the mixed scanner received 61 consecutive packets on the correct channels,
then timed out on packet 62. The near-ceiling +22.4 ms setting received four
packets and timed out on the fifth. There were no wrong-channel payloads in
either run. Both stopped on the first miss without retries.

## Configuration

| Channel | Center (MHz) | SF | BW (kHz) | Symbol time (ms) |
| --- | ---: | ---: | ---: | ---: |
| 0 | 909.5 | 9 | 62.5 | 8.192 |
| 1 | 910.5 | 10 | 125 | 8.192 |
| 2 | 911.5 | 11 | 250 | 8.192 |
| 3 | 912.5 | 12 | 500 | 8.192 |

These preserve the preceding SF10/125 symbol duration exactly, rather than
approximately: `symbol_us = 2^SF * 1000 / BW_kHz`. The supported SF/BW values
and symbol-rate equation are specified in Semtech's
[SX1261/2 datasheet, sections 6.1.1.1–2](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf).
Equal symbol durations do not imply equal payload airtime or sensitivity.

- RX: XIAO ESP32-S3 / Wio SX1262, COM31, MAC 28:84:85:B4:09:80.
- TX: XIAO nRF52840 / Wio SX1262, COM15, serial B35E71C1C3726CE7.
- Normal RX gain 0x94, -9 dBm TX chip request, CR4/5, 32-symbol preamble,
  16-byte CHS1 packets, 1 MHz channel spacing. No extra IRQ tracing/polling.
- 5.1-symbol visits = 41,780 us; programmed preamble = 262,144 us.
- Double frequency write retained, warm XOSC, bulk SPI and fast RX restart.
  SF/BW changes on every hop, so **one 0x8B command is required on every hop**.
- Goal per setting: 100 packets/channel, 400 total, strict channel identity.
  No relaxed off-channel acceptance and no automatic channel-count expansion.

The lab scanner stages four channels through the existing two production
profile slots; this does not change the public two-profile protocol.

## Positive controls

Before scanning, each profile received ten fixed-frequency probes after full
normal initialization. **All four passed 10/10, forty total**. Zero frequency
commands occurred after each setup; no stationary receiver hardware failure.
These are fresh ten-packet controls, not claims of 100/100 on each new profile.

Both devices acknowledge the same exact modulation table through `mixinfo`.
Transmitter results verify SF, fractional BW, center, sequence and packet size.
RX records verify center-command word and SF/BW at packet read. Full expected
payload identity and CRC remain required. Mode/cache errors are treated as
fixture failures rather than ordinary RF misses.

## Timing controls and settling budget

Six-second RX-only window at zero added delay: 141 hops, mean 637.922 us,
maximum 828 us. The largest 100 us delay step with a 500 us per-hop reserve is
`floor((262144/4 - 41780 - 828 - 500)/100)*100 = 22400 us`.

The +22,400 us RX-only verification window measured 87 hops, mean 638.241 us,
maximum 833 us. Nineteen complete idle cycles measured mean 259,292.789 us,
maximum 259,310 us, below the 262,144 us preamble. Every hop had two observed
frequency commands and one modulation command, with no mode/cache/RX errors.

This is an empirical near-ceiling budget, not a guarantee of acquisition
for every arrival phase. Longer packet holds and software jitter can defeat
the idle-cycle calculation. RX is active throughout added settling.

## Four-channel results

| Added settling | Strict received / attempted before first miss | Failed TX profile | Mean switch (us) | Max switch (us) | Max idle cycle (us) |
| --- | ---: | --- | ---: | ---: | ---: |
| 22,400 us | 4 / 5 | Channel 2, SF11/250 | 660.951 | 892 | 259391 |
| 0 us | 61 / 62 | Channel 0, SF9/62.5 | 674.819 | 899 | 169798 |

Both failures were timeouts, with TX rc=0, RX mode=1, device errors=0.
Both runs had zero mode/cache/receive errors and zero off-channel payloads.
The +22.4 ms run exercised 163 hops, 326 frequency commands and 163 modulation
commands. Zero-delay exercised 718 hops, 1,436 frequency commands and 718
modulation commands. Optimized RX resume counts equaled hop counts.

No-delay per-channel counts at stop:

| Channel | Received | Attempted | Good-packet mean RSSI (dBm) | Good-packet mean SNR (dB) |
| --- | ---: | ---: | ---: | ---: |
| 0 | 15 | 16 | -34.00 | 11.15 |
| 1 | 16 | 16 | -33.69 | 8.24 |
| 2 | 15 | 15 | -34.33 | 5.78 |
| 3 | 15 | 15 | -33.27 | 3.47 |

The receiver stayed longer on some visits: maximum held visit 582,993 us
with settling, 580,577 us without. Held-visit statistics include successful
packet reception, so these maxima alone do **not** prove false off-channel
preamble holds in this experiment. No IRQ trace was collected for the two
failures; their precise mechanism is not established.

The longer success sequence and absence of wrong-channel payloads are
encouraging relative to the earlier uniform-SF/BW tests, but do not establish
a statistically measured improvement or zero-loss reliability. Each setting
was censored at first failure; do not present 61/62 as an unbiased measured
packet-delivery rate or either run as a completed 400-packet test. The random
arrival method is seeded, not RF-phase synchronized across configurations.

## Implementation, verification and final state

HIL-only `ProfileMixedChannels.h`, `basemixed`, `mixtx`, `scanmixed` and
`profile_switch_mixed.py` add the bounded experiment. Uniform modes preserve
their defaults. Post-hop checks use the actual per-channel modulation;
the uniform collector and settling evidence checks reject mixed-mode results.
Receive ownership, packet guards, BUSY and command-error handling remain intact.

17 native profile tests and 48 HIL host tests passed. Tests cover fractional
62.5 kHz, equal symbol duration, table identity, normal uniform mapping,
required modulation writes, TX replay, and wrong-policy rejection. Both
firmware builds and normal uploads succeeded. The nRF52 ZIP is application-
only, SoftDevice ID 0x0123; no bootloader or SoftDevice replacement. RAK COM29
was not opened. Production settings/source were not changed this turn.

Both boards rebooted idle after the second failed setting, with test firmware
retained. No autonomous TX, background collectors or restoration. Temporary
PlatformIO overlay removed. No commit or push was requested. Existing unrelated
working-tree changes remain intact. `git diff --check` passed apart from
unrelated pre-existing CRLF warnings.

## Provenance

Raw capture: `tools/hil/profile_switch_mixed_equal_symbols_results.json`.
It includes the four baselines, two timing controls and two strict scans,
with `complete:true`, `stopped:both_delay_controls_failed`, and no fixture or
cleanup errors. Earlier captures were not rewritten.

- Raw SHA-256: `49de5c6415b1becdca02bc6e3c72d5e55127e92e758bbe3ac6c342c89e3723dd`
- RX BIN SHA-256: `20d3008ef3d92610510748f64ac90c327f37c91d96cd3ac55272a918867fd5d0`
- TX ZIP SHA-256: `915d507d0feec1712fad2bd4be62c2fa8d769d11400711de0da1cf98a50a4b36`
- TX HEX SHA-256: `1225bcc70854a224f2862443d816b0e9216a253da87c42e96569a933204af941`

RX build reported 94,844 bytes RAM / 367,537 bytes flash. TX reported
18,220 / 95,908 bytes. Source is keymindCascade commit
`0e5955f889788a4317863e731b38ffe09e3d5441` plus existing uncommitted work and
these HIL changes; this is not a clean-release comparison.
