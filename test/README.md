# Host unit tests

Fast, hardware-free unit tests for the fork's pure logic, run on the host with
GoogleTest via PlatformIO's `native` environment. They cover the extractable
observer/WebConfig logic (validation, preset table, topic templates, key
parsing) -- the parts that don't depend on the ESP32, radio, or network stack.
Integration behavior (AsyncTCP transport, WiFi/MQTT, SoftAP) is exercised
separately; see "Local testing without hardware" in `MQTT_IMPLEMENTATION.md`.

## Running

```sh
pio test -e native                      # all suites
pio test -e native -f test_webconfig_keys   # a single suite
```

A green `[PASSED]` per suite means GoogleTest returned 0 (all assertions
passed). PlatformIO's "0 test cases" line is just its Unity-style counter and
does not reflect the GoogleTest count -- run the built binary directly
(`.pio/build/native/program`) to see the per-assertion breakdown.

## Suites

| Suite | Source under test | Covers |
|-------|-------------------|--------|
| `test_mqtt_presets` | `src/helpers/MQTTPresets.h` | preset lookup; table integrity (unique names, non-empty URLs, JWT-audience invariant, names fit the slot buffer); `mqttPresetNeedsSlotCredentials`; slot-count constants |
| `test_observer_validation` | `src/helpers/MQTTObserverValidation.h` | IATA (exactly 3 alphanumerics), owner key (64 hex), NTP hostname, and the buffer-fit check behind the #17 length validation -- including boundaries and nulls |
| `test_webconfig_keys` | `src/helpers/WebConfigKeys.h` | POST-key allowlist, secret detection, admin-password and browser-terminal command validation, slot-index bounds, and short-input guards |
| `test_webconfig_batch` | `src/helpers/WebConfigBatch.h` | config POST/replay/busy decisions; paced command drain; setup WiFi validation/IP handoff; reboot confirmation/fallback; stop/refcount gating; exact timing and rollover boundaries |
| `test_topic_template` | `src/helpers/MQTTTopicTemplate.h` | `{iata}/{device}/{token}/{type}` expansion, overflow/NUL-termination, and a buffer-size fuzz |
| `test_mqtt_topic_router` | `src/helpers/MQTTTopicRouter.h` | complete preset/custom topic-routing contract; MeshRank all types except raw; required identifiers; invalid inputs/slots; exact buffer boundaries |
| `test_mqtt_connection_policy` | `src/helpers/MQTTConnectionPolicy.h` | reconnect guard/backoff/stagger and breaker transitions; stable reset; JWT lifetime/renewal policy; exact timing boundaries and 32-bit `millis()` rollover |
| `test_wifi_reconnect_policy` | `src/helpers/WiFiReconnectPolicy.h` | five-minute forced reconnect cadence, connection resets, duplicate disconnect observations, and 32-bit `millis()` rollover |
| `test_radio_liveness` | `src/helpers/RadioLivenessTracker.h`, `src/helpers/radiolib/RxBoostedGainDefaults.h` | staged radio recovery, activity reset and rollover handling; target-specific RX boosted-gain defaults and SX126x precedence |
| `test_lr1110_rx_recovery` | `src/helpers/radiolib/LR1110RxRecovery.h` | LR1110 four-byte RX-buffer shift signature; captured and accumulated shifts; ordinary/scoped packet exclusions |
| `test_mqtt_packet_queue_policy` | `src/helpers/MQTTPacketQueuePolicy.h` | queue-full eviction; stale-disconnect flush; adaptive drain limits; bounded QoS0 retries; exact timing boundaries and 32-bit `millis()` rollover |
| `test_mqtt_packet_filter` | `src/helpers/MQTTPacketFilter.h` | per-slot 0-15 allowlist parsing/formatting, numeric and named spellings; exact bounds; membership; candidate/eligible split and retry-completion policy; pre-queue union gate; default-mask detection |
| `test_mqtt_runtime_buffer_lifecycle` | `src/helpers/MQTTRuntimeBufferLifecycle.h` | idempotent allocation/release; partial-allocation degradation; retry of only missing buffers |
| `test_mqtt_prefs_codec` | `src/helpers/MQTTPrefsStorage.h`, `src/helpers/MQTTPrefsCodec.h` | binary pre-slot/3-slot/6-slot migration fixtures; v1 header integrity; downgrade preservation; shortest-payload write policy (default filters stay downgrade-readable) |
| `test_mqtt_prefs_atomic_store` | `src/helpers/MQTTPrefsAtomicStore.h` | transactional MQTT writes and legacy `/node_prefs` handoff; exact short-write detection; begin/finish/rename failure cleanup; original-file preservation |
| `test_prefs_save_routing` | `src/helpers/PrefsSaveRouting.h` | runtime common/observer setters write only their owning preference image; mixed-owner setters and migrations can deliberately write both |
| `test_mqtt_payload_builder` | `src/helpers/MQTTPayloadBuilder.cpp` | status/packet/raw JSON contracts; optional fields; escaping; RX metrics and path; score handling; exact buffer bounds; maximum representative payloads |
| `test_telemetry_history` | `src/helpers/TelemetryHistory.h` | 30-minute rings; seven-day temperature/voltage and dynamically sized GPS retention; exact 1 C temperature/status encoding; separate Base64 series payloads; 14-bit GPS differentials; resize preservation, heap budgets, and 1-based paging bounds |
| `test_flood_filter_policy` | `src/helpers/FloodFilterPolicy.h` | unordered blacklist matching; ordered 1/2/3-byte pbyte rule prefixes; original incoming scope classes and canonical region-name identity; channel-authentication cache key comparison; priority ordering and terminal stop masks; bridge-bucket and regionless channel-target selector encoding; `require=region` and per-channel scope-gate truth tables; fast/slow timing; adding, replacing, and preserving packet scope |
| `test_logical_message_cache` | `src/helpers/LogicalMessageCache.h` | bounded logical-message mapping; stable retry timestamps; exact older retries after newer messages; stale and same-timestamp mismatch rejection |
| `test_cli_command_utils` | `src/helpers/CLICommandUtils.h`, `src/helpers/ContactListOrder.h`, `src/helpers/TerminalCommandTracker.h`, `src/helpers/TerminalDisplayFilter.h` | terminal verb/argument/path parsing; routed receive labels; quiet display defaults and independent emergency filtering; favorite-first contact ordering; single-command reply matching, round-trip timing, and rollover-safe expiration |
| `test_identity_generation` | `src/helpers/IdentityGeneration.h` | reserved-prefix rejection; bounded retries; final provisioned attempt; fail-closed exhaustion |
| `test_remote_cli_reply_cache` | `src/helpers/RemoteCliReplyCache.h`, `src/helpers/RemoteCliRequest.h`, `src/helpers/RemoteCliTimeout.h` | authenticated logical-request matching; bounded recent-reply history; backward-compatible retry identity; 300% response timeout; empty-response completion; on-air truncation and clearing |
| `test_companion_frame_queue` | `src/helpers/CompanionFrameQueue.h` | response/required/best-effort classification; reserved capacity; stable priority; safe eviction; message-waiting coalescing |
| `test_serial_mode_switch` | `src/helpers/ArduinoSerialInterface.cpp`, `src/helpers/MultiSerialInterface.h` | independent terminal/seeder control-sequence recognition across reads and binary-frame boundaries; passthrough ownership of USB input and suppression of binary output; Bluetooth-only connection and pairing-request routing |
| `test_ble_tx_stall_watchdog` | `src/helpers/BleTxStallWatchdog.h` | exact BLE fragment progress; blocked-reply timeout; rollover-safe elapsed time; disconnect recovery retry and completion |
| `test_ota` | `src/helpers/ota/` | container and EndF integrity; protocol codecs; transfer, resume, and apply safety; adaptive 2-to-4 block-request window growth and stall contraction; active-transfer priority classification |
| `test_trace_retry` | `src/Mesh.cpp` retry and relay policy | opaque OTA relay behavior during TempRadio; background discovery priority; immediate primary transfer relay, receive-delay bypass, fast CAD retry, and no generic flood retry; trace and non-OTA flood retry timing |
| `test_utils` | `src/Utils.cpp` | `Utils::toHex` (upstream) |

## Conventions (and how to add a suite)

- Each `test/test_<name>/` directory builds into its **own** GoogleTest program
  and must define its own `main()` (`::testing::InitGoogleTest` + `RUN_ALL_TESTS`).
- Tests are **host-only**: include only pure headers. Arduino/crypto stubs live
  in `test/mocks/` (on the include path via `-I test/mocks`).
- Firmware headers are included from `src` (via `-I src`, e.g.
  `#include "helpers/MQTTPresets.h"`). Some are guarded or ESP-flavored, so a
  suite may need shims **before** the include -- e.g. `test_mqtt_presets` does
  `#define WITH_MQTT_BRIDGE 1` (the preset table is behind that flag) and
  `#define PROGMEM` (the embedded CA-cert strings are PROGMEM-qualified).
- To add a suite: create `test/test_<name>/test_<name>.cpp` with a `main()`, and
  add any host-only source it links to the `native` env's `build_src_filter` in
  `platformio.ini` (header-only code needs no source entry). No other wiring.
- Keep logic testable by extracting pure functions into headers (as
  `MQTTObserverValidation.h` / `WebConfigKeys.h` / `MQTTTopicTemplate.h` do) and
  having the firmware call the same functions.
