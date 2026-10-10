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
- Defer a new connection if DMA-capable internal memory has less than
  16 KiB free or a largest block below 4 KiB. TLS without working PSRAM
  additionally needs 60 KiB free and a 17 KiB largest block. These are
  conservative admission bounds, not a promise that future allocations succeed.
- Prefer PSRAM for TLS allocations and reject an internal fallback that
  would consume the 16 KiB DMA reserve. Check allocation-size overflow.
- Keep renewal able to close an old TLS connection before checking memory
  for its replacement. Stop an in-flight SDK client on reconfiguration.
- Require byte-addressable internal memory for string/JSON/queue buffer
  fallbacks. The internal-memory capability alone also admits instruction RAM
  that only supports aligned 32-bit access.
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

All 1,794 native tests and the 31 memory-budget tests passed. The focused
MQTT tests are also connected to the GitHub unit-test workflow.

The VM reached both reported broker TLS endpoints with certificate
verification enabled. That is an endpoint check, not an ESP32 MQTT test.

MercerWoodMesh reported offline and its SSH connection timed out during this
run. No live V4 flash, MQTT publication or soak result is claimed. The next
hardware check must exercise the reported WSS presets together, verify rising
publish counters and DMA headroom, then include a broker/WiFi reconnection
and power-saving idle run with no reset or AES allocation error.
