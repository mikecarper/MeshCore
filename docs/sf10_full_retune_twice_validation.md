# SF10/125: apply the full radio settings twice per hop

2026-09-14. **Two full retune passes were verified, averaging 1.276 ms per
channel switch. This did not resolve reception in the bounded packet test:
the first packet timed out.** This is one failed probe, not a 400-packet loss
measurement. No production defaults or receive guards were changed.

## Requested interpretation and fixture

"The 125 run" was interpreted as uniform SF10/125 on four channels, not the
preceding mixed-SF/BW experiment. Apply the complete retune twice to each
destination: frequency, modulation, preamble/packet configuration and RX
restart, retaining normal guards and command-result checks for both passes.

- Receiver: XIAO ESP32-S3 / Wio SX1262, COM31, 28:84:85:B4:09:80.
- Transmitter: XIAO nRF52840 / Wio SX1262, COM15, B35E71C1C3726CE7.
- Centers: 909.5, 910.5, 911.5, 912.5 MHz. SF10/125, CR4/5, 32-symbol
  preamble, 5.1-symbol visits, 16-byte CHS1 probes, -9 dBm chip request.
- Normal RX gain 0x94; warm XOSC and fast/bulk RX path; TCXO timing 1600 us.
- No added settling and no extra IRQ tracing/polling. Goal: 100 packets per
  channel, stop on the first strict miss or a complete 400/400 pass.
- The previous immediate double frequency write remains inside **each**
  pass: four frequency commands per hop. Modulation is forced in both passes,
  giving two 0x8B commands and two optimized RX starts per completed hop.

The earlier approximately 0.53 ms path could skip unchanged modulation.
This test deliberately writes those settings again, so it measures more than
0.53 + 0.53 ms. No artificial time padding was added to meet an expected value.

## Same-image RX-only timing

Six-second windows in 1/2/2/1-pass order, with a reboot before each. Both one-
and two-pass controls force modulation writes, so the comparison isolates
complete repetition rather than also changing cache policy.

| Window | Passes per hop | Hops | Mean total (us) | Max total (us) | Mean first pass (us) | Mean second pass (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A1 | 1 | 141 | 647.284 | 847 | 644.220 | — |
| B1 | 2 | 139 | 1275.410 | 1491 | 648.899 | 622.835 |
| B2 | 2 | 139 | 1276.086 | 1488 | 649.000 | 623.288 |
| A2 | 1 | 141 | 647.858 | 836 | 644.872 | — |

Equal-weight/hop-count means: one full pass 647.571 us; two full passes
1275.748 us. The first and second passes average 648.950 + 623.062 us;
the total includes approximately 3.7 us additional wrapper/BUSY bookkeeping.
Both two-pass windows had 556 frequency writes, 278 modulation writes and
278 optimized RX starts. No second-pass guard block or hardware/cache error.

## Packet test

First probe: TX channel 3, 912.5 MHz, sequence 600920621. TX returned rc=0,
setup 86,914 us, transmit call 529,297 us. RX timed out with mode=1 and device
errors=0. The controller stopped without retrying or testing further packets.
No separate payload-only diagnostic ran because this was a real timeout,
not an intact wrong-channel delivery.

During the packet window:

- 223 channel hops; 892 frequency writes; 446 modulation writes; 446 optimized
  RX starts. Both pass counters = 223, second-pass-blocked = 0.
- Mean full switch 1275.058 us, maximum 1510 us.
- First pass mean 648.812 us, second pass mean 622.601 us.
- Maximum unheld full four-channel cycle 172,262 us, below the 262,144 us
  programmed preamble. Mean listening visit 41,785.719 us.
- Maximum held visit 405,102 us. No IRQ trace was taken, so the exact cause
  and channel of this hold cannot be assigned from these aggregate numbers.
- Received 0, missed 1. Mode errors, cache errors, RX errors and device errors
  were all zero. Normal gain remained 0x94.

This shows that completing two real settings applications does not by itself
guarantee successful reception in this fixture. It does not prove every packet
would fail or independently identify the cause of this particular timeout.

## Implementation and verification

HIL `BenchWrapper::hop` performs the first ordinary guarded `tuneProfile`,
then waits boundedly for BUSY-low and requests a refresh of the same profile
before the second ordinary guarded transaction. This bypasses only the
same-generation no-op, not receive ownership or packet-in-progress checks.
The modulation cache is invalidated for each requested full pass.

If the second pass is blocked by newly acquired reception, the diagnostic
fails closed. Returning ordinary BUSY would wrongly imply that no channel
change occurred even though pass one had completed; it is therefore treated
as a fixture failure, not silently skipped or counted as an RF loss.

`scanretunepasses 1` is the reboot default. `scanretunepasses 2` and
`--retune-passes 2 --modulation-cache off` select this bounded test.
The standalone controller is `tools/hil/profile_switch_retune_repeat.py`.
19 native profile tests and 49 host tests passed, including real refresh,
first/second failure handling, BUSY timeout, no guard override, and rejection
of missing physical write/resume counts. `git diff --check` passed with only
unrelated existing CRLF warnings.

Receiver build/upload succeeded with hash verification: 94,908 bytes RAM,
368,389 bytes reported flash. Transmitter firmware was not changed or
reflashed. Unrelated RAK COM29 was not opened. Both boards rebooted idle after
completion, retaining HIL firmware; no autonomous TX or background collector.
Temporary PlatformIO overlay removed. No commit/push requested or performed.

## Provenance

- Manifest: `tools/hil/profile_switch_sf10_full_retune_twice_results.json`
  SHA-256 `2928955ce4b02828f3de292cdf8a18606cb56d8a5d6b65c7b50c3de1dd15f764`
- Strict capture: `tools/hil/profile_switch_sf10_full_retune_twice_results_strict.json`
  SHA-256 `322d1859716fbed0e42224f7bfbf71815dcc986df3ccccea37c1680a63723340`
- Installed RX BIN SHA-256:
  `aa7e3e96729bdb855593385a7a5ab9a22a5e186fda551db863a52a81b7cca2bb`
- Unchanged TX ZIP SHA-256:
  `915d507d0feec1712fad2bd4be62c2fa8d769d11400711de0da1cf98a50a4b36`

Manifest complete, stopped `bounded_tests_complete`; strict capture complete
under first-miss stopping. No fixture or cleanup error. Previous captures were
not rewritten. Source remains keymindCascade commit
`0e5955f889788a4317863e731b38ffe09e3d5441` plus existing dirty changes and
these HIL-only additions, not a clean-release comparison.
