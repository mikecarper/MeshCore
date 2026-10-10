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
in this driver; the exact V4 heap history has not been captured in this run.

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
  before starting an SDK client task. This includes a 16 KiB startup allowance
  above the 16 KiB AES reserve. Retained, running clients need 16 KiB free and
  a 4 KiB largest block to reconnect. Pin SDK client tasks to their existing
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
- Count broker QoS acknowledgments separately from accepted writes and expose
  them as `pub_ack` in `get mqttN.diag`. This lets hardware tests verify
  delivery without treating an expired outbox entry as an acknowledgment.
- Add DMA free/largest readings to `get mqtt.stats` and diagnostic logs.
  Correct the operator-facing TLS error labels against SDK constants.

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

All 43 focused Python MQTT tests, 1,794 native tests and 31 memory-budget tests
passed. The focused MQTT tests are also connected to the GitHub unit-test
workflow.

Three Full MQTT repeater builds with USB packet logging passed on 10 October
2026 at revision `7acc63746`. Their build logs report:

| Build | Application flash bytes | Available internal heap bytes | Required heap bytes | Largest internal region bytes |
| --- | ---: | ---: | ---: | ---: |
| Heltec V4 | 2,182,125 | 205,080 | 198,432 | 172,312 |
| Heltec V3 | 2,125,149 | 211,296 | 209,184 | 178,528 |
| Station G2 | 2,017,685 | 201,280 | 200,480 | 168,512 |

Application flash is the PlatformIO size-check reading. Internal heap is
linked capacity before dynamic allocation, not free heap measured after boot.
All three RAM gates passed the startup policy; they do not qualify maximum
TLS/WSS/outbox load. The default MQTT startup allowance remains 24,576 bytes,
with runtime admission for additional connections. See
[firmware memory checks](../research/firmware_memory_budget.md#mqtt-startup-minimum-and-runtime-qualification)
for the separate maximum-slot estimate and qualification limits.

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

MercerWoodMesh is now reachable and live V4 testing is in progress. Live flash,
MQTT publication, reconnect and soak outcomes remain pending. The hardware
check must exercise the reported WSS presets together, verify rising publish
counters and DMA headroom, then include a broker/WiFi reconnection and
power-saving idle run with no reset or AES allocation error.
