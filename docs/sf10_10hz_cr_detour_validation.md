# SF10/125: tiny first-pass frequency offset and coding-rate detour

2026-09-14. **The requested offset no longer rounds to zero. Actual outgoing
commands verified a +10-step RF-word change (nominal +9.536743 Hz), then exact
restoration of the original word, with CR4/6 then CR4/5. Two passes averaged
1.259 ms in RX-only timing. The first packet still timed out.** This is a
bounded one-probe failure, not a 400-packet loss-rate estimate.

## Scope and precision

The request was to apply a slightly different center frequency and coding
rate on the first full retune, then the correct settings on the second,
with about 0.01 kHz difference. At these centers a float in MHz advances in
roughly 61 Hz increments: adding 0.00001 MHz can produce the same float.

The HIL implementation does not make that float addition. A scoped integer
offset changes the four-byte argument of the actual 0x86 frequency command
by ten steps. The nominal SX1262 step is 32 MHz / 2^25; ten steps are
9.5367431640625 Hz, the nearest achievable nominal command offset to 10 Hz.
See the frequency-step specifications in the [Semtech SX1261/2 datasheet](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf).
Pinned RadioLib 7.7.1 also specifies this step and sets the standard sensitivity
configuration bit for bandwidths other than 500 kHz. This establishes the
programmed offset, not a spectrum-analyzer measurement of physical frequency.

First pass: destination RF word + 10 and CR4/6. Second pass: original RF word
and CR4/5. The offset is applied to both existing duplicate frequency writes
inside the first pass. Caller buffers remain unchanged; a scoped restore
prevents the offset leaking into the corrected pass or an unrelated command.
Rollback applies the offset only when restoring the temporary CR4/6 tuple.

Both passes retain ordinary packet/ownership/BUSY guards, command-result
checks and RX startup. A blocked second pass fails the diagnostic rather than
overriding a packet guard. Each completed hop verifies both outgoing frequency
words and modulation words (0x0a040200 then 0x0a040100). The host requires a
verification count equal to completed hops and zero mismatches.

This feature is HIL-only, off on reboot. Production defaults were not changed.
The earlier +31.25 kHz draft was superseded before flashing or RF testing.

## Fixture and method

- RX: XIAO ESP32-S3 / Wio SX1262, COM31, 28:84:85:B4:09:80.
- TX: XIAO nRF52840 / Wio SX1262, COM15, B35E71C1C3726CE7; unchanged firmware.
- Centers 909.5, 910.5, 911.5, 912.5 MHz; uniform SF10/BW125.
- 32-symbol preamble, 5.1-symbol visits (41,780 us), no added settling.
- Normal RX gain 0x94; warm XOSC, bulk SPI and fast RX; TCXO setting 1600 us.
- 16-byte CHS1 probes, -9 dBm chip TX request, varied arrival phase.
- Goal 100 packets/channel; stop at the first strict miss or 400/400.
- Four frequency commands, two forced modulation writes, two optimized RX
  starts per completed hop. No extra IRQ tracing/polling.

Same-image RX-only ABBA controls compare two identical full passes (A) with
the first-pass detour followed by correction (B), six seconds each, rebooting
between windows. This is not a comparison against the original single-pass
approximately 0.53 ms cached-modulation path.

## Timing

| Window | First-pass detour | Hops | Mean total (us) | Maximum (us) | First pass mean (us) | Second pass mean (us) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A1 | Off | 139 | 1230.094 | 1464 | 631.727 | 594.281 |
| B1 | On | 139 | 1259.770 | 1490 | 647.165 | 603.245 |
| B2 | On | 139 | 1258.806 | 1481 | 646.547 | 602.957 |
| A2 | Off | 139 | 1230.597 | 1467 | 633.209 | 593.252 |

Mean two-pass control 1230.346 us; detour/correction 1259.288 us, about
28.943 us additional total including configuration/verification bookkeeping.
The detour passes themselves averaged 646.856 + 603.101 us. Both detour
windows verified 139/139 completed hops with zero mismatches; all four
windows had exactly 556 RF commands, 278 modulation commands and 278 RX
starts, with zero guard blocks or RX/mode/cache errors.

## Packet result

Probe 1021099889, TX channel 3 at 912.5 MHz, timed out: **0 received / 1
attempted**, then stopped. TX rc=0, setup 86,914 us, transmit call 529,297 us.
RX was in receive mode, hardware device-errors=0. No payload-only diagnostic
ran because this was a timeout, not an intact payload attributed to another
channel. There were no RF retries.

During that packet window:

- 220 completed hops; 220 verified detour/correction pairs, zero mismatches.
- 880 RF commands, 440 modulation commands, 440 optimized RX starts.
- Mean switch 1262.095 us, maximum 1507 us; first pass 652.082 us, second
  601.295 us. Second-pass-blocked=0, retune failures=0, mode/cache errors=0.
- Maximum unheld four-channel cycle 172,222 us, below the 262,144 us preamble.
- Maximum held visit 542,732 us; the aggregate receive-error count was 1.
  That counter means a packet read returned an error; this capture does not
  retain its error code or IRQ trace, so it cannot establish CRC as the cause
  or assign the hold to a particular channel. Device-errors=0 is a separate
  hardware status and does not mean there were no receive errors.

Across both detour timing windows and the packet window, **498/498 completed
hops** verified the nonzero first-pass RF offset, original final RF word and
CR4/6-to-CR4/5 sequence. This proves the change was exercised; it did not
resolve reception in this bounded test. The underlying RF failure remains
unidentified.

## USB setup issue and verification

The first controller attempt stopped before transmitting because the new
126-byte capability JSON plus CRLF formed an exact 128-byte native-USB reply.
It remained buffered until another command was sent. This was preserved as
a fixture failure, not counted as packet loss. A single LF-terminated write
(127 bytes) fixed this specific reply; three independent reads then passed
without a follow-up command, and the complete controller ran successfully.

22 native profile tests and 49 host tests passed. Added tests cover integer
carry, unchanged caller bytes, both SPI paths, observation of shifted wire
bytes, exact restoration, scope exit/nesting, non-frequency commands,
overflow, detour verification failures and the USB reply length. Build/upload
passed with flash hash verification: RAM 94,948 bytes, reported flash 369,593
bytes. `git diff --check` passed (unrelated existing CRLF warning only).

Both boards rebooted idle after completion. RX confirmed scanner inactive,
zero channels, one-pass default, detour disabled, normal gain 0x94. TX reports
no autonomous transmission. HIL firmware remains installed as requested.
RAK COM29 was not opened. Temporary PlatformIO overlay removed; no collector
left running. This experiment made no commits, pushes or production-default
changes.

## Provenance

- Successful manifest: `tools/hil/profile_switch_sf10_10hz_cr_detour_verified_results.json`
  SHA-256 `84a14c3ca3169b8afdaffbef4405a5ea25140560c799091e5f22257814713bcb`
- Strict capture: `tools/hil/profile_switch_sf10_10hz_cr_detour_verified_results_strict.json`
  SHA-256 `e7995b327b5a84d8fc1830ece1f4cfccd70cae9f367eefd91b7e97ade4a9fb68`
- Setup-only failed attempt: `tools/hil/profile_switch_sf10_10hz_cr_detour_results.json`
  SHA-256 `486dd52a4ce5f4b20fadec4e6af2e1ede9669acd3e6a09b3490ae5c8b52b82dd`
- Installed RX BIN SHA-256:
  `aae7fd3ffcb5226759884e4d1b41d9da67bb97e95ef89d29dd54f204412d6b48`
- Unchanged TX ZIP SHA-256 from the preceding validated upload:
  `915d507d0feec1712fad2bd4be62c2fa8d769d11400711de0da1cf98a50a4b36`

Successful manifest complete, stopped `bounded_tests_complete`; strict capture
complete under first-miss stopping. No fixture or cleanup error in that run.
The build used the live keymindCascade working tree with existing dirty
changes and these HIL additions, not a clean-release image. HEAD observed at
completion was `2b5a6f209835f0f67ed73fee78d36447a4566ce6`; the unrelated
Companion USB commit advanced HEAD from the earlier experiment's base during
this ongoing work. Its files were not reverted. The RX image hash above is
the exact tested-image identifier. Prior captures were preserved.
