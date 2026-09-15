# SX1262 profile-switch validation

Measured locally on 2026-09-13 using an XIAO ESP32-S3 with WIO SX1262.
Later tests also use the SenseCAP Indicator ESP32-S3 LoRa processor. The
[V8 integration](#production-integration-and-usb-recovery-v8) is the current
implementation; V1-V7 sections/captures document the development history.

## Initial result (V1)

The production retune path averaged **1.434 ms with XOSC held**, versus
**3.166 ms with RC standby**. Holding the oscillator saved **1.732 ms per
retune (54.7%)**. Warm-mode maximum was **1.467 ms** across 6,000 measured
retunes. The 6 ms switch allowance and 4 ms loop allowance are unchanged.

Each row combines both directions and two reversed-order trials, with 2,000
measured retunes per standby policy. Times run from production `tuneProfile`
entry until BUSY is observed low. GetStatus confirmed RX after every timed hop.

| Secondary profile | RC mean / maximum | XOSC mean / maximum |
| --- | --- | --- |
| SF7 / 500 kHz | 3.161 / 3.199 ms | 1.434 / 1.467 ms |
| SF8 / 500 kHz | 3.166 / 3.209 ms | 1.435 / 1.464 ms |
| SF9 / 500 kHz | 3.171 / 3.193 ms | 1.433 / 1.437 ms |

- Primary: 909.5 MHz, SF7 / 62.5 kHz, CR 4/5.
- Secondary: 910.5 MHz, CR 4/5, automatic production preamble calculation.
- TCXO configuration remained 1.8 V and 1,600 us startup delay.
- 12,000 measured retunes; zero retune failures, BUSY timeouts, or RX-mode errors.
- 109 packet/busy deferrals were excluded from hop timings (57 RC, 52 XOSC).
  Deferral preserves the existing packet and radio ownership checks.
- The first eight successful hops of every trial were excluded from
  steady-state statistics, including the initial oscillator transition.
- All 12 trials returned to RC standby after disabling the second profile.

## Scope and reproducibility

The [timing harness](../tools/hil/profile_switch_README.md) compiles the actual
production wrapper, not the earlier fast/minimal RX benchmark. RC and XOSC
use the same executable: the baseline changes only the standby policy after
entering dual-profile mode. No TCXO supply or initial startup delay is disabled.
The normal guard checks and frequency/SF/BW/CR/preamble setters remain active.

This isolates radio switching from UI, Wi-Fi, BLE, mesh dispatch, and scan-loop
scheduling. BUSY polling/timer overhead is included; the RX status query is
outside the timing. This is not a packet-delivery test, first-startup timing,
power measurement, wide-frequency image-recalibration test, or proof of analog
RF settling beyond BUSY/RX status. Indicator I/O-expander latency and nRF52
timing still need their own measurements before reducing common budgets.

Raw results: [profile_switch_results.json](../tools/hil/profile_switch_results.json).
The collector requires all requested samples, zero failure/timeout/mode-error
counts, and successful RC restoration before accepting a trial.

Build provenance:

- Base commit: `c329cf1fc1e7f2e8c8733ab0d000094730458fe3`, with the local warm-standby patch.
- Environment: `profile_switch_xiao`; Espressif32 6.11.0 / Arduino 2.0.17.
- RadioLib: pinned `187ef24791c3d844939b2be13a68bd890bd04e4c` (7.7.1).
- Test `firmware.bin` SHA-256:
  `629d39bba80ad97c434fbaab61bc7c3c302b5ac8180cb2cb12806508b2d111e0`.
- Existing unrelated Companion/repeater/Wi-Fi soak changes were not included
  in this minimal timing executable.

Host regression checks also passed: profile scan/CLI (3 tests), receive contract
(2 tests), and SX126x RX-status decoding (1 test). A normal
`heltec_v4_companion_radio_usb` build and the XIAO timing build succeeded.

The XIAO's original 8 MiB flash snapshot was verified against the device while
held in the bootloader before the timing firmware was uploaded. Raw flash
backups stay in the current user's private backup directory, outside Git.
After testing, the entire original snapshot was restored and verified again
against the device before reboot. The live USB CLI then confirmed `Xiao S3 WIO`
running `v1.17.1-soak-xiao-B` (11-Sep-2026). Its soak logger was left stopped;
existing logs were preserved. The Indicator and RAK Tag were not flashed.

## Follow-up: batched modulation

On the same date, a second 12,000-retune experiment held XOSC warm in both
cases and compared the old three-setter sequence with one combined LoRa
modulation update. Both paths ran in the same V2 executable, with the second
round reversing their order. Each mode completed 6,000 timed hops.

| Secondary profile | Separate setters: mean / maximum | Batched: mean / maximum |
| --- | --- | --- |
| SF7 / 500 kHz | 1.442 / 1.473 ms | 1.159 / 1.198 ms |
| SF8 / 500 kHz | 1.444 / 1.480 ms | 1.158 / 1.192 ms |
| SF9 / 500 kHz | 1.446 / 1.478 ms | 1.156 / 1.163 ms |

Overall: **1.444 ms to 1.158 ms**, saving **0.286 ms (19.8%)** per retune.
Use this matched V2 baseline for the batching comparison; the earlier V1
warm-standby numbers came from a different executable layout.

There were zero retune failures, BUSY timeouts, RX-mode errors, or SF/BW/CR
cache mismatches. All 12 trials restored RC standby after disabling profile 2.
84 packet/busy deferrals were excluded (54 separate-setter, 30 batched).
First eight successful hops per trial were excluded as before.

The batching removes two repeated modem checks and two modulation writes.
The helper validates a complete MeshCore bandwidth/SF/CR tuple, updates
RadioLib's cached SF/BW/CR before its automatic LDRO calculation, and restores
the old cache if the command fails. The wrapper's existing physical rollback
and safe-transition paths are retained. **Frequency changes, preamble updates,
standby, IRQ/buffer setup, packet parameters, and RX restart remain unchanged.**
This does not qualify eliminating those other repeated operations.

Raw results: [profile_switch_batch_results.json](../tools/hil/profile_switch_batch_results.json).
V2 test firmware SHA-256:
`b4403f6f5eb0a2b0a31c2d79859122225f247296699a578ad2596ef17eaed0fd`.
Toolchain and RadioLib pin match the first experiment. The normal
`heltec_v4_companion_radio_usb` build also succeeded with batching enabled.

The two new host tests execute the production method over 1,200 valid
bandwidth/SF/CR/LDRO combinations, invalid inputs and wrong modem, command
failures with cache rollback/retry, and the wrapper's failure short-circuit.
All eight relevant host tests passed. RF packet delivery and application/UI
scheduling remain outside this timing experiment; shared budgets are unchanged.

## Expanded screening: redundant RX setup and SPI (V5)

Corrected V5 measurements on 2026-09-13 cover **28,800 timed retunes in 300
trials** across XIAO S3/WIO SX1262 and the Indicator ESP32/SX1262. Each sweep
uses SF7/8/9 at 500 kHz paired with SF7/62.5 kHz, both directions, reversed
case order in round two, and eight excluded initial hops per trial.
All 300 trials completed their sample count and restored RC standby on exit.
There were **zero timed switch failures, BUSY timeouts, RX-mode errors or
SF/BW/CR cache mismatches**. The normal packet guards deferred 175 attempted
hops; these were not counted as timed retunes.

Twenty-one distinct cases were screened: seven individual redundant-operation
changes, a full manual RX control, combinations, 2/4/8 MHz SPI and whole-buffer
ESP32 SPI transfers. Shared controls appear in both groups. All new shortcuts
are **HIL-only**, not included in normal targets.

### Individual and combined RX changes

Means in milliseconds, with warm oscillator and batched modulation throughout.
Each XIAO row has 768 timed hops; each Indicator row has 384.

| RX/SPI case | XIAO mean | Indicator mean |
| --- | ---: | ---: |
| Production batching, 2 MHz SPI | 1.182 | 15.190 |
| Manual RX sequence, no omissions | 1.181 | 15.135 |
| Skip second standby | 1.124 | 14.195 |
| Skip repeated IRQ setup | 1.096 | 14.399 |
| Skip repeated buffer-base setup | 1.146 | 14.437 |
| Skip repeated packet-parameter setup | 0.991 | 12.364 |
| Skip RX modem query | 1.145 | 14.427 |
| Defer preamble to the one RX packet-parameter write | 0.956 | 11.659 |
| Skip wake NOP when already awake | 1.146 | 14.576 |
| Combined omissions, early preamble write | 0.771 | 9.096 |
| Combined omissions, deferred preamble | 0.732 | 8.384 |
| 4 MHz SPI only | 0.985 | 14.996 |
| 8 MHz SPI only | 0.889 | 14.889 |
| Combined/deferred, 4 MHz SPI | 0.627 | 8.280 |
| Combined/deferred, 8 MHz SPI | 0.579 | 8.224 |

The largest individual saving is programming preamble/packet parameters once.
Compare individual omissions with the manual full control when attributing
their isolated costs: replacing RadioLib's staging dispatch also has overhead.
Individual savings are not additive; some remove overlapping work.

### Whole-buffer SPI follow-up (same V5 binaries)

| Case | XIAO mean / maximum | Indicator mean / maximum |
| --- | --- | --- |
| Production batching, byte loop, 2 MHz | 1.184 / 1.221 ms | 15.185 / 15.785 ms |
| Bulk transfer only, 2 MHz | 1.070 / 1.073 ms | 15.092 / 15.134 ms |
| Bulk transfer only, 4 MHz | 0.890 / 0.903 ms | 14.924 / 14.983 ms |
| Bulk transfer only, 8 MHz | 0.797 / 0.806 ms | 14.836 / 14.908 ms |
| Combined/deferred, byte loop, 8 MHz | 0.579 / 0.588 ms | 8.222 / 8.843 ms |
| Combined/deferred + bulk, 2 MHz | 0.671 / 0.700 ms | 8.369 / 9.027 ms |
| Combined/deferred + bulk, 4 MHz | 0.582 / 0.595 ms | 8.252 / 8.315 ms |
| Combined/deferred + bulk, 8 MHz | **0.535 / 0.545 ms** | **8.203 / 8.233 ms** |

The fastest XIAO candidate saves **0.649 ms (54.8%)** versus that sweep's
already-warm, already-batched production control. At 8 MHz, bulk transfers
alone add about 0.043 ms of benefit to the combined RX shortcut. The Indicator
remains dominated by I2C-mediated chip-select/BUSY/DIO access; the final bulk
step improves its combined mean by only 0.019 ms. Small HAL effects varied
between executable layouts in preliminary screens; do not generalize them.

**The Indicator exceeds the generic 6 ms switch allowance even in its fastest
experimental path.** These are board measurements, not a justification for
reducing the shared 6 ms switch / 4 ms loop constants. Indicator scheduling
and effective preambles need board-specific treatment before a production
rollout. Application/display/network scheduling is still excluded here.

Raw accepted sweeps:

- [XIAO RX cases](../tools/hil/profile_switch_v5_rx_xiao_complete.json)
- [Indicator RX cases](../tools/hil/profile_switch_v5_rx_indicator.json)
- [XIAO bulk SPI](../tools/hil/profile_switch_v5_bulk_xiao.json)
- [Indicator bulk SPI](../tools/hil/profile_switch_v5_bulk_indicator.json)

### Test corrections and provenance

V3/V4 broad screening accidentally called `SX1262::startReceive()` instead of
`CustomSX1262::startReceive()` in the fallback, omitting MeshCore's extra
preamble-detection IRQ flag. Their raw files are marked superseded and are
not included in the counts or conclusions above. The V4 packet sweep was
stopped on discovery. V5 preserves that flag in both normal and manual RX
paths; a host regression test checks the Custom fallback and mapped flags.
The earlier V1/V2 warm/batching experiments did not use that experimental
subclass and are unaffected.

An initial V5 XIAO capture stopped on a partial serial JSON line during guarded
mode exit. It is retained as incomplete, not counted above. The host now
accumulates reads until newline, with regression coverage. Initial setup
rejections are retried only for the explicit pre-measurement packet-guard
reply, bounded to three seconds and counted; packet losses and timed failures
are never retried away.

Final V5 firmware SHA-256:

- XIAO: `5506a40ffa8240201aca519e8e24db3647f897e7a1290bba22be6e4986d8f02f`
- Indicator: `dd595b2e7fa8c1ccb14fac9223f1a57277cd6a602864ee9bf090672f494fa2f6`

Base commit/toolchain/RadioLib pin match the earlier experiments. Thirteen unique
host tests passed (eight production regressions, two HIL boundary/buffer tests,
three serial-reader/setup-guard tests). The HIL tests cover all 255 nonzero omission masks,
normal-path fallbacks, preserved IRQ clear/SetRx, error short-circuit and
full-duplex buffer lengths 1..260. They are not full driver lifecycle tests.
An additional normal `t1000e_companion_radio_usb` nRF52 build passed with the
production warm/batching changes: 591,484-byte app, runtime RAM guard passed.
It was not flashed to the RAK or any nRF52 board.

### Qualification boundary

No BUSY wait, command status validation, stale-IRQ clear, RX command or wrapper
packet-ownership guard is removed. The fastest path relies on a known
RX-to-RX window and established RX setup. Correct invalidation after TX, CAD,
sleep, reset, failed commands, and recovery must be designed and tested before
promoting these shortcuts. The per-byte/bulk HAL experiment is ESP32-specific.
Sensitivity/PER, oscillator power draw, large-frequency image recalibration,
and application-driven continuous scanning remain unqualified.

### Actual packet checks and limitations

Final forward matrix: Indicator transmitter to XIAO receiver, -9 dBm,
21 cases x SF7/8/9 x both profiles x 16/64/255-byte payloads = **378 probes**.
**359 sequence/length/payload/profile checks succeeded**, 13 receiver-declared
wait timeouts occurred, and six receiver USB replies did not arrive before
the host deadline. Transmitter replies returned success. Successfully read
packets had RSSI -42 to -39 dBm and SNR 10.2 to 14.2 dB. Timeout diagnostics
reported RX mode and no device errors; some retained a preamble IRQ. These
observations do not establish why packets were missed.

The production-batched baseline delivered 17/18 in that matrix; the fastest
combined/bulk/8 MHz candidate delivered 18/18. An earlier reversed-order
smoke test of the fastest candidate had one receiver timeout, so the clean
18-packet batch is **not** a reliability qualification. Earlier 0 dBm checks
also had baseline and candidate misses. Reducing power did not eliminate them.

Reverse direction: XIAO transmitter to Indicator receiver, -9 dBm, baseline,
combined/8 MHz and combined/bulk/8 MHz, with the same SF/profile/length matrix.
**All 54 payloads arrived intact**, 18/18 per case. However, one XIAO USB TX
acknowledgement was delayed past the host deadline, after which 32 later
records picked up the previous command's acknowledgement. Thus the raw file
has only 21 fully matched transactions despite 54 correctly received payloads.
This is sequence-correlated evidence of a host acknowledgement problem, not
33 RF losses. Subsequent collector code rejects/stores stale sequence replies
instead of associating them with a new command, and now asserts DTR on Windows
to match the existing `s3_memory_soak.py` HWCDC session policy. The captures
above used DTR deasserted; a causal DTR A/B test was not performed. Sequence
handling has host regression coverage, but these last host changes were not
rerun on HIL firmware after the boards were restored.

USB acknowledgements and actual receiver payload validity are recorded
separately. Missing acknowledgements never trigger an automatic retransmit.
The host drains the receiver concurrently with the transmitter's command and
records timeouts without hiding them. These tests establish some real packet
functionality, not a controlled packet-error-rate comparison. Fix/qualify the
test transport, repeat with a stable RF reference, then test TX/CAD/reset/RXPS
and continuous application scanning before promoting the shortcuts.

- [Forward matrix, all 21 cases](../tools/hil/profile_switch_v5_packets_complete.json)
- [Reverse matrix, three cases](../tools/hil/profile_switch_v5_packets_reverse_complete.json)
- [0 dBm smoke](../tools/hil/profile_switch_v5_packet_smoke.json)
- [-9 dBm reversed-order smoke](../tools/hil/profile_switch_v5_packet_minus9_smoke.json)

Earlier incomplete packet captures and superseded screens are retained for
audit, not counted in the 378/54 final matrices. No identity or channel secrets
were transmitted: probes used synthetic sequence-tagged bytes.

## Production integration and USB recovery (V8)

Normal XIAO S3 WIO and Indicator LoRa variants now opt in to buffered 8 MHz
SPI and a guarded fast RX-to-RX path. Other SX1262 variants retain default
SPI/full RX setup; modulation batching and warm standby remain shared.

The fast path keeps the TCXO warm, applies one SF/BW/CR tuple and one packet
tuple, clears stale IRQs and starts RX with the original BUSY handling. It
reuses an established LoRa IRQ map and buffer bases only within an owned
continuous-RX retune. Ordinary standby, staging/TX, CAD, sleep, reset/init,
RX duty cycling and command failures invalidate that reuse. RX startup failure
now fails the whole profile transaction and restores the old profile/caches
with full setup; failed recovery keeps the profile marked for refresh.

### Sleep recovery discovered during integration

Initial lifecycle captures found `XOSC_START_ERR` (0x20) after retained sleep,
despite successful RX status. RC-policy controls reproduced the same flag:
this was not established as a regression caused by the fast path. Simply
waking in RC and entering standby before sleep did not clear it. Reapplying
the already configured TCXO voltage and 1,600 us delay resolved it.

The production sleep override now enters standby first, wakes in RC, and
restores TCXO configuration on wake. It refuses to clear unrelated device
faults. The warm policy resumes after full RX setup. This follows the standby
entry requirement in section 13.1.1 of the
[Semtech SX1261/2 datasheet](https://files.waveshare.com/wiki/SX1262-XXXM-LoRaWAN-GNSS-HAT/DS_SX1261-2_V1.2.pdf).
Section 13.3.6 describes TCXO control and the XOSC error flag; it does not by
itself prove the cause of this retained-sleep observation.

Diagnostic files `profile_switch_v6_recovery_xiao.json`,
`profile_switch_v7_recovery_xiao.json`,
`profile_switch_v7_recovery_xiao_complete.json` (despite its filename, its
`complete` field is false), and `profile_switch_v7_sleep_diagnostic_xiao.json`
retain these failures. They are not counted as passing lifecycle tests.

### USB fix and scope

The HIL host distinguishes Espressif native CDC (DTR asserted) from CH340
bridges (DTR/RTS deasserted). Replies are newline-assembled and sequence-matched.
Firmware caches completed packet **and timing** reports before emitting and
flushing them. A timeout or malformed report triggers a sequence-scoped result
query, never a repeated TX or timing run. Randomized session sequence starts
avoid a cached result from an earlier host session.

V7 had successfully fixed/retested packet result replay but a long native-USB
timing sweep still lost one response. V8 adds whole-report timing buffering and
replay. The incomplete V7 XIAO timing capture is retained, not silently completed.
The public MeshCore Companion USB protocol is unchanged; these USB fixes are
for the radio-lab transport. The previous broad Windows-DTR policy described
in the V5 history above has been replaced by native-device identification.

Both boards passed **20 reopen cycles**, deliberate lost timing replies,
deliberate lost TX replies, stale TX reply isolation, and **18 lifecycle cases**
each. The cases cover TX, CAD, sleep, reset/init, RC-policy sleep and explicit
TCXO-restoration controls, repeated three times. Fast counters prove one full
RX fallback after invalidation and reuse only on the following retune.
All command results, RX-mode checks and device-error checks passed.

- [XIAO recovery](../tools/hil/profile_switch_v8_recovery_xiao.json)
- [Indicator recovery](../tools/hil/profile_switch_v8_recovery_indicator.json)

V8 test firmware SHA-256 (same pinned RadioLib/toolchain as above):

- XIAO: `e1806bbf4af5a88b31423131806a682713221082235507ff95e5beb9ef3ac067`
- Indicator: `bab4009078a4fff68dfb7aa73a5afc83e84f5c47589354d8aeb8e5b8d8798442`

Normal USB Companion builds passed for `Xiao_S3_WIO_companion_radio_usb`
(813,901 app bytes / 63,012 static RAM),
`SenseCapIndicator-LoRa_comp_radio_usb` (879,809 / 62,960), and
`t1000e_companion_radio_usb` (591,980 / 179,868). Runtime RAM budgets passed
on all three. These normal application images were compiled, not substituted
for the user's configured soak firmware during the HIL tests.

Host/native regression coverage includes production modulation caches/LDRO,
standby failure short-circuit, RX-start rollback failure, ownership guards,
fast-state invalidation, TCXO wake fault preservation, buffered SPI semantics,
USB result overflow/replay, partial reads and stale-sequence handling.

### Final steady-state timing

V8 ran 256 timed retunes per trial, four production configurations, SF7/8/9
at 500 kHz paired with SF7/62.5 kHz, and two reversed-order rounds on each
board: **12,288 measured retunes / 48 trials**. The first eight hops per trial
are excluded. There were zero retune failures, BUSY timeouts, RX-mode errors
or modulation-cache errors. All 48 trials restored RC policy on exit. The
normal packet guards deferred 21 XIAO and 59 Indicator hops; no setup retries
or naturally missing/replayed USB reports occurred in these final sweeps.

| Production configuration | XIAO mean / maximum | Indicator mean / maximum |
| --- | --- | --- |
| Warm + batching, per-byte SPI 2 MHz | 1.193 / 1.227 ms | 15.174 / 15.836 ms |
| Fast RX, per-byte SPI 2 MHz | 0.744 / 0.770 ms | 8.426 / 8.471 ms |
| Full RX, buffered SPI 8 MHz | 0.828 / 0.840 ms | 14.816 / 15.386 ms |
| Fast RX + buffered SPI 8 MHz | **0.549 / 0.556 ms** | **8.263 / 8.879 ms** |

The combined production path saves **54.0% on XIAO** and **45.5% on Indicator**
relative to each same-image warm/batched control. Small differences from V5-V7
reflect different binaries/code layout; only within-image A/Bs are compared.
Both fast configurations must increment the production success counter; full
RX controls must not. Mode/cache verification happens after the timed region.

- [XIAO V8 timing](../tools/hil/profile_switch_v8_production_xiao.json)
- [Indicator V8 timing](../tools/hil/profile_switch_v8_production_indicator.json)

The common 6 ms switch and 4 ms loop allowances are unchanged, not delays
inserted by the driver. Indicator still exceeds 6 ms. These measurements do
not qualify continuous application scheduling, scan-overlap reception,
sensitivity/PER, power use, temperature extremes, or image recalibration on
frequency changes larger than this 1 MHz pair. Do not shorten common transmit
preambles or advertise the XIAO number for other boards.

### Final over-air packet matrix

**288/288 payloads and command transactions passed**: 144 Indicator-to-XIAO
and 144 XIAO-to-Indicator. Each direction covers all four production cases,
SF7/8/9 secondary profiles, both target profiles, 16/64/255-byte synthetic
payloads and two reversed-order rounds. TX used buffered 8 MHz SPI at -9 dBm.
Fast receiver readiness reports had to show actual production fast resumes.

There were **zero RX errors, receiver timeouts, missing replies, stale replies,
or natural transport-recovery queries** in these matrices. Deliberately lost
and stale replies were separately exercised by the recovery tests above.
RSSI ranged -43 to -39 dBm and SNR 10.2 to 14.5 dB. This clean short-range
matrix addresses the failures seen in the earlier V5 harness; it does not
identify which single change caused each historical miss or establish a
field packet-error rate.

- [Indicator TX / XIAO RX](../tools/hil/profile_switch_v8_packets_forward.json)
- [XIAO TX / Indicator RX](../tools/hil/profile_switch_v8_packets_reverse.json)

All **18 host/native regression tests** passed. The HIL builds do not include
the unrelated pre-existing Companion/repeater/Wi-Fi soak edits; the normal
application compile checks include the current working tree and preserve them.

A subsequent requested [SF6/125 kHz multi-channel test](sf6_channel_scan_validation.md)
used 32-symbol preambles and 4.8-symbol visits. It stopped on the third packet
at the initial four channels (two received, one timeout); no higher count was
tried. That continuous scan experiment is separate from the 288/288
stationary-after-retune matrix above.

## Unchanged modulation tuple follow-up

The initial software-only checks below were followed by
[same-image hardware timing and separated-radio tests](separated_radio_modulation_cache_validation.md).
Frequency-only means improved from 548.880 to 451.897 us on XIAO and from
8,263.438 to 7,601.193 us on Indicator. Four-channel SF6 reception was not
loss-free. The following paragraph records the initial implementation stage,
not the current hardware-test status.

Implemented 2026-09-14 after commit `0e5955f8`, without restarting hardware
tests. On opted-in fast RX-to-RX hops, `setLoRaModulationParams()` now compares
the requested SF, encoded bandwidth, encoded CR and effective LDRO against
the last successfully acknowledged modulation write. An exact match within
a valid owned standby window omits only `SetModulationParams` (0x8B).
Frequency programming, the modem query, preamble/packet parameters, IRQ clears,
RF-switch control and BUSY waits remain unchanged.

The acknowledgement is separate from RadioLib's mutable software fields.
Auto/manual LDRO compares the resulting command byte, with the pinned
RadioLib >=16 ms symbol rule. A failed write revokes the whole fast context
before rollback. Ordinary SF/BW/CR/forced-LDRO setters invalidate the saved
tuple even on failure. Existing full-RX staging, ordinary standby, TX/CAD
staging, reset/init, sleep, duty cycling and failed RX/standby operations also
invalidate it. The first hop after lost context therefore writes again;
frequency-only hops that retain the context can skip subsequent writes.

All **30 selected host/native tests passed**, including repeat-write counts
for all supported bandwidth/SF/CR combinations and auto/forced LDRO modes,
each independently changed field, LDRO policy transitions, failed-write retry,
modem-query failure, ownership gates, ordinary setters and lifecycle invalidation.
Both `profile_switch_xiao` and `profile_switch_indicator` compiled successfully
against the pinned RadioLib. No firmware was uploaded, no radio command was
issued, and no new RF result was collected. The temporary build overlay was
removed. The earlier ~0.549 ms XIAO and ~8.263 ms Indicator timings do **not**
measure this added optimization; its time saving and RF behavior await testing.
