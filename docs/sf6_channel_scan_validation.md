# SF6 / 125 kHz channel-count test

Measured 2026-09-13. The run stopped on the **third packet at the initial four
channels**: two arrived intact, then the receiver timed out. No five-channel
test, lower-channel test, retry or retransmission was performed. Consequently
this run does **not** establish a maximum channel count with 100% reception.

Follow-up: [four channels at 5.1 symbols with an IRQ trace](sf6_5p1_trace_validation.md)
stopped at packet 8 and observed no preamble acquisition during the miss.

## Requested setup

- Receiver: XIAO ESP32-S3 + WIO SX1262, COM31.
- Reference transmitter: SenseCAP Indicator ESP32-S3 LoRa processor, COM28.
- SF6 / 125 kHz / CR4/5, fixed **32-symbol programmed preamble**.
- **4.8-symbol receive visits**: 2,457.6 us, rounded up to 2,458 us.
- Buffered 8 MHz SPI and the production fast retune/packet-ownership guards.
- Start at four channels and add one only after 100/100 packets on every channel.
- Stop at the first missing/corrupt packet; USB/reference/scanner faults are
  reported separately as inconclusive fixture errors.
- 16-byte synthetic payloads at -9 dBm. No identity/channel secrets transmitted.
- Four channel centers: 909.500, 909.750, 910.000 and 910.250 MHz.
- Balanced randomized channel order, seed 606125; varied arrival timing across
  approximately two idle scan cycles. Arming an expectation does not select
  the receiver's channel or reset its visit clock.

The 100-packet-per-channel target was **not reached**: the explicit first-miss
stop rule ended the run during its first shuffled pass.

## Observations

| Probe | Channel center | Outcome |
| --- | --- | --- |
| 1 | 910.250 MHz | Valid 16 bytes, RSSI -40 dBm, SNR 10.8 dB |
| 2 | 910.000 MHz | Valid 16 bytes, RSSI -40 dBm, SNR 11.0 dB |
| 3 | 909.750 MHz | Receiver timeout; transmitter returned success |

All three transmit acknowledgements and receiver reports matched their
sequence IDs, with no missing/replayed/stale USB replies. On the miss, the
receiver reported RX mode and zero device errors. The scanner recorded zero
retune failures, RX-mode errors, modulation-cache errors or CRC/read errors.
No probe was sent on 909.500 MHz before the stop condition.

| Timing | Minimum | Mean | Maximum |
| --- | --- | --- | --- |
| Production retune to BUSY low, 3,467 hops | 504 us | **514.652 us** | 726 us |
| Idle receive dwell, 3,463 visits | 2,458 us | **2,462.480 us** | 2,468 us |

These samples include scanning during the bounded 10-second wait that
confirmed the missing packet. There were 2,451 deferred *attempts* while
production preamble/header/packet ownership held a visit; this is not a count
of packets or failed retunes. Held visits are excluded from idle-dwell timing.
All 3,467 successful retunes used the production fast-RX path.

The means imply about **11.91 ms per four-channel idle cycle**, excluding
packet-held visits and small loop bookkeeping. Fast retuning alone did not
produce 100% reception in this setup. This short stop-on-first-miss experiment
does not identify the exact acquisition/phase cause, estimate a stable PER,
prove that three channels would pass, or establish a universal board limit.

## Implementation and reproduction

This is a lab-only N-channel scheduler over the real `tuneProfile()` path.
It reuses the two production profile slots as staging slots for the next
frequency. Normal MeshCore `radio2` remains two-profile. The explicit test
schedule intentionally does not use the normal two-profile automatic-preamble
fit calculation or generic 6 ms switch / 4 ms loop allowances. It keeps the
existing packet-protection guards and measures real dwell/retune overhead.

```text
python tools/hil/profile_switch_channels.py --receiver COM31 --sender COM28 --output results.json --samples 100 --preamble 32
```

The controller has regression tests for balanced samples, starting at four,
incrementing only after a perfect level, stopping without another TX, and
distinguishing fixture failure from an RF miss. A separate RX-only 0.5-second
setup check verified the scan timing before any capacity-test packets were
sent. The capacity run itself was not repeated.

- [Raw result, including the failing probe](../tools/hil/sf6_125_channel_capacity_results.json)
- [Host controller](../tools/hil/profile_switch_channels.py)
- [Firmware scheduler](../tools/hil/profile_switch_channels.h)
- [Harness instructions](../tools/hil/profile_switch_README.md#sf6125-khz-channel-count-limit)
- [Earlier USB and fast-switch qualification](radio_profile_switch_validation.md#production-integration-and-usb-recovery-v8)

Build provenance: base commit `c329cf1fc1e7f2e8c8733ab0d000094730458fe3`
plus the local fast-switch and HIL changes; Arduino ESP32 2.0.17,
Espressif32 6.11.0, RadioLib 7.7.1 pinned at
`187ef24791c3d844939b2be13a68bd890bd04e4c`. This is the extended V8 harness
with `channel_sweep: 1`; its binaries differ from the earlier two-profile
V8 measurements, so timings should not be treated as a same-image A/B.

Firmware SHA-256:

- XIAO: `b0a6dd216be8a638fe088219801290d4632afcf63048f8597037650d68cd2301`
- Indicator: `f0aa43a46e5e59ebf4768a255bd8c31d7a72c0038e11173314f78908b725786d`

## Device handoff

After the first-miss stop, both original 8 MiB flash snapshots were restored.
Separate full-flash verification matched each backup's digest, followed by
hardware reset. The private backups remain in the current-user backup
directory. The Indicator RP2040 and RAK4631 were not modified. The interrupted
XIAO soak logger remains stopped, and the temporary PlatformIO HIL overlay was
removed. Implementation, tests and reports were initially handed off locally
before publication.
