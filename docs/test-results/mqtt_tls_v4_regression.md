<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/mqtt_tls_v4_regression/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 MQTT TLS connection regression

## Report and diagnosis

The Heltec V4 report contains two separate failures:

- `0x8006`: ESP-TLS connection timeout opening the WSS brokers
  `mqtt.waev.app:443` and `mqtt-v1.cascadiamesh.org:443`.
- `esp-aes: Failed to allocate memory`, followed by TLS write failure
  `0x8018` and resend errors.

The firmware configured a 2,500 ms network timeout to bound synchronous
publishes. ESP-MQTT also uses that setting for connection establishment,
including TLS and the WebSocket upgrade. It was not a write-only setting.

The shipped ESP32-S3 AES driver allocates DMA-capable internal bounce buffers
for unaligned external-memory records, up to 1,600 bytes per buffer. Ample
PSRAM or an aggregate free-heap reading therefore does not establish that an
AES operation can succeed. The supplied log identifies an allocation failure
in this driver. The same allocation error was reproduced on the lab V4
with the older firmware and five enabled WSS presets.

The shipped Arduino 2.0.17/IDF 4.4.7 TLS allocator was checked in its archives:
TLS allocations use the configurable `mbedtls_calloc` function, so the
existing PSRAM redirect takes effect. Retaining a PsychicMqttClient does not
retain its TLS records: ESP-IDF frees/recreates the TLS session on reconnect.

References: [ESP-IDF 4.4.7 AES implementation](https://github.com/espressif/esp-idf/blob/v4.4.7/components/mbedtls/port/aes/dma/esp_aes.c)
and [ESP-TLS errors](https://github.com/espressif/esp-idf/blob/v4.4.7/components/esp-tls/esp_tls_errors.h).

## Firmware changes

These changes apply to the shared ESP32 MQTT bridge, including repeater,
room-server and companion images that enable it.

- Restore the SDK's 10,000 ms network timeout. A stalled synchronous publish
  can now wait up to that same timeout; the radio loop runs separately.
  Cooperative shutdown also includes that deadline in its stop budget.
- Schedule initial connections through the normal maintenance loop. Check
  the actual pending attempt across SDK tasks, rather than assuming a
  five-second startup delay or 15-second reconnect gap finished a handshake.
- Reserve 32 KiB free DMA-capable internal memory and an 8 KiB largest block
  before initializing a new SDK client. This includes a 16 KiB startup allowance
  above the 16 KiB AES reserve. Restarting an initialized, stopped client needs
  24 KiB free and an 8 KiB largest block: its buffers/configuration remain
  allocated, while the 6 KiB task stack and task control block are recreated.
  Retained, running clients need 16 KiB free and a 4 KiB largest block to
  reconnect. Pin SDK client tasks to their existing
  6,144-byte default stack; the separate bridge worker remains 8 KiB.
  TLS without working PSRAM
  additionally needs 60 KiB free and a 17 KiB largest block. These are
  conservative admission bounds, not a promise that future allocations succeed.
- Prefer PSRAM for TLS allocations and reject an internal fallback that
  would consume the 16 KiB DMA reserve. Check allocation-size overflow.
- Keep renewal able to close an old TLS connection before checking memory
  for its replacement. Stop an in-flight SDK client on reconfiguration.
- Require byte-addressable internal memory for string/JSON/queue buffer
  fallbacks. The internal-memory capability alone also admits instruction RAM
  that only supports aligned 32-bit access.
- Use uniform 896-byte MQTT buffers for all ESP32 presets. The SDK does not
  resize initialized buffers through a config update, so a retained client
  must already have room when an operator changes from password to JWT
  authentication. This costs additional buffer memory for non-JWT presets on
  internal-only boards, while preserving safe client ownership.
- Count broker QoS acknowledgments separately from accepted writes and expose
  them as `pub_ack` in `get mqttN.diag`. This lets hardware tests verify
  delivery without treating an expired outbox entry as an acknowledgment.
- Add DMA free/largest readings to `get mqtt.stats` and diagnostic logs.
  Correct the operator-facing TLS error labels against SDK constants.
- Charge the existing 49,152-byte WiFi stack reserve for every MQTT image,
  including profiles without compiled WiFi credentials. Multiple WiFi features
  charge it once. Saved runtime credentials can activate the station.

Existing slot limits, certificate verification, authentication and backoff
remain in effect. Memory pressure defers additional slots instead of consuming
memory needed by existing connections and the radio.

## Validation and limits

The regression tests execute the production optimizer, adapter setters,
connection admission, TLS allocator fallback, scheduler and diagnostics with
controlled SDK calls and heap readings. They cover IDF 4/5 config layouts,
PSRAM and internal-only modes, fragmented heap, exhausted PSRAM, delayed
connection callbacks, retry timing and bounded replies. They do not execute
an ESP32 AES peripheral or qualify physical WiFi behavior.

All 46 focused Python MQTT tests and 33 memory-budget tests passed after the
final reconnect/buffer changes. The 1,794-test native core suite passed after
the initial TLS/admission fix, before the subsequent MQTT-only refinements.
The focused MQTT tests are also connected to the GitHub unit-test workflow.
The new memory tests include a linked-ELF boundary that the previous
WiFi-omitted policy incorrectly accepted.

Full MQTT repeater and room-server builds with USB packet logging passed on
10 October 2026 at revision `937f8610c`. The V4 Full Companion build passed
at `d11bf3615`. The RC32 build at the same revision also passed using Arduino
3.3.11 and ESP-IDF 5.5.5, exercising the newer SDK in an actual firmware build.
The only intervening change corrected the host-side WiFi memory accounting.
Their build logs report:

| Build | Application flash bytes | Available internal heap bytes | Required heap bytes | Largest internal region bytes |
| --- | ---: | ---: | ---: | ---: |
| Heltec V4 | 2,182,265 | 205,080 | 198,432 | 172,312 |
| Heltec V3 | 2,125,377 | 211,296 | 209,184 | 178,528 |
| Station G2 | 2,017,953 | 201,280 | 200,480 | 168,512 |
| Heltec V3 room server | 2,104,057 | 201,992 | 196,016 | 169,224 |
| Heltec RC32 without display, IDF 5 | 2,287,033 | 195,688 | 194,336 | 162,920 |
| Heltec V4 Full Companion | 2,418,405 | 233,880 | 194,336 | 201,112 |

Application flash is the PlatformIO size-check reading. Internal heap is
linked capacity before dynamic allocation, not free heap measured after boot.
These RAM gates passed the startup policy; they do not qualify maximum
TLS/WSS/outbox load. The default MQTT startup allowance remains 24,576 bytes,
with runtime admission for additional connections. See
[firmware memory checks](../research/firmware_memory_budget.md#mqtt-startup-minimum-and-runtime-qualification)
for the separate maximum-slot estimate and qualification limits.

Two additional Full builds compiled and linked the shared MQTT code but were
held by the unchanged RAM safety gates:

| Full build held | Available internal bytes | Required bytes | Shortfall bytes |
| --- | ---: | ---: | ---: |
| T-Beam SX1276 repeater, original ESP32 | 179,736 | 192,560 | 12,824 |
| Heltec V3 Companion | 182,264 | 198,432 | 16,168 |

Both already enabled WiFi through other build flags, so the corrected
MQTT-only WiFi detection did not increase either requirement. Their capacity
shortfalls require separate memory work before those Full artifacts can be
qualified; neither gate was lowered and neither failed artifact was published.
The shared firmware fixes still apply to their MQTT code. The original ESP32
compile check and IDF 4/5 tests cover the non-V4 source paths; physical testing
in this report is limited to the V4.

Live TLS 1.2 certificate chains from 12 audited preset endpoints were
verified with the exact CA PEMs extracted from `MQTTPresets.h` using a native
host build of the pinned mbedTLS 2.28.7 submodule from ESP-IDF 4.4.7, commit
`2b8e772fc1cb0732cda3bae7d1e9d6f4cfaf63d9`. Hostname verification was enabled.
All 11 GTS Root R4 endpoints and the ISRG Root X1 Cascadia endpoint returned
verification result 0 and flags `0x00000000`. Wrong-hostname and unrelated-anchor
negative checks were rejected. WAEV also passed with the server root omitted
from its supplied chain.

The existing cross-signed GTS Root R4 PEM is a valid trust anchor for that
pinned mbedTLS verifier. Ordinary OpenSSL root-only verification rejected it,
while partial-chain verification with the same PEM passed all 11 GTS endpoints.
This host-probe policy difference does not establish a firmware CA failure or
require a CA replacement for the reported AES allocation errors. The verifier
used the native default mbedTLS configuration; this establishes pinned X.509
chain and trust-anchor behavior, not an ESP32 hardware handshake.

## Physical V4 results

MercerWoodMesh's Heltec V4 was tested over its native USB console using the
existing SlowFi network. The Pi's WiFi configuration was not changed. A full
16 MiB private flash backup was made before testing. Test credentials and raw
console logs are not included in the public results.

| Check | Result |
| --- | --- |
| Older firmware `c5fe8a0e`, WAEV and Cascadia | Both connected for the 150-second trial; accepted writes increased with no outbox backlog. The older firmware has no PUBACK counter, so accepted writes alone do not establish broker delivery. |
| Older firmware, five WSS presets | Reproduced `esp-aes: Failed to allocate memory`, `0x8018` and `0x8006`. All five slots eventually disconnected. This is a failing baseline. |
| Final firmware `937f8610c`, five presets enabled | WAEV, Cascadia and Analyzer US connected within 46 seconds. Analyzer EU and MeshMapper waited without allocating SDK clients because DMA memory was below the cold-start admission threshold. No AES allocation, TLS timeout/write error or CPU reset was observed during this trial. |
| Final firmware, power saving on, 180.005 seconds without console commands | All three connected brokers stayed healthy. Their `pub_ack` counters increased from 2/1/1 to 5/5/5. Uptime advanced from 57 to 258 seconds including surrounding queries. DMA heap stayed at 22,776 free / 22,516 largest bytes, with no outbox backlog, drops, AES allocation error, TLS timeout/write error or CPU reset. |
| Cooperative MQTT stop | The stop request completed and `get mqtt.stopping` returned off. The radio console remained responsive. |

The three-connection measurements included about 22.7 KiB free DMA-capable
heap with a 22.0 KiB largest block. Outbox activity briefly consumed additional
memory. Three brokers were qualified in these conditions; this does not
qualify five simultaneous WSS connections or a long-duration loaded-radio
soak. Additional enabled slots wait for headroom rather than exhausting the
memory needed by active TLS connections.

The final power-saving trial recorded core error bitmask `0` before and after
the idle interval; bitmask `8` appeared after the subsequent MQTT stop. An
earlier fixed-firmware trial also recorded bitmask `8`. This is the configured
radio watchdog flag on a quiet channel, also observed in earlier V4 console
qualification; it is not eight MQTT errors or evidence of a CPU reset. This
run does not claim zero radio watchdog events.

The attempted SSID interruption returned "WiFi SSID saved; reboot to apply"
and did not disconnect the station. It is therefore not counted as a physical
WiFi-loss recovery test. Regression tests exercise the production WiFi-loss
handler and connection scheduler with retained SDK clients, including the
restart memory allowance and serialized reconnect attempts.

A Pi-wide USB hub reset interrupted the older five-broker baseline. Several
radios disconnected together and subsequently reappeared. That interruption
is retained as a host-lab failure, not attributed to a V4 firmware crash.

After the final live check, the original NVS and filesystem backup regions
were restored and their writes verified, preserving the final firmware. Saved
configuration replies matched, and a separate `get public.key` check verified
that the public-key hash was unchanged.
Comparison removes console echo/prompt framing and treats the old blank
`get wifi.ssid` response as the new `(not configured)` response. MQTT and
power saving were both returned to their original off state. No release assets
were uploaded as part of this test.
