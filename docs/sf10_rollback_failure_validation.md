# SF10/125 rollback and wider-spacing failure isolation

2026-09-14. **No strict four-channel 400/400 pass. Reverting the switching
optimizations did not remove failures. Off-channel reception also occurs with
a fixed, fully initialized receiver, without any channel switching.** A traced
timeout shows an off-channel preamble holding the scanner away from the
intended channel until after its transmit preamble. Wider spacing did not
eliminate either wrong-channel delivery or actual missing packets.

This isolates a concrete scanner failure mechanism, not the underlying
physical RF cause. Commanded frequency words are observed, but actual emitted
spectrum, antenna-port power, image responses and overload have not been
independently measured. Do not claim front-end saturation or a specific spur
as proven. Do not treat these first-failure-censored samples as unbiased PER.

## Fixture and requested controls

- RX: XIAO ESP32-S3 / Wio SX1262, COM31, MAC 28:84:85:B4:09:80; normal RX gain
  register 0x94 throughout (boosted gain off).
- TX: XIAO nRF52840 / Wio SX1262, COM15, serial B35E71C1C3726CE7.
- SF10, 125 kHz BW, CR4/5, 32-symbol preamble, 16-byte sequenced CHS1 probes,
  -9 dBm chip request, four channels, 5.1-symbol post-settle listening visits.
- Original centers: 909.5, 909.75, 910.0, 910.25 MHz. User-requested wider
  centers: 909.5, 910.5, 911.5, 912.5 MHz (1 MHz spacing).
- Existing fixed-channel positive control was 100/100; see
  `sf10_nrf52_double_write_settling_validation.md`. No failed packets retried.
- Unrelated RAK COM29 was not opened. New nRF52 upload was application-only,
  using normal SoftDevice ID 0x0123 validation; no bootloader/SoftDevice erase.

Rollback modes are runtime HIL controls on the same current source, not
historical firmware checkouts. No production default or receive safety guard
was changed during this investigation. Tests preserve ownership, packet-in-
progress, BUSY and command-result checks. Existing unrelated working-tree
changes were left intact. No commit or push was requested this turn.

## Maximum practical settling control

At SF10/125, a symbol is 8,192 us, the programmed preamble is 262,144 us,
and each 5.1-symbol post-settle visit is 41,780 us. For each rollback mode:

1. Measure six seconds of RX-only switching without added delay.
2. Choose the largest 100 us delay step satisfying
   `4 * (41780 + observed_max_switch_us + added_delay_us + 500) <= 262144`.
3. Measure another six seconds; require at least eight full idle cycles and
   maximum observed idle cycle below the preamble before allowing TX.

The 500 us per-hop reserve covers some observed scheduling variation, not
all possible jitter. RX is active during added settling. Near-ceiling cycle
timing is not itself a guarantee of acquisition for every arrival phase,
especially when an IRQ causes an extended packet hold. Idle-cycle statistics
exclude guard/packet-held visits; those are measured separately.

## 250 kHz spacing: eleven rollback cases

Each case stopped on its **first probe**, TX channel 3, with a real timeout.
No strict 400/400 pass, TX fault, RX mode/cache failure, or hardware error.
The first nine cases used the checkout's existing 1,600 us TCXO setting;
the final two explicitly restored 6,000 us. Production timing was not edited.

| Case | Added delay (us) | Zero-delay mean switch (us) | Max settled idle cycle (us) |
| --- | ---: | ---: | ---: |
| Fast, cached modulation, double frequency write | 22500 | 535.88 | 259297 |
| Always write modulation | 22500 | 625.50 | 259682 |
| Individual SF/BW/CR writes | 22200 | 840.97 | 259346 |
| Full normal RX restart | 22200 | 896.89 | 259577 |
| Full RX + individual modulation | 22000 | 1113.95 | 259623 |
| Above + byte-wise SPI, warm XOSC | 21800 | 1215.79 | 259220 |
| Above + RC standby, 1.6 ms TCXO | 20100 | 2942.50 | 259339 |
| Fast, cached modulation, single frequency write | 22600 | 450.25 | 259341 |
| RC/full RX/individual/byte-wise, single write, 1.6 ms TCXO | 20200 | 2837.50 | 259329 |
| RC/full RX/individual/byte-wise, double write, 6 ms TCXO | 15800 | 7293.25 | 259550 |
| RC/full RX/individual/byte-wise, single write, 6 ms TCXO | 15800 | 7190.31 | 259139 |

Actual frequency/modulation command counts and fast-RX counts verified each
policy. Slower modes received less added delay to preserve the total sweep
budget. These are eleven probes with eleven timeouts, **not eleven complete
400-packet runs**. Packet arrival pauses use the same seeded scheduling
method but not hardware-synchronized identical RF phases across modes.

## Fixed-frequency controls: off-channel responses without switching

Each pair received three independent probes after full normal initialization.
Positive controls were interleaved. The receiver remained at one frequency:
zero observed frequency commands after setup, no hopping or cache shortcut.
Payload success requires the exact expected sequence/channel/body, length 16
and clean CRC. It does not require RX center to equal TX center in this
explicit offset diagnostic. Offset below means programmed RX minus TX.

| Spacing | RX-TX offset (kHz) | Probes | Intact expected payloads | Preamble IRQ seen |
| --- | ---: | ---: | ---: | ---: |
| 250 kHz | 0 | 6 | 6 | 6 |
| 250 kHz | -250 | 3 | 2 | 3 |
| 250 kHz | -500 | 3 | 0 | 3 |
| 250 kHz | -750 | 3 | 0 | 3 |
| 250 kHz | +250 | 3 | 0 | 2 |
| 1 MHz | 0 | 6 | 6 | 6 |
| 1 MHz | -1000 | 3 | 2 | 3 |
| 1 MHz | -2000 | 3 | 0 | 0 |
| 1 MHz | -3000 | 3 | 3 | 3 |
| 1 MHz | +1000 | 3 | 3 | 3 |

At 250 kHz spacing, the two valid -250 kHz-offset packets reported RSSI
-91/-90 dBm and SNR -12.8/-10.8 dB. At 1 MHz spacing, valid off-channel
packets reported RSSI -102..-109 dBm and SNR -11.5..-15.8 dB. Wider on-channel
controls were all -33 dBm, SNR +8.0..+8.8 dB. These are radio-reported values,
not calibrated measurements of transmitted spurs or receiver rejection.

The non-monotonic offset pattern means that increasing spacing alone cannot
be assumed to remove this response. These fixed-frequency tests rule out a
channel-hop shortcut as a necessary condition for the observed off-channel
packets; they do not establish which physical component produces them.

## A traced real timeout at 250 kHz spacing

Fast/double-write RX, +22,500 us settling. One channel-3 probe timed out with
TX rc=0, RX mode=1 and device errors=0. The trace contains 454 events, overflow
0. Trace adds 64 us IRQ polling; use it for ordering, not an unperturbed timing
A/B comparison. Times below are relative to receiver expectation arming.

| RX time (ms) | Evidence |
| ---: | --- |
| 169.190 | Frequency command for channel 3, 910.25 MHz |
| 234.032 | Leaves channel 3 for channel 0 |
| 298.869 | Frequency command for channel 1, 909.75 MHz |
| 330.030 | PREAMBLE_DETECTED (0x0004) while on channel 1 |
| 363.632 | Requested next hop is held by receive-in-progress guard |
| 726.734 | Guard clears the lingering preamble IRQ |
| 726.826 | Frequency command for channel 2 |
| 791.654 | Finally commands intended channel 3 again |
| 814.586 | Channel-3 hop/settling completes |

TX setup was 86,914 us and its transmit call 529,297 us. Host/firmware timing
places TX RF start approximately 287..289 ms after RX arming and the end of
its programmed preamble around 550 ms. This estimate is not a synchronized
RF measurement, but the receiver's several-hundred-millisecond absence from
the intended channel is much larger than USB timestamp uncertainty.

The held channel-1 visit measured **427,395 us**, versus normal idle four-
channel cycle maximum **259,358 us**. The guard timeout is consistent with
`RadioLibWrapper::calcMaxPacketMillis`: `(32 + 8 + 4.25) * 8192 = 362496 us`,
rounded to 363 ms. `CustomSX1262::isReceiving()` starts that timer when the
guard first observes the preamble, here later than the diagnostic IRQ poll.

Thus an off-channel preamble can monopolize the scanner long enough to miss
the real channel. Do not simply remove or shorten this safety guard as a
production fix: it also protects legitimate weak or long-preamble reception.

## 1 MHz spacing: strict results versus payload delivery

Three strict rollback cases (fast double, fast single, fully rolled back
single/6 ms) each stopped at their first probe. All three actually decoded
the intact channel-3 payload while programmed for channel 2: RX 911.5 MHz,
TX 912.5 MHz, RF word at read 955777024. They are **channel mismatches, not
lost payloads**. RSSI was -102/-101/-101 dBm; SNR -11.5/-11.5/-11.2 dB.

An explicitly separate payload-delivery diagnostic then continued through
such packets while retaining strict validity in raw evidence:

| Mode | Added delay | Delivered before stop | Strict same-channel | Stop | Mean switch | Max idle cycle |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| Fast, cached, double write | 22.5 ms | 3 of 4 probes | 0 | TX channel 0 timed out | 557.721 us | 259381 us |
| RC/full RX/individual/byte-wise, single write, 6 ms TCXO | 15.8 ms | 2 of 3 probes | 0 | TX channel 1 timed out | 7310.510 us | 259643 us |

Every delivered packet in these two runs arrived one programmed channel below
its transmitter. Both runs had zero mode/cache/RX/device errors and actual
switch-policy counts matched. Neither is a 400/400 pass, even under the relaxed
delivery-only criterion. The true timeout in the fully rolled-back path also
shows the problem is not limited to the accelerated RX restart or 0x8B cache.
No traces were enabled for these two runs, so do not assign their individual
timeouts the exact IRQ timeline from the earlier traced probe.

## Changes and verification

- HIL-only rollback, explicit TCXO timing, selectable 250/1000 kHz spacing,
  frequency-command trace events and fixed-frequency offset controls.
- Separate opt-in payload-delivery test; strict wire validity remains intact.
  Host/device counters distinguish strict from off-channel deliveries;
  settling sweeps reject delivery-only captures, including reused evidence.
- 16 native profile tests and 46 HIL host tests passed. Receiver and nRF52
  builds/uploads succeeded with normal verification. Receiver reported
  94,844 bytes RAM / 366,777 bytes flash; nRF52 18,220 / 95,572 bytes.
- `git diff --check` passed; unrelated pre-existing CRLF warnings only.
- Temporary PlatformIO overlay removed. Controllers stopped, both boards
  rebooted idle with HIL firmware retained. No autonomous TX or restoration.

Next useful isolation is the physical off-channel response (controlled RF
attenuation or reversed radio roles), followed by safe acquisition/hold-policy
experiments. More settling delay alone does not address the demonstrated
false-hold mechanism. No claim of an identified silicon defect or verified
production fix is made here.

## Evidence provenance (SHA-256)

Raw JSON files below are under `tools/hil/`; prior captures were not rewritten.
Matrix manifests include per-case paths and timing controls. All listed new
experiments completed under their stated stopping policies without fixture or
cleanup errors. Completed first-failure runs still have incomplete 400-packet
levels by design.

| Capture | SHA-256 |
| --- | --- |
| profile_switch_sf10_rollback_results.json | `e87c504b3c89fbafe90c18fcd0ab37df34a6aeb51d95b5c69f58d60f70de185a` |
| profile_stationary_sf10_offset_results.json | `d3d87315513a70fc3f00cc7f17369043328f53ee3028d499087b21e767370ab7` |
| profile_switch_sf10_rollback_fast_trace_results.json | `8ac9db50f0f06eb7511d7d233957119618df62afdc7112dd83eb68d292fcd725` |
| profile_switch_sf10_rollback_6ms_results.json | `74e7bfeb23bc569a5786e2322b5e3b1753cba9f628e2969bf4f5c4296ca5817a` |
| profile_switch_sf10_wide_rollback_results.json | `d79096fbe3e15e0375457b487fd40be3418789e798a978efb79d8298c880eea2` |
| profile_switch_sf10_wide_payload_results.json | `1316a2c2f32a8c0773fc56587c4220ee8f6f6cdf4ab9b87e00fd361fc9395257` |
| profile_switch_sf10_wide_legacy_payload_results.json | `fcf2123270634e2f27800787e20b9b8b7ae63bcfb03e17d26a4a4f6f4e23d32f` |
| profile_stationary_sf10_wide_offset_results.json | `4c494a59fa793d274b0366185cfe3230b6c9fce7a57dfaa61f9f29c36465bbd3` |

RX firmware images used, in chronological order:

| Stage | firmware.bin SHA-256 |
| --- | --- |
| Initial nine-mode rollback | `5f8dbf9c4584e0de94a7aebda5a20e961b644a880ecb6a34ab5871621958696f` |
| Fixed 250 kHz offsets and traced timeout | `cd839caa33c869dd448cc55e9c8b959e39d105f80a2ac4a8029393698b993fd3` |
| Explicit 6 ms TCXO rollback | `0ba044035c4986749c9ad4f50c702dc8c57e8e0fecf9d46fc11515cad5678166` |
| Wider strict rollback | `5fc919c80438635b4a96021b39fae2ada9e767508d7bb73854e4e108f0d4920f` |
| Wider delivery diagnostics and fixed offsets; final installed | `7b313a0f8f1eb04f6fc12197af5e9481ad43154b981ab7627e50167d2a97d8c2` |

The 250 kHz tests used the prior nRF52 TX ZIP hash
`7466625ef5e7bcc8f7dd16db2b843db29b4312a33b0d32bc0b1e1193d6aee03a`.
All wider tests used the new selectable-spacing application:

- TX ZIP: `c474b7598a2c0b6c529ead897f3fc7aab8caf4b7581c73d7acac6ffae081377d`
- TX HEX: `b400fc53c496f4a956f88ccee5cda83b5f6679b8e2b59bfcb4b8155ea9e1e5e9`

Build artifacts are generated and may be replaced by a subsequent build;
hashes identify the images used, not a promise that every historical binary
remains in `.pio/build`. Source starts from keymindCascade commit
`0e5955f889788a4317863e731b38ffe09e3d5441` with the existing dirty changes and
these uncommitted HIL additions; this is not a clean-release comparison.
