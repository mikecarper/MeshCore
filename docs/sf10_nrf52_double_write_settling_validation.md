# SF10 / 125 kHz, XIAO nRF52 TX, double write, 1 ms delay grid

2026-09-14. **Fixed-channel positive control passed 100/100. The subsequent
four-channel, double-write sweep failed at every 1 ms step from +0 to +23 ms.**

New sender serial B35E71C1C3726CE7 first enumerated as Zephyr CDC COM33
(2FE3:0004). A 1200-baud touch exposed Seeed XIAO bootloader COM25
(2886:0044, XIAO-BOOT). The application-only DFU ZIP was accepted with its
normal compatibility checks, reported programmed successfully and reappeared
as COM15 (2886:8044), with the same serial and ready XIAO nRF52840/Wio SX1262
HIL identity. No bootloader/SoftDevice replacement or full erase was performed.
The unrelated RAK COM29 (4D6F77FD74011872) was not opened.

## Requested test

- Receiver: existing XIAO ESP32-S3/Wio SX1262 on COM31, normal RX gain 0x94.
  MAC 28:84:85:b4:09:80. The host reasserts double-write mode after every reboot.
- Sender: XIAO nRF52840/Wio SX1262 COM15, serial B35E71C1C3726CE7.
- SF10/125 kHz, CR4/5, four channels, 5.1-symbol listening visits, 32-symbol
  preamble, 100 packets/channel, fixed 16-byte probes at -9 dBm chip request.
- Added post-BUSY settling 0, 1, 2, ... 23 ms. Each failed setting stops at
  its first miss; the entire sweep stops at its first 400/400 pass. No added
  delay beyond the nominal full-sweep timing ceiling and no lower SF runs.
- Double frequency writes retained; modulation cache on; no extra IRQ trace.
  No prior-transmitter or single-write measurements will be reused.

Budget uses 539 us nominal double-write retune (ceil of the measured 538.192
us RX-only mean), 41,780 us post-settle visit and 262,144 us preamble:
`262144 / 4 - 41780 - 539 = 23217 us`. Last 1 ms grid point is 23,000 us.
This is a nominal ceiling, not a worst-case jitter or zero-loss guarantee.

## Preparation

The settling controller now accepts `--step-us 1000 --frequency-repeat on`;
its existing defaults are unchanged. Capture policy checks reject mixed
single/double evidence. The 42 host collector tests and 14 selected profile
native tests passed, including grid/first-pass stop, nRF USB DTR behavior,
transmitter replay, setup/TX failure, stationary payload integrity, zero-retune
and host/device-count checks. `git diff --check` passed (unrelated CRLF warnings).

Minimal `profile_switch_xiao_nrf52_tx` firmware compiled successfully: 95,340
bytes reported flash usage, 18,212 bytes static RAM. It uses the production
XIAO pin map and CustomSX1262 initialization, exposes only reference TX and
cached USB results, and has no Mesh identity or application settings writes.
Includes the existing build-local bounded nRF USB READY/HFCLK fix.

Platform: Nordic nRF52 10.8.0; Adafruit nRF52 framework
1.10701.0+sha.d5413016; ARM GCC 14.2.1; RadioLib 7.7.1 at
`187ef24791c3d844939b2be13a68bd890bd04e4c`.

Generated DFU ZIP contains **application only**, requiring SoftDevice ID
0x0123. Upload used normal DFU validation without compatibility overrides.
The V4's earlier no-backup erase authorization is not treated as authority to
erase this new board's bootloader or unrelated storage.

Flashed nRF52 transmitter SHA-256:

- HEX: `aaee693900dedc3816a6baeca576c7449502d30c05ac48e2663b02acec9aec9a`
- DFU ZIP: `7466625ef5e7bcc8f7dd16db2b843db29b4312a33b0d32bc0b1e1193d6aee03a`

## Baseline requested before continuing the sweep

The initial four-channel run was interrupted at the user's request after
13 completed settings, added delay 0..12,000 us. Each missed its first packet:
nine matched the test sequence but were reported on channel 2 when channel 3
was expected; four timed out. These are not 13 full 400-packet measurements
and must not be presented as an unbiased loss rate. The incomplete original
top manifest and all raw captures under
`tools/hil/profile_switch_sf10_nrf52_double_1ms_results*` remain unchanged.

Added an independent HIL-only stationary receiver: one full normal radio
initialization, fixed 910.25 MHz (channel 3), normal RX rearm, no profile
scheduler/fast-switch/cache shortcut. All 100 packets use the same transmitter,
SF10/125, CR4/5, 32 preamble symbols, 16-byte CHS1 body and -9 dBm request.
No RF retries and no early stop on an RF miss; hardware/fixture faults abort.

Result: **100/100**, CRC/read errors 0, foreign packets 0, hardware errors 0,
observed frequency writes after setup 0. Normal gain register 0x94 and RX mode
1 verified. RSSI mean -33.610 dBm, range -34..-33; SNR mean +8.736 dB,
range +7.8..+10.0. Baseline cleanly stopped and both devices rebooted idle.
This confirms the fixed-channel link in this finite sample, not all-channel
or interference immunity.

Capture: `tools/hil/profile_stationary_nrf52_sf10_100_results.json`.
SHA-256: `547c30efc89c29e3f60cb975f7fc34de3e9f14bed3ed4cc9f1ba01f63402bf15`.
Receiver image with stationary capability compiled/flashed and hash-verified:
SHA-256 `0daab2e3293359280714042492639ab8c176609143cf01996ad4ba7026704768`.
Reported flash 364,657 bytes; static RAM 94,796 bytes. Existing double-write
and four-channel switching implementation is unchanged; baseline is a
separate inactive command mode during the scan.

## Post-baseline four-channel run

Fresh prefix: `tools/hil/profile_switch_sf10_nrf52_postbaseline_double_1ms_results`.
Command: `profile_switch_settling.py --receiver COM31 --sender COM15 --sf 10
--frequency-repeat on --step-us 1000 --output <fresh-prefix>.json`.
No old captures are reused. Stop at the first 400/400 or after +23 ms.

Completed all 24 settings. **No 400/400 pass.** At zero added delay, the first
packet passed and the second timed out. Each +1..+23 ms setting failed its
first packet. Total 25 probes: one valid, 17 timeouts and seven test-sequence
packets reported on channel 2 while expecting channel 3. Each setting stops
on its first miss; this is deliberately censored evidence, not a measured
unbiased 4% packet-delivery rate. The lone valid packet was -33 dBm / +8.2 dB
SNR. The wrong-channel records do not alone distinguish real off-channel
acquisition from channel bookkeeping; no root cause is claimed.

Measured 3,086 retunes, weighted mean **526.783 us (0.527 ms)**, maximum
876 us (0.876 ms), excluding the separately applied settling delay. Exactly
6,172 SetRfFrequency commands confirm two per hop. One modulation command
was issued across the measured scan windows (after the sole received packet);
no retune failures, RX-mode errors, cache errors or CRC/read errors.
All gain checks remained 0x94. This measurement is from the receiver image
with the new independent baseline mode, not a controlled performance A/B
against the previous image's 538.192 us RX-only mean.

At +23 ms the measured settling interval was 23,000..23,001 us and mean
switch-plus-settle 23,567.2 us. That short failed window did not contain a
complete unheld scan cycle, so it provides no measured final-step full-sweep
latency. The ceiling remains the nominal 23,217 us calculation above.

Post-baseline top manifest SHA-256:
`badda7f64a7bdd445942da19dc76dea0d1aace2ae451772edf4744d4670ac28c`.
Interrupted pre-baseline top manifest SHA-256:
`90f7b213bc44ae73714c5d9d32216ff3f1760fafd657feb5ea9299ed1e10ddf3`.

Conclusion: the fixed-channel link passes its 100-packet control, but extra
post-switch settling through the nominal budget does not cure this scanner
failure. Investigate acquisition/hold behavior and channel attribution next;
do not claim more waiting, a second frequency write, or a transmitter swap
has fixed the issue. No guard/default production timing was changed here.

Collector exited successfully at the nominal timing limit, with no cleanup
error; both radios rebooted idle. The previous follow-up automation remains
paused. No commit or push requested; HIL firmware remains installed. Temporary
build overlay removed. No background test collector remains running.
