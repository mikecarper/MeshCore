# Carrier-wave RF checks

`cw` and `cw2` transmit a continuous, unmodulated carrier at the selected
profile's frequency and the configured TX power. They are available in the
shared ASCII CLI on SX1276-family, SX1262-family, and LR1110 builds, including repeaters,
Companions, room servers, and sensors that use that CLI.

| Command | Action |
| --- | --- |
| `cw on` | Use the active primary frequency for 10 seconds. |
| `cw on 2.5` | Use the active primary frequency for 2.5 seconds. |
| `cw2 on 0.75` | Use the active second profile for 0.75 seconds. |
| `cw off` or `cw2 off` | Stop the carrier immediately. Either name stops it. |
| `get cw` or `get cw2` | Show which carrier is active and its remaining seconds. |

`set cw ...` and `set cw2 ...` are also accepted. Durations range from
0.001 to 60 seconds, rounded to milliseconds. The deadline runs in the main
loop, so it is not a precision pulse generator or a hardware cutoff.

`cw2` requires an active `radio2` or `tempradio2` profile in `rxtx` mode.
An `rx` or disabled second profile cannot transmit a carrier. `cw` uses
`tempradio` when that is the current primary profile. There is one physical
transmitter: a carrier uses one profile at a time and ignores the cross-TX
setting. Stop it before selecting the other carrier. Repeating `on` on the
same profile restarts the duration.

Packet transmission, reception, profile scanning, CAD, and receive recovery
checks pause during the carrier. The firmware keeps the MCU awake to service
the deadline, controls the board's external amplifier, and resumes normal
reception and profile scanning afterward. It rejects entry during a packet
or a busy radio. Profile retuning waits until the carrier stops; replacement
or expiry of the active second profile stops its carrier. Nothing about CW
is saved across reboot.

The normal `set tx <dBm>` command also works during CW for meter sweeps. It
keeps the board's power limits and PA calibration, resumes the carrier after
changing power, and does not extend the CW timeout. TX power retains its usual
saved behavior. SX1276 temporarily uses FSK with zero frequency deviation,
then rebuilds the LoRa configuration before resuming reception.

Use the local USB or BLE console for immediate replies and manual stopping.
An on-air CLI reply must wait until the carrier ends because LoRa packets
cannot transmit during the test.

A second radio on the same frequency can detect the carrier using
instantaneous RSSI. Compare readings before, during, and after transmission;
CAD and packet counters are not carrier detectors. This confirms RF activity,
not calibrated output power, frequency accuracy, antenna match, or SWR.
Afterward, exchange ordinary LoRa packets to verify both RX and TX recovery.

The reproducible bench witness is `tools/hil/cw_observer.cpp`, built with
`tools/hil/cw_observer.ini` on a separate RAK4631. It never transmits on its own.
See the [hardware validation](carrier_wave_validation.md) for measured results,
reproduction steps, and the tested preamble settings.
