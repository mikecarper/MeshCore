# SX1262 channel-scanning receiver experiment

Standalone diagnostic firmware for a XIAO ESP32-S3 with the Wio SX1262 module
and a Heltec V4. It does not modify MeshCore preferences, start Wi-Fi/Bluetooth,
or transmit automatically. One transceiver visits channels sequentially; it
cannot receive four simultaneous packets.

## Radio configuration

- Up to 12 channels: 909.5 through 920.5 MHz in 1 MHz steps; default four.
- SF5, 125 kHz, coding rate 4/5, explicit header, payload CRC.
- 32 programmed preamble symbols; private LoRa sync word `0x12`.
- Configurable CAD symbol count; detection peak 18 and minimum 10.
  These thresholds are the datasheet's four-symbol SF5 settings. The
  one-symbol comparison retains them and includes a single-channel control;
  it does not establish optimal one-symbol detection thresholds.
- TCXO 1.8 V with the repository's 1600 microsecond startup allowance.
- TX power 0 dBm at the SX1262, GC1109 bypass on the tested V4.
- No transmit queue, mesh forwarding, or packet retransmission.

## Build

From the repository root:

```text
pio run -d tools/hil/cad_scan
```

Run only one PlatformIO process at a time. The two environments are built
sequentially by this command. The V4 environment uses the generic XIAO S3
Arduino board recipe with explicitly supplied V4 radio GPIOs; this is not a
production V4 firmware recipe and does not drive its display.

For the recorded test, both boards already had app0 active at `0x10000`.
Only that app partition was replaced, then restored with the saved soak image.
Do not assume that address or active slot for another installation. Stop the
existing serial owner before flashing or opening a port; opening ESP32 native
USB serial can reset the board.

## Serial commands (115200 baud)

| Command | Action |
| --- | --- |
| `idle` | Stop scanning and enter standby. |
| `warm 1` | Use oscillator-ready standby between operations. |
| `warm 0` | Use RC standby between operations. |
| `channels 12` | Scan the first 12 channels; accepts 1–12 and defaults to 4. |
| `fixed 0` | Receive continuously on channel 0; available indices are 0–11. |
| `scan` | CAD, then software starts RX when activity is detected. Symbol count defaults to 4. |
| `cad-auto` | CAD with the hardware CAD-to-RX handoff, using the current CAD symbol count. |
| `cad 1` | Set CAD to 1 symbol and scan with automatic RX handoff; accepts 1, 2, 4, 8, or 16. |
| `rxscan 4` | Use RadioLib's full receive setup on every channel and watch for preamble for four symbols. |
| `rxfast 4` | Configure reception once, then retune and re-arm RX with minimal commands; watch for preamble for four symbols. |
| `tx 0 123 255` | Send one test packet: channel 0, sequence 123, 255 payload bytes. No automatic repeats. |
| `stats` | Print cumulative counts and minimum/mean/maximum microsecond timings. |
| `mixed 9` | Select two profiles: channel 0 SF7/62.5 kHz and channel 1 SF9/500 kHz. Accepts 7, 8, or 9 for channel 1. |
| `preamble500 64` | Set the mixed-mode channel 1 preamble from 32 through 128 symbols in steps of 16, or use 40 for the extra SF8 trial; channel 0 stays at 32. Apply on transmitter and receiver. |

`rxscan` and `rxfast` accept windows from 1 through 16 symbols. Configure
`warm 1` before testing these modes. A preamble detection keeps the receiver
on that frequency through the packet, with a bounded 250 ms wait in uniform
SF5 mode. Mixed mode allows the active profile's maximum packet airtime plus
100 ms. Observation windows use each profile's own symbol duration. CRC and
test payload validation are required to count a successful reception.

`stats.cad` measures the CAD operation in CAD modes; in RX modes it measures
receive setup plus the observation window. `visit` includes tuning. `sweep`
measures uninterrupted visits to all configured channels, not periods occupied by
packet reception. Serial commands can add small outliers to sweep timing.
`false_hits` means activity/preamble detected without a decoded packet before
the wait expired; it includes late packet detection, not just noise.

## Measurement limits

This is a strong-signal, one-transmitter lab comparison, not an FCC compliance
test or proof of weak-signal range. CAD can detect payload symbols too; activity
detection alone is not packet reception. The tests count decoded, CRC-valid,
sequence-matched packets. A packet on another frequency during an ongoing
reception can be missed. These results do not establish production MeshCore
reliability, coexistence performance, power consumption, or months of uptime.

The private lab orchestration and raw logs live under ignored `out/cad-scan/`.
The results report identifies the trials whose TX acknowledgements matched
their requested sequence numbers; the exploratory `rxscan-4` trial with
misaligned acknowledgements is excluded from delivery-rate comparisons.

The measured results are in [the hardware report](../../../docs/cad_scan_validation.md).
The two-channel SF7/62.5 kHz plus 500 kHz tests are in
[the mixed-profile report](../../../docs/mixed_scan_validation.md).
CAD configuration and timing references are in the
[Semtech SX1261/2 datasheet](https://resource.heltec.cn/download/HT-N5262M/Semtech-SX1262_datasheet.pdf),
sections 6.1.4, 6.1.5, and 13.4.7.
