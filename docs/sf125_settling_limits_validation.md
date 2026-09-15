# Descending SF10..SF5 / 125 kHz settling limits

Experiment started 2026-09-14. **Stopped at the user's request to replace the
Indicator transmitter with a V4. No 400/400 pass was found.** The saved
[top-level manifest](../tools/hil/profile_switch_sf125_first_pass_results.json)
remains incomplete; the [SF10 manifest](../tools/hil/profile_switch_sf125_first_pass_results_sf10.json)
contains all completed settings. Original evidence is not rewritten to make an
interrupted experiment look complete.

## Results at user-requested stop

94 SF10/125 settings completed: every added delay from 0 through 9,300 us in
100 us increments. Each stopped on its first RF miss; none reached 400/400.
The best individual setting was +1,000 us: 25 valid packets, then packet 26
failed. No lower SF was reached in this descending sweep. No result is claimed
for SF10 delays above 9,300 us or for SF9..SF5 in this run.

Across these completed settings: 641 probes, 547 valid and 94 failed. Because
each setting ends at its first miss, these totals are not an unbiased packet
error-rate measurement and do not establish a reliable lower/upper delay limit.

For the 547 valid receptions, reported SNR averaged **+8.311 dB**, minimum
+7.5 dB and maximum +9.5 dB. RSSI averaged **-40.547 dBm**, range -42 to
-39 dBm. These are conditional on successful reception; no SNR is inferred
for missing packets.

24,055 retunes averaged **456.620 us**, weighted by hop count and excluding the
intentional settling delay. Reported retune failures, RX-mode errors and cache
errors were all zero. Four RX errors were reported, one each at +1,100,
+5,900, +6,700 and +6,900 us. These counters do not identify the transmitter as
the cause. At +9,300 us, the unheld four-channel cycle averaged 206.134 ms
(maximum 206.157 ms), versus a 262.144 ms programmed preamble; this nominal
time margin still did not produce an all-pass setting.

Controllers and collector were stopped, and both boards were rebooted to idle
HIL. Follow-up polling is paused. Subsequent `info` checks returned ready on
both boards, with XIAO gain register 0x94 and Indicator 0x96. Test firmware was
retained; no original firmware was restored. Replacement-board testing has
not started.

Saved manifest SHA-256 at stop:

- Top level: `9cbf315704816be4d3cf7977e954e64a2033155e654cd7108919e6a17a5f8f8a`
- SF10: `509f632de1a463d4f5f84c055a1570468c5752685d596f4b445aa7d7c339189d`

## Requested method

XIAO ESP32-S3/WIO SX1262 COM31 RX, SenseCAP Indicator ESP32-S3 COM28 TX,
user-separated positions, XIAO boosted gain off (actual register 0x94),
Indicator gain unchanged at 0x96. Four centers 909.500/909.750/910.000/910.250
MHz; bandwidth 125 kHz, CR4/5, 5.1-symbol visits, 32 programmed preamble
symbols, 16-byte synthetic packets at -9 dBm. Warm fast RX, buffered 8 MHz
SPI and unchanged-modulation cache enabled. No extra IRQ polling/tracing.

Start SF10 and descend one SF at a time through SF5. For each SF, test added
post-BUSY delay in 100 us increments starting at zero. Every failed setting
stops on its first invalid reception/timeout; a pass requires 100/100 on
each of four channels. **Latest user instruction: stop the entire sweep at
the first 400/400 pass**, without continuing to find an upper limit or a lower
SF. Without any pass, continue to the ceiling, then lower SF. Fixture errors abort the entire
experiment. No failed RF probe is retransmitted.

This searches for a sampled all-pass setting, not proof of monotonic delay
behavior, an upper delay limit, or zero PER. The RX modem remains active during the
extra delay; the independent 5.1-symbol visit starts afterward. Thus extra
settling is also extra RX occupancy, not a pre-SetRx blanking interval.

| SF | Visit | Last 100 us delay step | Programmed preamble |
| --- | ---: | ---: | ---: |
| 10 | 41,780 us | 23,300 us | 262,144 us |
| 9 | 20,890 us | 11,400 us | 131,072 us |
| 8 | 10,445 us | 5,400 us | 65,536 us |
| 7 | 5,223 us | 2,500 us | 32,768 us |
| 6 | 2,612 us | 1,000 us | 16,384 us |
| 5 | 1,306 us | 200 us | 8,192 us |

Budget: `preamble_us / 4 - dwell_us - 453`, using the nominal retune cost.
Actual cycle means/maxima are recorded and may exceed that nominal budget.
The randomized arrival pause now includes added settling when estimating
two scan cycles; earlier saved captures retain their original distributions.

The stop policy changed after SF10 delays 0..5,600 us had all failed. The old
supervisors were stopped while the active 5,600 us collector finished normally.
Original captures are preserved. A fresh first-pass manifest reuses completed,
configuration-validated raw captures with their SHA-256 hashes, then resumes at
5,700 us; no completed setting is repeated. The earlier top-level manifest is
incomplete because its upper-limit policy was superseded, not due to an RF or
fixture fault.

## Build and initial checks

Both HIL targets compiled and verified uploads succeeded. The selected
**53 host/native tests passed**: 36 host collectors, seven profile native,
four SX1262, five radio and one receive-mode test.
After the stop-rule change, all 39 host collector tests passed, including new
whole-sweep first-pass stopping and matching, hash-preserving capture reuse.

Actually loaded firmware SHA-256:

- XIAO: `89faa3bbc5eade5e632e0c720c75be0ba9105e595b1384de74a00e09c0b60a24`
- Indicator: `fb06c52e138e5cd65fff1393e3f1208230b06977e5f824852086d4c80c89845c`

A one-second RX-only SF10/+23,300 us smoke check recorded 15 hops with
minimum delay 23,300 us, minimum post-delay dwell 41,782 us, and zero
retune/RX-mode/cache errors. Its two clean cycles averaged 262,125 us, maximum
262,127 us: only a tiny margin against the 262,144 us preamble, not a robust
worst-case bound. No RF probe was transmitted during that check.

Base `0e5955f889788a4317863e731b38ffe09e3d5441` on `keymindCascade`, plus local
changes. RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`,
Espressif32 6.11.0 / Arduino ESP32 2.0.17.

```text
python tools/hil/profile_switch_sf_limits.py --receiver COM31 --sender COM28 --output tools/hil/profile_switch_sf125_limits_results.json
```

Updated run:

```text
python tools/hil/profile_switch_sf_limits.py --receiver COM31 --sender COM28 --output tools/hil/profile_switch_sf125_first_pass_results.json --stop-on-first-pass --reuse-prefix tools/hil/profile_switch_sf125_limits_results.json
```

The controller reboots to idle HIL after each per-SF child completes or fails;
test firmware is to remain installed, per user request. Original private flash
backups are retained. RAK4631, Indicator RP2040 and unrelated S3 soak work are
not part of this experiment. No commit/push requested. Temporary build overlay
has been removed.
