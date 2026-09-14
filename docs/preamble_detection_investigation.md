# Off-channel preamble investigation

Measured 2026-09-14. **The SF8 off-channel preamble indication reproduces with
a stationary, fully initialized receiver, without profile hopping or fast RX
reuse.** Both boards reproduce it when their transmitter/receiver roles are
reversed. This is evidence of transmitter-associated off-channel detection in
this close-range bench setup, not evidence that the channel jump needs more
settling time.

The earlier SF8 scan's long software guard explains its missed listening
opportunity. The earlier SF6 scan miss remains unresolved: its trace had no
preamble indication or guard hold. No production guard or acquisition policy
was changed during this investigation.

## Controlled experiment

- XIAO ESP32-S3 + WIO SX1262 (COM31) and SenseCAP Indicator ESP32-S3 LoRa
  (COM28). The Indicator RP2040 and RAK4631 were not modified.
- Receiver fixed at 910.000 MHz. Transmitter silent, on frequency, or offset
  by +/-250, +/-500 or +/-1,000 kHz. These are offsets, not bandwidths.
- 125 kHz bandwidth, SF8 or SF6, CR4/5, 32 programmed preamble symbols,
  -9 dBm, one 16-byte sequence-tagged synthetic payload per transmitting case.
- Each case fully initializes both radios. RX uses ordinary `startReceive()`;
  profile-switch optimization and experimental omission masks are disabled.
  Buffered 8 MHz SPI remains enabled. No hops, warm-retune reuse, CAD, or
  `isReceiving()` software guard runs during these controls.
- TX is prepared before RX is armed. Each receive window lasts 450 ms;
  all eight cases are shuffled within each balanced round, seed 814910.
  The full RX window completes even when an off-frequency packet is not
  received, as expected. An on-frequency positive-control failure stops the
  experiment. No RF retransmissions are used to repair a result.
- The HAL records actual outgoing `SetRfFrequency` bytes on both boards,
  and the frequency word present at `SetTx`. The host verifies these words
  against the requested frequencies. This checks commands sent to the chip,
  not an independent measurement of the emitted RF spectrum.
- Existing IRQ replies are observed and IRQs additionally polled at a nominal
  64 us interval. Events are buffered in RAM; timestamps are software
  observations, not synchronized RF arrival times. Unknown payload contents
  are not exported.

The three completed matrices contain **128 windows: 112 transmitted probes
and 16 silent controls**. A separate initial 450 ms receiver-only smoke check
was also performed. These are bounded diagnostic controls, not another
100-packets-per-channel capacity sweep.

## Results

Entries below are windows with a preamble indication / tested windows. Each
positive window reported one indication; latched IRQs do not count continuous
detector activity or prove a valid packet.

| Transmitter offset | SF8, XIAO RX | SF8, Indicator RX | SF6, XIAO RX |
| --- | ---: | ---: | ---: |
| Silent | 0/8 | 0/4 | 0/4 |
| On frequency | 8/8 | 4/4 | 4/4 |
| -250 kHz | 8/8 | 4/4 | 1/4 |
| +250 kHz | 8/8 | 4/4 | 0/4 |
| -500 kHz | 5/8 | 3/4 | 0/4 |
| +500 kHz | 1/8 | 3/4 | 0/4 |
| -1,000 kHz | 5/8 | 4/4 | 0/4 |
| +1,000 kHz | 5/8 | 3/4 | 0/4 |

All **16 on-frequency probes delivered valid payloads**. None of the 96
off-frequency probes delivered a valid payload. No silent control detected a
preamble. Completed windows ended in RX mode with zero device errors; there
were no foreign valid payloads or trace overflows.

Most off-frequency detections stopped at `PreambleDetected` (0x0004). Four
progressed further but failed CRC (`seen_irq=0x0056`: preamble, header, RxDone,
CRC error): two at +250 kHz with XIAO RX, and one each at +250 and +1,000 kHz
with Indicator RX. Therefore **HeaderValid alone would not prove that these
off-frequency indications are legitimate traffic**.

In forward SF8 on-frequency controls, instantaneous RSSI at the first preamble
was -42 to -41 dBm; at +/-250 kHz it was -91 to -88 dBm. These are instantaneous
receiver readings, not calibrated measurements of transmitted leakage or a
sensitivity test. Board positions and attenuation were not systematically
varied. Reversing roles reproduces the behavior but does not isolate its
physical path.

## What this explains

The [earlier SF8 four-channel scan](sf8_5p1_trace_validation.md) missed probe
13 on 910.250 MHz after detecting a bare preamble while tuned to 910.000 MHz.
The existing `CustomSX1262::isReceiving()` guard allowed 91 ms for preamble
progression, consistent with the observed 92.257 ms from first preamble
observation to the stale-bit clear. That produced approximately 102 ms total
occupancy on the wrong channel and a 135.145 ms target-channel revisit gap.
The programmed 32-symbol preamble lasted only 65.536 ms.

The delayed retune itself took 550 us. These controls reproduce the suspect
off-frequency indication with no retunes at all. Together the evidence
supports **off-channel acquisition plus a long guard hold** as the SF8
failure mechanism, rather than slow SPI or a demonstrated need for a longer
post-jump delay. It does not independently prove the RF source of that one
historical scan event.

The controlled-transmitter association and quiet silent controls make random
ambient RF alone an inadequate explanation of the repeated bench result.
Exact RF cause remains unmeasured: receiver selectivity/false acquisition,
transmitter spectral leakage, and coupling between the nearby boards are
not separated by these tests. No spectrum-analyzer capture or calibrated
attenuation sweep was performed. Increasing channel spacing is not established
as a fix: SF8 indications also occurred at +/-1 MHz.

The [earlier SF6 miss](sf6_5p1_trace_validation.md) had no observed nonzero IRQ
or guard hold. The four successful stationary SF6 positive controls show the
fixed-channel link works, but do not establish the acquisition time needed
after a retune. These small, differently sized samples are not packet-error
rate estimates or proof of a zero-loss channel count.

## Follow-up candidates, not implemented fixes

Semtech documents that false LoRa detections can occur and describes
`SetLoRaSymbNumTimeout` as validating reception over multiple symbols before
continuing when the modem locks. This is a candidate to investigate, not a
drop-in verified fix. Its behavior in continuous RX and through each retune
must be measured. See section 13.4.9, page 99 of the
[Semtech SX1261/2 datasheet, revision 2.2](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/datasheet/SX1261_2%20V2-2.pdf).

Useful next experiments would qualify/revalidate preambles before granting a
long scan hold, and repeat the controls with lower signal levels or increased
physical isolation. Any candidate must preserve legitimate weak signals and
long preambles. Blindly shortening the current guard, or requiring only a
header indication, is not justified by these results. No production radio
change was made in this diagnostic turn.

## Reproduction, provenance, and checks

See the [stationary diagnostic instructions](../tools/hil/profile_switch_README.md#stationary-off-channel-preamble-controls).
The same diagnostic firmware binaries were used for all three matrices.
Base commit `c329cf1fc1e7f2e8c8733ab0d000094730458fe3` plus local changes;
RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`, Espressif32
6.11.0 / Arduino ESP32 2.0.17. Both HIL targets built and uploaded successfully.
All **29 selected host/native tests passed** after testing.

Raw results and SHA-256:

- [Forward SF8, eight rounds](../tools/hil/preamble_stationary_sf8_results.json):
  `e876a8b1727c5584aa67cb4fcdcae5ca30701e90f7ed998b5df36c37d245f7b0`
- [Reverse SF8, four rounds](../tools/hil/preamble_stationary_sf8_reverse_results.json):
  `83b576544f807c6b81ee83a7db487137ef2584cb4981e475c7e7ac50e4fa2363`
- [Forward SF6, four rounds](../tools/hil/preamble_stationary_sf6_run2_results.json):
  `bdb65b4e23d8b05c8523cec092533509021f878518a38523b02135b810da991b`
- [Aborted initial SF6 launch](../tools/hil/preamble_stationary_sf6_results.json)
  is retained: no trials and no RF probes. A host reboot-command quoting
  failure left the diagnostic image unready after the preceding run. After
  correcting the command and rebooting both boards, the complete SF6 matrix
  was recorded to the separate `run2` path. A subsequent host cleanup change
  ensures an already-opened receiver port is closed if opening TX fails.

Tested firmware SHA-256:

- XIAO: `f89da2a2e542df481ff6c26d2492dcc46db8794b5549d3b4f73217767130a21d`
- Indicator: `e58207c7e63de625bee2d4e4400eb934b2737f51e8babc7c1fddd85c48efdda6`

## Device handoff

Fresh full 8 MiB backups were taken and separately verified before flashing,
in the private current-user MeshCore backup directory. No backup contents
or identity/channel secrets are in the diagnostic results.

- XIAO backup: `5e5825b625a0e6ded65aafa72658eb3b15b466316a9efe8239b2f09de9418e63`
- Indicator backup: `6f834296d4e29e4fe17297aad0a9e2bf2adcf0a1391cf645de1bc51aebfd70de`

Both original images were restored after the controls, separately verified
against the complete 8 MiB flash with matching digests, and hardware-reset.
The RAK4631 and Indicator RP2040 were untouched; the XIAO soak logger was not
restarted. The temporary HIL build overlay was removed. Investigation source,
tests, and results were initially handed off locally before publication.
