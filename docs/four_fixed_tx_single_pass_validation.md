# Four fixed transmitters / fast single-pass receiver

## Result

MercerMesh Pi bench run, started **2026-09-14 22:18:49 UTC** (15:18:49 PDT):
four physically separate, fixed-channel transmitters and the Heltec V4 receiver.
The full stationary controls passed **400/400**. The subsequent four-channel
scan received **392/400 correctly (98%)**, with **8 timeouts (2%)** and **zero
wrong-channel payloads**. No RF retries were used.

| TX / assigned channel | Frequency (MHz) | Fixed-RX control | Scanning RX | Scan mean RSSI (dBm) | Scan mean SNR (dB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| RAK4631 / 0 | 909.5 | 100/100 | 96/100 | -20.69 | +8.63 |
| Heltec T096 / 1 | 910.5 | 100/100 | 98/100 | -3.84 | +8.45 |
| Heltec MeshTower V2 / 2 | 911.5 | 100/100 | 100/100 | -45.48 | +8.41 |
| Seeed T1000-E (LR1110) / 3 | 912.5 | 100/100 | 98/100 | -18.27 | +8.51 |

RSSI/SNR describe received packets only. Each transmitter sent 200 unique
packets across its baseline and scan. All 800 transmit calls returned success,
with zero frequency or modulation commands during those packet calls. There
were zero recovered/replayed USB responses in the packet records.

## Switching timing and command evidence

| Hop population | Count | Minimum (us) | Mean (us) | Maximum (us) |
| --- | ---: | ---: | ---: | ---: |
| All single-pass switches | 7,206 | 429 | 513.980 | 801 |
| Cached modulation; no 0x8B | 6,814 | 429 | 498.602 | 686 |
| Modulation cache refresh | 392 | 763 | 781.291 | 801 |

The receiver recorded exactly **7,206 frequency commands and 7,206 optimized
RX resumes**: one frequency write and one resume per hop. Second-pass count
was zero. Duplicate frequency writes, frequency/CR detours and added settling
were disabled. Warm XOSC, bulk 8 MHz SPI and cached modulation were enabled.

The average switch was **0.514 ms**, but not every switch was below 0.6 ms.
After each received packet, ordinary RX staging invalidates the modulation
cache; the next hop refreshes it. Subsequent unchanged hops omit 0x8B.
The 392 refreshes match the 392 received packets. The source path is
`RadioLibWrapper::recvRaw()` -> `startRecv()` / `startReceiveMode()` ->
`PhysicalLayer::startReceive()` -> `CustomSX1262::stageMode()` ->
`SX1262ProfileSwitchState::invalidate()`. No production cache-invalidation
behavior was changed for this test.

Mean switch-plus-settling-instrumentation time was 515.698 us, maximum 803 us.
The settling measurement averaged 1.718 us of timer/measurement overhead;
the configured settling delay was zero. The idle four-channel sweep averaged
169.135 ms (maximum 170.470 ms), versus 262.144 ms for the 32-symbol preamble.
Packet-held visits include reception/guard time and are not idle sweep timings.
The `deferred` counter counts guard-loop deferrals, not lost packets.

## Method and fixture

- SF10 / 125 kHz / CR4/5; 32-symbol preamble; synthetic 16-byte CHS1 packets.
- Four channels spaced 1 MHz apart; 5.1-symbol RX visits (41,780 us).
- Each physical transmitter was configured once and remained on its assigned
  frequency throughout both the controls and scanning.
- Receiver was reinitialized for each fixed-channel control and once before
  the scan. All 100 control packets per transmitter were retained.
- Scan used 100 packets per channel, randomized channel order and arrival
  pauses (seed 606125). Transmissions were staggered, not concurrent.
- Each expected packet had a 10-second scan timeout. Misses did not stop the
  run, restart the scanner, force it onto the TX channel or trigger a retry.
- SX1262 normal RX gain was verified as register 0x94. The V4's external
  KCT8103L front end remains distinct from the chip's boosted-gain setting.
- Requested TX power was -9 dBm at each radio chip. The Heltec TX boards have
  external PA paths, held in TX mode by this HIL firmware; these are **not
  calibrated equal antenna-port powers**.
- High-rate channel trace was disabled; command counters and timing remained
  enabled. Gateway services remained running, so unrelated RF was not excluded.

| Role | Hardware serial / MAC | Pi USB topology |
| --- | --- | --- |
| TX0 RAK4631 | 9AB3B64C641BA927 | 1-1.3.1 |
| TX1 Heltec T096 | 651F8E496197F882 | 1-1.2.3 |
| TX2 Heltec MeshTower V2 | 9352162A72082314 | 1-1.2.2 |
| TX3 Seeed T1000-E LR1110 | 34A9141999729D5D | 1-1.2.1 |
| RX Heltec V4 | 44:1B:F6:69:CF:98 | 1-1.3.3 |

Board selection checked serial plus topology, not transient tty names or CDC
interface count. The RAK3401 gateway, serial 0B81C9C68D8D01B4, was excluded.
The earlier local XIAO radios were not modified during this run.

## Remaining losses and interpretation

Timeouts occurred at overall scan attempts 10, 14, 55, 65, 145, 299, 304 and
384. Thus they were not confined to startup. Each timeout reported RX mode 1
and zero device errors; TX calls returned success and still reported no
configuration writes. Final RX failure, RX-mode, cache and receive-error
counters were all zero.

The wrong-channel behavior did **not** reproduce in this configuration.
The remaining misses do not require a transmitter that retunes between packets.
However, this is not a controlled A/B against the earlier XIAO setup: the RX
board, TX boards/chips, antennas and geometry changed. These results do not
prove that the earlier transmitter was faulty or establish the cause of the
eight remaining losses.

Several received signals were very strong, particularly the T096 at about
-4 dBm. The weakest link, MeshTower V2 at about -45 dBm, passed 100/100 while
scanning. That makes a controlled spacing/attenuation comparison worth
considering, but this sample does not prove receiver overload. The observed
98% is a finite bench result, not a reliability guarantee or sensitivity test.

## Capture integrity and post-run validation

All 400 scan probes finished and final radio telemetry was returned. The
original host checker then rejected that telemetry because it incorrectly
required zero modulation writes over the entire run, including post-packet
RX staging. Its raw capture therefore remains `complete: false`, with final
status preserved verbatim inside its error string. The normal `sender_final`
collector step did not run. **The raw failure record was not rewritten.**

Source review established the cache-refresh behavior above. The host checker
now validates modulation writes against the measured refresh-hop count, checks
that cached plus refresh hops equal total hops, and still requires single
frequency writes, single fast resumes, zero added delay and the other policies.
It also saves final telemetry before checking it, so future failures retain
that structured record.

Separate offline validation parsed the saved telemetry, checked the full
400-probe scan and all four 100-probe controls, verified 800 unique sequences,
and checked every transmitter's packet counter from 1 through 200 and its
zero-retune acknowledgments. A capture regression additionally checks that
each TX/RX response ID matches its probe and every accepted payload/channel
matches the assigned channel. No radio was rerun to replace the failed host
assertion. The derived summary explicitly reports
`original_collector_complete: false` and `post_run_validation_passed: true`.

Evidence files:

- [Immutable raw capture](../tools/hil/profile_four_tx_sf10_single_results.json)
- [Independently validated summary](../tools/hil/profile_four_tx_sf10_single_validated.json)
- [Deployment record](../tools/hil/profile_four_tx_sf10_single_deployment.json)
- [Build manifest](../tools/hil/profile_four_tx_sf10_single_manifest.json)
- [Verified idle/service state](../tools/hil/profile_four_tx_sf10_single_cleanup.json)

Raw capture SHA-256:
`42de3ac0c335b2c59f35c1cab217a01ef242c9f2e7b0010c8000a908a59232e6`.
Derived summary SHA-256:
`316bd551eb7306dfc576f4b88ad23e82cb1d701481d1e022067c961806d31bfe`.

## Build, deployment and final state

Built from the dirty `keymindCascade` worktree based on
`2b5a6f209835f0f67ed73fee78d36447a4566ce6`. This is not a claim that HEAD
alone reproduces the images. The manifest records image and HIL-source hashes.
All five PlatformIO builds succeeded and ran sequentially.

After the host-checker correction, 25 focused unit/regression tests passed,
including the preserved-capture check. `git diff --check` passed; its only
warning concerned a pre-existing CRLF conversion in `MyMesh.cpp`.

RadioLib was pinned to `187ef24791c3d844939b2be13a68bd890bd04e4c` (7.7.1).
The nRF framework was `d5413016` (1.10701.0); the ESP build used platform
6.11.0 / Arduino 2.0.17. The first three nRF targets retained S140 6.1.1
(FWID 0xB6, app base 0x26000); T1000-E retained S140 7.3.0 (FWID 0x123,
app base 0x27000). nRF DFU packages were application-only, with their matching
SoftDevice requirements intact. No nRF bootloader/SoftDevice replacement or
cross-board override was used. Runtime FWID and application base were verified.

V4 received the normal ESP bootloader, partition table, boot_app0 and HIL app
at 0x0, 0x8000, 0xE000 and 0x10000, respectively, with esptool hash checks.
No whole-flash bulk erase was issued. No backup was requested for these
authorized bench deployments.

The initial bundle's collector/deployer hashes are not the final scripts used:
the actual run used `profile_four_tx_v2.py` with SHA-256
`b762e412636076902174c76b71961cc8ab3059c2b6de3dd3b4123bafcb06545c`
(startup-greeting fix, original strict final assertion), and deployment used
`profile_four_tx_deploy_v3.py` with SHA-256
`8a660788e0215e6ac1aece0a5d033521f9f376c9e51c00e760e918ebfd72cca1`.
The current local collector includes the later validation correction; firmware
images were not changed after the run. Remote evidence remains under
`/home/mikec/hwtest/runs/four-tx-sf10-20260914`.

All five radios were subsequently rebooted and independently verified idle.
All TXs reported unprepared, packet count zero, no failure and autonomous TX
disabled; V4 reported scanning inactive and zero hops after reboot. HIL
firmware remains installed. ModemManager was restored active, gateway services
were left active, and the V4 memory-soak service remains inactive because its
firmware is now the bench receiver. No Pi USB hub power cycle was performed.

No commit, push or pull was performed for this test. Unrelated worktree changes
were preserved.
