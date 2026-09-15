# ESP32-S3 OTA memory experiment

This experiment compares the current static S3 OTA workspace (A) with
`OTA_HEAP_CONTEXT=1` (B), with WiFi and a real TLS MQTT connection enabled.
It is isolated on `experiment/s3-ota-memory-soak`; published recipes retain
their existing defaults. A short stress test cannot establish months of uptime.

## Hardware and scope

| Board | Role | Flash | PSRAM | Lab identity |
|---|---|---:|---:|---|
| Heltec V4.3 OLED, standard V4 | Observer/MQTT repeater | 16 MiB | 2 MiB QSPI | USB MAC `44:1B:F6:69:CF:98` |
| Original SenseCAP Indicator LoRa | Full companion plus experimental MQTT | 8 MiB | 8 MiB octal | WiFi MAC `D8:3B:DA:75:23:AC` |
| Seeed XIAO ESP32-S3 with WIO LoRa | Observer/MQTT repeater | 8 MiB | 8 MiB octal | MAC `28:84:85:B4:09:80`, Windows COM31 |

This is not an R8 test. Both Indicator variants use the same 320x320 canvas;
the native 480x480 Full-plus-MQTT configuration is not qualified by this run.
One custom TLS MQTT slot per board publishes only to the isolated Pi broker.
These results do not qualify five concurrent slots, JWT renewal, heavy RF
traffic, every OTA receive/apply path, or months of operation.

## Reproducible builds

Base commit: `bc0fb2c335938c97220de51098cea6162f2f0fe0`, following PR #7's merge.
Copy `tools/hil/s3_memory_soak.ini` to ignored `platformio.local.ini` and build
the six named environments sequentially, with `MESHCORE_ESP32_FULL_BUILD=1`.
Never run multiple PlatformIO processes in this checkout. Archive each image,
ELF, map, partitions, bootloader, and memory report before the next build.

The private broker's public CA must be in `ssl_certs` while generating these
experimental images. Remove it and `platformio.local.ini` afterward. Private
keys stay on the Pi. WiFi credentials and the Windows logger token are in
private local files; do not commit them or the generated certificate bundle.
Broker certificate verification remains enabled.

The V4 and XIAO lab recipes also enable `MESH_SOAK_WIFI_KEY_OVERRIDE` and
require the ignored local header `tools/hil/S3SoakWiFiKey.h`. To reproduce
diagnostics using ordinary WiFi credentials, remove that define from the lab
recipe; the private header is then unnecessary. Published recipes do not
enable either lab flag.

| Image | Application bytes | Link-time available internal bytes | SHA-256 |
|---|---:|---:|---|
| V4 A | 1,910,392 | 237,360 | `68c2749e0ea40db91d953f6355010bc602f85424526ca6acbe1cad7fd11c8630` |
| V4 B | 1,911,256 | 252,760 | `6847ad4ad6bac2e558b8aed67de37417a497972391f13a71e0a42b5c9704dcff` |
| Indicator A | 2,313,256 | 244,248 | `3d0ac27f95ddc54a32b59a3892d157713dcded285dbeb98d1bdfdb1ce0b951ab` |
| Indicator B | 2,314,136 | 255,496 | `83045d7ec48befc43f3460c08c68b71da037a5476e14dd59def5706fc5a1b07e` |
| XIAO A | 1,845,000 | 238,448 | `6fcc657c5ccbc5dd3d53a0cf1ceebde54479d59d329f8625e7cb7141a27d9ed8` |
| XIAO B | 1,845,912 | 253,864 | `2b233e42e0c515b152ce83ca8fe38b1f35780970bb6350dbadf2591b6a2699a1` |

All six firmware builds passed their RAM/flash gates. The linked internal
capacity gain is 15,400 bytes on V4, 11,248 bytes on Indicator, and 15,416 bytes
on XIAO. This is
capacity returned while idle, not a reduction in peak OTA allocation needs.
Both A/B variants keep the existing MQTT client reuse policy and internal
MQTT task stack; this experiment does not move that stack into PSRAM.

## Instrumentation

`MESH_SOAK_DIAGNOSTICS` exposes local-only `get soak`, `soak ota-cycle`, and
`soak wifi-drop` commands. They report free/largest/minimum internal heap,
free PSRAM, OTA context size/presence, WiFi state, and uptime. No published
recipe enables these controls.

The lab network has a valid 64-digit raw WiFi key. Production MQTT preferences
currently accept only 63 characters. The V4 experiment uses a separate,
test-only NVS key through `MESH_SOAK_WIFI_KEY_OVERRIDE`, without changing the
production binary preference layout. This does not fix released raw-key input.
The network's original nine-character passphrase was subsequently recovered
from the connected Windows profile; XIAO uses it through ordinary settings.
The override is compiled into both XIAO images but no override key is stored.

The Pi logger owns one persistent V4 USB handle: opening this particular USB
device resets it, even with DTR/RTS disabled. Firmware installations and logger
restarts therefore begin new uptime runs. Indicator telemetry uses TCP 5002.
The logger distinguishes the 49.7-day `millis()` rollover from a reboot.

Passive samples omit `get mqtt.stats`: its outbox query can block behind the
MQTT client lock during a TLS handshake and stall the CLI. The logger reads
`get mqtt.status` and separately counts messages received through a verified
TLS subscription. It stores topic counts, not MQTT payloads or credentials.

Two exploratory rapid-flap runs used an overly short 90-second recovery cutoff.
The first also used the blocking outbox probe. Preserve those pilot results;
do not count them as successful tests. Production MQTT backoff deliberately
grows to five minutes and clears after two stable connected minutes, so rapid
flapping is not equivalent to repeated independent connection tests.

## Results and continuing soak

V4 and Indicator both passed the matched A/B workload: 200 explicit OTA context
acquire/release attempts, four MQTT bridge restarts, one WiFi reconnection and
one five-second private-broker outage per image. An initial bridge restart
preconditions each run. No unexpected uptime resets occurred in these runs.
A was paused after its four bridge restarts to investigate the display report,
then resumed for the network faults; its elapsed duration includes that pause.

After the workload, with WiFi and one TLS MQTT slot connected:

| Board | A free internal bytes | B free internal bytes | Gain | A largest block | B largest block |
|---|---:|---:|---:|---:|---:|
| Standard V4 | 95,396 | 111,040 | 15,644 (15.28 KiB) | 81,908 | 98,292 |
| Indicator | 107,168 | 118,520 | 11,352 (11.09 KiB) | 94,196 | 106,484 |
| XIAO (same saved settings, cold boot) | 151,028 | 166,444 | 15,416 (15.05 KiB) | 139,252 | 147,444 |

Free heap varies with network activity; link-time capacity is the cleaner
measurement of storage moved out of static RAM. PSRAM remained about 2.01 MB
on V4 and 6.33 MB on Indicator. In each B run the OTA context returned to absent
after the allocation workload. Neither board's largest block shrank through
those 200 allocation cycles or the reconnect workload. WiFi/MQTT recovery took
about 4.3 seconds on A and 3.8 seconds on B; broker recovery took about 19.5 and
21.8 seconds respectively. These are single observed recovery times.

Workspace recreation has an observed latency cost. The 200 command cycles
took approximately 189 seconds on V4 B and 379 seconds on Indicator B, versus
32 and 15 seconds on A. This includes command transport, OTA initialization,
and ordinary firmware service, so it is not an allocator microbenchmark.
The static comparator already owns an initialized workspace; the dynamic
variant recreates it. This experiment does not claim faster OTA startup, and
this cost should be checked against real session use before a default change.
Code inspection shows that `OtaContext::begin()` calls `ota_self_firmware()`,
which scans the running ESP32 application for its EndF identity on each call.
All six experimental images contain valid EndF trailers. Caching immutable
boot identity is a candidate for a separate change, not part of these results.

The real WiFi mOTA folder endpoint was tested separately on Indicator: 20
empty-folder sessions on A and 40 on B. All completed and B released its context.
During B's first batch, free internal RAM went from 118,552 to 116,316 bytes,
and its largest block from 106,484 to 102,388. A second batch ended at 116,308
bytes with the same 102,388-byte largest block. This retained allocation/layout
change is not explained by the now-absent OTA context; record it separately and
watch it during the soak. It did not keep growing in the second batch.

The original A folder script also tried V4, whose observer recipe has no TCP
5001 folder source. Its overall result therefore says `passed: false`, despite
Indicator completing all 20 sessions. That endpoint mismatch is preserved in
the evidence; it is not an OTA failure on V4. The corrected script targets only
the Indicator. These empty-folder checks do not qualify a full firmware transfer.

XIAO passed 200 context cycles, four bridge restarts and one WiFi reconnect
per image, with no unexpected reset. Its largest block stayed unchanged within
each workload. WiFi/TLS recovered in about six seconds for both. This local
test did not inject a separate broker outage or exercise a TCP folder endpoint.
Verified subscriptions on the Pi confirmed actual TLS publications for both.

The first XIAO A run followed initial device configuration and finished with
126,140 internal bytes free, which would have overstated the OTA saving. It is
preserved as `xiao-live/A-configuration-stress.json`. Reflashing A without an
erase, retaining exactly the saved settings used by B, and rerunning the whole
workload produced the 151,028-byte baseline above. Its resulting gain exactly
matches the 15,416-byte linked-capacity change. The earlier larger gap is not
credited to OTA storage; the precise retained allocation was not isolated.

The Pi stores evidence under `/home/mikec/hwtest/runs/s3-memory-soak`:

- `telemetry.jsonl`: append-only memory, WiFi/MQTT state, uptime, transport errors.
- `latest.json`: latest samples plus broker-side received-message counts.
- `A-stress.json` / `B-stress.json`: matched stress results and phase boundaries.
- `A-folder.json` / `B-folder.json`: real WiFi mOTA empty-folder session checks.
- `B-repeat-folder.json`: the extra batch checking the initial allocation change.

`meshcore-memory-soak.service` holds the USB connection and samples each minute.
`meshcore-soak-broker.service` supplies the private TLS broker. Both are enabled
on the Pi. The logger never reboots a board to hide a firmware failure, but
reopening the V4 USB transport can itself reset it; inspect transport counts,
service restarts, and uptime together when diagnosing any interruption.
The final passive phase started at `2026-09-11T07:18:07Z`. Installing the final
logger revision (which serializes concurrent samples) deliberately restarted
the service and therefore reset V4 once. That begins its long-soak uptime run;
Indicator retained its uptime. Both then reported WiFi/TLS connected, absent
OTA contexts, and actual broker-side publications.

The local XIAO logger owns COM31 and writes under
`C:\git\MeshCore\out\s3-memory-soak\xiao-live`. Its control endpoint is
loopback-only with a private token. It runs while this Windows session remains
alive; it is not a Pi service and will not survive a computer shutdown. A
separate verified TLS subscription on the Pi confirmed XIAO publications for
both images. The Pi logger's per-device counts cover V4 and Indicator only.
The final XIAO B passive phase was verified at `2026-09-11T07:21:21Z`, with
166,508 free internal bytes, a 155,636-byte largest block, WiFi/TLS connected,
and no active OTA context. The largest block differs from the post-stress
sample because this is a fresh boot, which illustrates why comparisons must
use the same workload history. A Pi subscription received its publication two
seconds later.

Read-only checks on the Pi:

```sh
systemctl is-active meshcore-memory-soak meshcore-soak-broker
cat /home/mikec/hwtest/runs/s3-memory-soak/latest.json
```

On Windows, read `out/s3-memory-soak/xiao-live/latest.json`. Keep the computer
awake for continuous local samples. Neither logger automatically reboots a
radio in response to a failed sample.

The public lab CA was removed from `ssl_certs`, and the temporary local PIO
configuration and generated test trust bundle were removed after building.
Archived experimental firmware and public evidence remain in ignored
`out/s3-memory-soak`; no test CA enters the published build configuration.

No published default has changed. Leave the experimental B images running to
collect longer evidence before deciding whether to expand S3 heap allocation.
More idle internal RAM gives WiFi/TLS useful headroom; it does not itself prove
stability or eliminate the need to allocate the OTA workspace during use.

## Physical display checks

The user reported unresponsive screens during MQTT restart testing. The V4
lit after temporarily choosing `on`; after restoring battery and USB profiles
to `button`, 15 seconds, the user confirmed that pressing the button wakes it.
The synchronous reconnect/diagnostic work may have delayed UI service, but the
exact cause of the original visual report was not established.

The T096 is a separate nRF52840 board, USB identity `651F8E496197F882`. It was
reflashed with current Full firmware and briefly instrumented. The trace showed
the radio, sensor and UI loop advancing; the display had initialized but was
marked off. Its functional CDC0 was in Binary mode after a host-session close.
Sending a newline-delimited `+++MESHCORE-TERM-START` restored ASCII control.
With display mode set to `on`, the user confirmed the normal screen appeared.
Temporary tracing was removed by reinstalling the non-instrumented image, and
battery/USB profiles were restored to `button-pairing`, 15 seconds. The user
then confirmed that its button wakes the normal screen after timeout.

T096 recovery firmware: `v1.17.1-t096-check`, Full/shared OTA queue, `-Os` size
profile, available linked internal RAM 99,892 bytes (73,728 required). ZIP SHA-256
`f5cfcb001efda559ae9b4e7b105ac9766a604808f5fd708b7c533f8a11c64578`.
No device-wide filesystem erase was needed for this recovery. T096 does not
count as an S3 A/B test.

Host validation: `python -m unittest discover -s tools/hil -p test_s3_memory_soak.py`.
The five checks cover diagnostic filtering, missing data, resets, and counter
rollover. They are not substitutes for the hardware evidence.
