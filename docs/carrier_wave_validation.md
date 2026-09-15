# CW and CW2 validation

Tested on the MercerWoodMesh lab Pi on September 14, 2026 (Pacific), using
base commit `9241ccc253ad2f8ac2c9a2fe756f59ba2f52505d` plus the CW changes.
Test images are marked `v1.17.1.6-cw-test-9241ccc2`; they are development images.

A separate RAK4631 sampled instantaneous RSSI every approximately 10 ms.
The transmitting boards ran the Full V4 repeater and Full T1000-E Companion
builds. Both were configured for −9 dBm chip TX power, with profiles at
909.5 and 919.5 MHz, BW125, SF7, CR5, and 32-symbol preambles.

| Check | V4 / SX1262 | T1000-E / LR1110 |
| --- | --- | --- |
| `cw on 0.25` measured duration | 0.25 s | 0.25 s |
| `cw on 1.25` measured duration | 1.25 s | 1.25 s |
| Manual stop approximately 1 s into a 5 s request | 1.01 s | 1.01 s |
| `cw on 1.25` after enabling RXPS | 1.26 s | 1.25 s |
| `cw2 on 1.25` on the second frequency | 1.25 s | 1.25 s |
| Primary-frequency RSSI change while CW2 transmits | 0 dB | 0 dB |
| Paired RX and TX recovery checks | 5/5 passed | 5/5 passed |

The observer saw roughly −56 to −57 dBm during V4 carriers and −67 to
−71 dBm during T1000-E carriers, against a −112 to −113 dBm baseline.
Readings returned to baseline after timeout and manual stop. These are
received signal readings, **not calibrated transmitter output measurements**.

RX recovery used CRC-valid LoRa test payloads and the DUT's receive counter.
TX recovery used each DUT's MeshCore advertisement, received with valid CRC
by the observer. Both DUTs finished in RX, with CW and radio2 disabled and
no outbound packet pending. The observer has no autonomous transmission.

## Software checks

- 1,436 native tests passed, including new checks that CW holds queued packets,
  services its deadline, and prevents transmission in the same loop where a
  received command starts CW.
- Compiled production-method tests cover fractional input, malformed durations,
  bounds, timer rollover, busy/IRQ rejection, RXPS cleanup, board TX/RX callbacks,
  profile selection and replacement, failed TX entry, stop retries, and failed
  RX restart. An ESP32 sleep test covers a carrier with USB disconnected.
- Full V4 repeater, Full T1000-E Companion, and ordinary STM32
  `RAK_3x72_companion_radio_usb` builds passed. The STM32 build is a compilation
  check, not an RF test of that board.
- An additional existing OTA sleep test still expects the old
  `ota::ota_ctx().apply_pending` spelling. It fails against unchanged HEAD
  `src/Mesh.cpp` and test-file contents; the actual implementation uses
  `ota_context_if_active()`. This is separate from the CW changes.

## SX1276 follow-up

The SX1276 carrier feature from [PR #11](https://github.com/mikecarper/MeshCore/pull/11)
is included alongside SX1262 and LR1110 support. It switches to FSK with zero
deviation, applies the board's calibrated TX power, and rebuilds the runtime
LoRa configuration when stopped. External-PA settings pass through the same
DAC/drive-power mapping used for packets, including RFO selection.

The normal TX power command can adjust a running carrier for meter sweeps.
RadioLib's SX1276 power setter enters standby, so the carrier is restarted
afterward without extending the deadline. Failed power changes stop the
carrier and restore the previous cached power.

Follow-up checks use base `9dd9881d520b4b791600a00fff1f8e1436921054` plus the
SX1276 changes. Production-method regression tests cover both profiles,
zero deviation, external-PA calibration, RFO, power sweeps, timeout, failed
FSK entry, failed restoration, retry guards, and return to LoRa. The native
CW paths and existing radio/profile/sleep/PA checks pass as well.
Both `GEPRC_Linkflow_900_repeater` (external PA) and
`Heltec_v2_companion_radio_usb` firmware builds pass, including size checks.

SX1276 RF output has not been measured in this follow-up. The physical RF
results above apply to SX1262 and LR1110.

## Bench qualifications

The first CW2 request arrived before radio2's existing delayed activation.
The harness now polls `get radio2.status` for the active mode before testing.

An initial V4 dual-profile packet probe used a 120-symbol preamble against
32-symbol profiles. A comparison around another CW2 burst received **0/8
before and 1/8 afterward** with that mismatched probe, versus **8/8 before
and 8/8 afterward** with a matching 32-symbol probe. The configured preamble
also sets the driver's preamble-detection hold timeout, so a much longer
incoming preamble can outlast that hold and be interrupted by scanning.
The final dual-profile recovery checks use matched preambles. This finding
does not establish general packet-loss performance for every scan combination.

These checks establish RF activity, selected-frequency behavior, and recovery
on the tested boards. They do not measure spectral purity, frequency error,
SWR, calibrated power, or months of uptime. Carrier timeouts are serviced by
the main loop and depend on that loop remaining responsive.

## Reproduction

Append `tools/hil/cw_observer.ini` to `extra_configs` in `platformio.local.ini`,
preserving existing entries, and build `pio run -e cw_observer_rak4631`.
Flash that application onto a separate lab RAK4631. Do not run PlatformIO
concurrently with another build or upload in the checkout.

Configure the lab DUT's primary profile as above, disable radio2, set TX power
to −9 dBm, and reboot to apply saved primary settings. Then run:

```sh
python3 tools/hil/carrier_wave.py --dut /dev/serial/by-id/DUT \
  --observer /dev/serial/by-id/OBSERVER --role repeater --output cw-results.json
```

Use `--role companion` for the T1000-E. The harness changes lab radio2/RXPS
settings and emits short RF bursts and packet probes. It is for test boards.

Compact evidence: [carrier_wave_results.json](../tools/hil/carrier_wave_results.json).
Command guide: [carrier-wave RF checks](carrier_wave.md).
