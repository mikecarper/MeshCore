# Host unit tests

Fast, hardware-free unit tests for pure and host-simulated logic, run with
GoogleTest through PlatformIO. The `native` environment runs every suite except
the KISS modem; `native_kiss_modem` builds that suite with its separate source
filter. Hardware, real radio, AsyncTCP, Wi-Fi/MQTT, and SoftAP behavior still
require integration or target testing; see "Local testing without hardware" in
`MQTT_IMPLEMENTATION.md`.

## Running

```sh
pio test -e native                      # all suites except KISS modem
pio test -e native_kiss_modem           # KISS modem suite
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
| `test_telemetry_history` | `src/helpers/TelemetryHistory.h`, `ExternalVoltageHistory.h` | 30-minute rings; seven-day temperature/battery and four-day multi-channel I2C voltage retention; exact 1 C temperature/status and 0.02 V packed voltage encodings; zero-only channel omission; Base64 pages and raw chunks; GPS differentials, resize preservation, heap budgets, and paging bounds |
| `test_flood_filter_policy` | `src/helpers/FloodFilterPolicy.h` | unordered blacklist matching; ordered 1/2/3-byte pbyte rule prefixes; original incoming scope classes and canonical region-name identity; channel-authentication cache key comparison; priority ordering and terminal stop masks; bridge-bucket and regionless channel-target selector encoding; `require=region` and per-channel scope-gate truth tables; fast/slow timing; adding, replacing, and preserving packet scope |
| `test_logical_message_cache` | `src/helpers/LogicalMessageCache.h` | bounded logical-message mapping; stable retry timestamps; exact older retries after newer messages; stale and same-timestamp mismatch rejection |
| `test_cli_command_utils` | `src/helpers/CLICommandUtils.h`, `src/helpers/ContactListOrder.h`, `src/helpers/TerminalCommandTracker.h`, `src/helpers/TerminalDisplayFilter.h` | terminal verb/argument/path parsing; routed receive labels; quiet display defaults and independent emergency filtering; favorite-first contact ordering; single-command reply matching, round-trip timing, and rollover-safe expiration |
| `test_identity_generation` | `src/helpers/IdentityGeneration.h` | reserved-prefix rejection; bounded retries; final provisioned attempt; fail-closed exhaustion |
| `test_remote_cli_reply_cache` | `src/helpers/RemoteCliReplyCache.h`, `src/helpers/RemoteCliRequest.h`, `src/helpers/RemoteCliTimeout.h` | authenticated logical-request matching; bounded recent-reply history; backward-compatible retry identity; 300% response timeout; empty-response completion; on-air truncation and clearing |
| `test_companion_frame_queue` | `src/helpers/CompanionFrameQueue.h` | response/required/best-effort classification; reserved capacity; stable priority; safe eviction; message-waiting coalescing |
| `test_companion_status_response` | `src/helpers/CompanionStatusResponse.h` | request-tag correlation and minimum status-response length, including rejection of the three-entry ACL payload that previously masqueraded as status |
| `test_serial_mode_switch` | `src/helpers/ArduinoSerialInterface.cpp`, `src/helpers/MultiSerialInterface.h` | terminal/seeder control-sequence recognition and passthrough ownership; queued/atomic USB output under backpressure and short writes; partial-frame busy state; requester-affine replies, locked contact streams, and Bluetooth-only pairing routing |
| `test_ble_tx_stall_watchdog` | `src/helpers/BleTxStallWatchdog.h` | exact BLE fragment progress; blocked-reply timeout; rollover-safe elapsed time; disconnect recovery retry and completion |
| `test_atomic_file_writer` | `src/helpers/AtomicFileWriter.h` | verified temporary-file commit; short-write, readback, validation, and rename failures; preservation of the live file and stale-temp cleanup |
| `test_cad_timing` | `src/helpers/radiolib/CadTiming.h`, `LR2021SideDetectorConfig.h`, `RadioAirtime.h` | Cascade and slow-profile CAD deadlines; invalid airtime handling; bounded LR2021 side-detector parsing and LDRO recomputation |
| `test_companion_node_prefs` | `examples/companion_radio/NodePrefs.h` | independent device power saving, RXPS, Wi-Fi, and FEM preferences; one-time migration of the regressed power-saving default |
| `test_config_serializer` | `src/helpers/ConfigSerializer.cpp`, Companion `NodePrefs` | escaped config save/load, whitespace and malformed input, unknown fields, and FEM preference round trips |
| `test_deferred_cli_command` | `src/helpers/DeferredCliCommand.h` | copying authenticated command context, single-pending-command enforcement, clearing, and length rejection |
| `test_host_cli_bridge` | `src/helpers/HostCliBridge.h` | bounded request/reply parsing, Base64URL serial framing, correlation preservation, one-time service-claim proofs, request-token fields, line safety, and UTF-8-safe truncation |
| `test_kiss_modem` | `examples/kiss_modem/KissModem.cpp` | KISS escaping/framing and packet metadata under partial writes, host TX backpressure, queue saturation, and radio completion; run with `native_kiss_modem` |
| `test_mesh_tables` | `src/helpers/SimpleMeshTables.h` | packet and ACK/multipart deduplication, scope-independent identity, route-prefix matching, and deterministic recent-repeater expiry/eviction |
| `test_mqtt_lifecycle` | `src/helpers/MQTTLifecycle.h` | idempotent start/stop, initialization rollback, cooperative stop acknowledgment and timeout, callback ownership, restart, and the OTA flash barrier |
| `test_mqtt_reply_format` | `src/helpers/MQTTReplyFormat.h` | bounded formatted appends, exact-fit and one-byte buffers, truncation, NUL termination, and invalid starting positions |
| `test_packet_manager` | `src/Packet.cpp`, `src/Dispatcher.cpp`, `src/helpers/StaticPoolPacketManager.cpp` | truncated-packet rejection, unavailable-radio behavior, scoped RX-delay replacement, queue/CAD scheduling, and staged radio/TX recovery |
| `test_persistent_store_format` | `src/helpers/PersistentStoreFormat.h` | contact-page headers and CRCs, dirty-page state, stable slot allocation, and bounded resumable legacy migration across power loss |
| `test_power_management` | `src/helpers/PowerManagementUtils.h` | median filtering of a brownout outlier and valid-reading requirements for the boot lock |
| `test_rx_power_saving` | `src/helpers/radiolib/RXPowerSaving.h` | level-derived timing, tuple-selected 32/64/128-symbol wire preambles, equivalent SF7/BW500, SF6/BW250, and SF5/BW125 profiles, SF5/BW250 and SF6/BW500 level-8/64 timing, SF5/BW500 level-8/128 timing, SF5/BW62.5 with a 16-symbol timing assumption, automatic retuning, and SX1262 TCXO timing thresholds |
| `test_region_names` | `src/helpers/RegionNameUtils.h` | canonical public-region markers while preserving distinct private and differently named regions |
| `test_routing_policy` | `src/helpers/RoutingPolicy.h` | scoped/unscoped flood hop limits and selection of direct, path-return, mirrored-scope, default-scope, or unscoped replies |
| `test_rs232_uart` | `src/helpers/bridges/RS232UartUtils.h` | stopping the active UART peripheral before reassigning its pins |
| `test_security_session_timer` | `src/helpers/nrf52/SecuritySessionTimer.h` | two-minute security-session expiry, cancellation, restart, and `millis()` rollover |
| `test_trace_path_helpers` | `src/helpers/TracePathHelpers.h` | round-trip route construction, hash-width conversion, raw path parsing/limits, and terminal trace timeout bounds |
| `test_user_gpio` | `src/helpers/UserGpio.cpp`, `UserGpioReplyTracker.h` | board-approved pins, get/set/reset, timed nonblocking transitions, duplicate suppression, rollover, and completion-reply routing |
| `test_utf8_helpers` | `src/helpers/UTF8Helpers.h` | byte-limit truncation at complete code-point boundaries and rejection of malformed or truncated UTF-8 |
| `test_wifi_ota_seeder_policy` | `src/helpers/WiFiOtaSeederPolicy.h`, `WiFiOtaSeederStatus.h` | listener state versus network availability, serial/TCP folder ownership, detach detection, and bounded status formatting |
| `test_ota` | `src/helpers/ota/` | v2 application/v3 bootloader parser separation; legacy XIAO, generic internal, and exact MeshTower V2 SD embedded identity, vector, capability, explicit-confirmation, codec-isolation, scratch-headroom/shared-slot no-EndF gates, and no-autofetch gates; container and EndF integrity; protocol codecs; transfer, resume, and layered apply safety; adaptive 2-to-4 block-request window growth and stall contraction; active-transfer priority classification |
| `test_trace_retry` | `src/Mesh.cpp`, `RTCClock`, `ClockSyncUtils.h`, retry and relay policy | app-v2 and boot-v3 traffic sharing `PAYLOAD_TYPE_OTA=0x0C` and the TempRadio suspend policy; opaque OTA relay behavior; background discovery priority; immediate primary transfer relay, receive-delay bypass, fast CAD retry, and no generic flood retry; trace and non-OTA flood retry timing; backward RTC correction; clock consensus/path policy and the 10-minute default drift threshold |
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
