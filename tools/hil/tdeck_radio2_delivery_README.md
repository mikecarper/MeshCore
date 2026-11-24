# T-Deck Packet Delivery During Redraw

Status: implemented and host-tested, **not hardware-tested**. Embedded build and
any explicit flashing are separate owner-controlled steps. The collector never
builds, flashes, resets, reboots, changes saved settings, or retries RF sends.

This extends `tdeck_display_switch.cpp`, using its production radio wrapper,
automatic two-profile scheduler and display paths. The T-Deck remains RX-only.
An existing production Full Companion sends a real zero-hop RAW_CUSTOM packet;
another production Full Companion independently receives its exact payload.
The T-Deck captures the complete wire packet and actual receive profile while
the selected redraw workload is active. It does not pin the scheduler for a send.

## Fixture

Use three separately identified USB devices and an appropriately isolated RF
fixture. Verify local frequency permission and attenuation/antenna arrangements
before authorizing TX. A chip power readback is not an EIRP or external-PA claim.

| Device | Required state before invoking the collector |
| --- | --- |
| T-Deck | Updated `tdeck_display_switch` image, PSRAM available for mode 2 |
| Sender | Production Full Companion, stationary primary on the selected target tuple; radio2 off; `radio.mode tx` or `both`; applied TX power -9..0 dBm |
| Witness | Production Full Companion, primary 909.5/62.5/SF7/CR5, secondary 910.5/500/SF8/CR5 in `rx`, `radio.mode rx`; both explicit preamble 32 |

Both Companions need explicit primary preamble 32, RX power saving off, repeat
off, radio2 crossing off, no temporary profiles and no saved/temporary primary or
secondary schedules. Preconfigure these separately; no setter is sent by this
collector. Sender/witness use the existing Binary Companion startup, local CLI,
CMD_SEND_RAW_PACKET (65) and PUSH_RAW_DATA (132) mechanisms.

The source primary tuple for `--profile 0` is 909.5 MHz / BW62.5 / SF7 / CR5 /
preamble32. For `--profile 1` it is 910.5 MHz / BW500 / SF8 / CR5 / preamble32.
The witness and T-Deck always keep the full pair. Run each target separately,
after separately preparing the sender. No automatic source retuning occurs.

The witness must have radio2 enabled in **receive-only** mode. Production
PUSH_RAW_DATA includes an actual A0/A1 profile tag only when radio2 is active;
single-profile FF tags and full-wire log pushes without profile tags do not
count as witness evidence. A0/A1 also excludes unlabelled UDP bridge ingress.

Only one tool may own each port. Use exact USB serials from the fixture inventory.
The production Companion opener asserts DTR, so sender/witness are restricted to
native CDC devices (VID 2886, 239A or 303A, excluding 303A:1001 Serial/JTAG).
UART/reset bridges are refused. The T-Deck uses its existing board-aware no-reset
serial opener. These restrictions can require different Companion fixture boards.

## Commands

Preview without hardware, pyserial or site packages:

```powershell
python -S tools/hil/tdeck_radio2_delivery.py --plan --profile 1 --count 3 --seconds 5 --fps 20
```

Explicit live invocation, with **placeholder ports/serials to replace from the
user-selected fixture** and a fresh report path in an existing directory:

```powershell
python tools/hil/tdeck_radio2_delivery.py --allow-transmit --profile 1 --tdeck-port COM10 --tdeck-serial TDECK_USB_SERIAL --sender-port COM11 --sender-serial SENDER_USB_SERIAL --witness-port COM12 --witness-serial WITNESS_USB_SERIAL --count 3 --seconds 5 --fps 20 --output tdeck-radio2-delivery.json
```

For the primary control, separately prepare the source for 909.5/62.5/SF7/CR5/32,
then use the same command with `--profile 0` and a different fresh output path.
Default modes are 0 (static), 1 (direct full redraw), and 2 (buffered cooperative
redraw); modes rotate between repetitions. Default wire sizes are 64 and 172
bytes. Each size/mode/repetition gets one unique sequence and exactly one send
in its own five-second RX window. Default campaign size is 18 transmissions.

`--modes 2 --sizes 64 --count 1` narrows a smoke campaign. `--spi-mhz 2 --bytewise`
selects the legacy radio-transfer policy; otherwise buffered 8 MHz is used.
Use separate fresh reports for SPI policies. Run `--fps 1` separately for the
low-refresh control. Limits are count1..100, seconds3..10, fps1..30, and wire
sizes32/64/172; this path deliberately stays below the existing conservative
Companion USB framing limit and does not claim 255-byte application delivery.

Focused deterministic test invocation, also registered in CI:

```powershell
python -m unittest discover -s tools/hil -p test_tdeck_radio2_delivery.py -v
```

Tests use only the Python standard library, existing pure probe codec, and a
native C++ compiler (`CXX`, default `g++`) for the actual capture-helper slice.
That native case skips when no compiler is available. Live collection additionally
requires pyserial and the local Companion/serial helper modules. No PlatformIO
process is started by the collector or these tests.

## Evidence Contract

The firmware advertises `delivery_version:1` and 32 capture slots. Its commands
are `delivery <mode> <MHz> <bulk> <seconds> <fps> <sequence>` and read-only
`delivery-record <sequence> <index>`. The existing `result <sequence>` replay
continues to work. The next run invalidates the preceding packet capture.

Each live trial:

1. Captures and validates both Companions' read-only configuration/counters.
2. Arms bounded T-Deck capture and waits for exact sequence/profile readiness.
   Redraw modes complete their first measured frame before granting readiness.
3. Checkpoints send intent, sends once using the existing `SPLIT1` run nonce,
   source marker and sequence codec, and records the exact sender ACK or error.
4. Collects bounded witness pushes, the retained T-Deck timing result and every
   retained packet record. No packet hex is printed from the timed radio/display
   callback; all 255 received bytes can be retained without truncation.
5. Checks source physical TX counters increased exactly `[1,0]` and witness TX
   counters did not change. Counter resets/wraps and extra TX fail validation.
6. Requires exactly one exact witness payload with the correct actual A0/A1 tag
   and exactly one full byte-for-byte T-Deck wire packet with its actual target
   profile. It checks record indices, lengths, trial sequence, timestamps, frame
   context, capture counts and radio health. Ambient packets remain in evidence.

Malformed/stale JSON, missing fields, boolean-as-sequence values, duplicate exact
packets, wrong profiles, corrupt bytes, overflow, missing TX ACKs, missing witness
evidence, RX errors and failed health checks cannot produce a passing trial.
Lost result/record serial replies can replay read-only retained data, never the
measurement command or RF transmission. Any failed trial stops the campaign;
the partial report remains incomplete rather than silently retrying a loss.

The report stores the run nonce, exact expected wire hex, source ACK/error,
before/after fixture snapshots, full witness frame hex, T-Deck raw JSON lines,
timing result and packet records. `assessment.failures` names failed checks.
`summary` separates planned, attempted, independently confirmed sent, exact
received, and fully passing trials. A received packet can remain proven when its
source ACK is lost, but that trial and campaign cannot pass.

`during_redraw` means the bytes were **processed inside a cooperative display
callback**, not that RX_DONE occurred during a particular pixel transfer. Record
`at_us` is the processing timestamp within the measured window. Direct redraw can
only process outside the blocking frame. Frame counts and the full timed workload
prove a redraw campaign; they do not prove exact RF/SPI interrupt overlap.

## Reporting And Integration

Retain complete and failed reports. State source revision, all three image
identities/hashes, selected ports/USB serials, conducted/antenna arrangement,
attenuation, selected target, SPI policy, requested/actual frame counts and each
failure reason. Report exact confirmed-sent and received sequences, not merely
requested packet counts or a sender-local ACK. Unrun cases are not successes.

Build/link and inspect the `tdeck_display_switch` RAM gate before flashing;
capture adds about 8.5 KiB fixed RAM. The old receive-only timing collector and
commands remain compatible. See [radio2 load testing](../../docs/radio2_load_testing.md)
for the separate native traffic model and diagnostic-counter context.

This is a controlled low-rate PHY/raw-delivery campaign under a representative
redraw workload. It is not production Full Companion UI/app delivery, sensitivity
or PER qualification, simultaneous dual-source load, WiFi/Bluetooth coexistence,
long-duration reliability, 255-byte application delivery, or hardware validation
until the explicitly selected physical fixture has actually been run.
