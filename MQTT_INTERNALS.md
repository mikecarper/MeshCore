# MQTT Bridge Internals

Developer-facing notes on how the MQTT observer feature is structured in the codebase: source files, the seams that keep it isolated from upstream MeshCore code, and how on-device settings are migrated across firmware versions. For user-facing setup and CLI reference, see [MQTT_IMPLEMENTATION.md](MQTT_IMPLEMENTATION.md).

## Files

### Core Implementation
- `src/helpers/bridges/MQTTBridge.h` - MQTT bridge class definition
- `src/helpers/bridges/MQTTBridge.cpp` - MQTT bridge implementation
- `src/helpers/MQTTPresets.h` - Preset definitions, CA certificates, and lookup functions
- `src/helpers/MQTTDefaults.h` - Compile-time defaults for fresh `/mqtt_prefs`
- `src/helpers/MQTTMessageBuilder.h` - JSON message formatting utilities
- `src/helpers/MQTTMessageBuilder.cpp` - JSON message formatting implementation
- `src/helpers/JWTHelper.h` - JWT token generation for Ed25519-based authentication
- `src/helpers/CommonCLI_Observer.cpp` - All observer CLI command handling (MQTT, WiFi,
  timezone, NTP, OTA, SNMP, alerts)

### Integration seams with upstream code

The observer feature is kept out of upstream-tracked files through three mechanisms:

- **CLI hook methods** - upstream `CommonCLI.cpp` delegates to three `CommonCLI`
  methods defined in the fork-owned `CommonCLI_Observer.cpp`: `handleObserverCommand()`,
  `handleObserverSetCmd()`, and `handleObserverGetCmd()`. Each returns `true` if it
  consumed the command, otherwise the upstream parser runs. Only these three call
  sites touch upstream CLI code.
- **Callback virtuals** - observer behaviour needed from the application is exposed
  as default-no-op virtuals on `CommonCLICallbacks` (e.g. `restartBridgeSlot`,
  `isMqttBridgeRunning`, `syncMqttNtp`, `onAlertConfigChanged`, `sendAlertText`,
  `resolveAlertScope`, `beginDeferredOtaUpdate`). The example apps override them
  behind `#ifdef WITH_MQTT_BRIDGE`.
- **Separate settings file** - observer settings (MQTT slots, WiFi, timezone, SNMP,
  radio watchdog, fault alerts) live in the `MQTTPrefs` struct persisted to
  `/mqtt_prefs`, keeping `NodePrefs` / `/com_prefs` aligned with the upstream layout.

Remaining integration points in upstream files:
- `examples/simple_repeater/MyMesh.{h,cpp}`, `examples/simple_room_server/MyMesh.{h,cpp}` -
  bridge/alerter/SNMP wiring and packet-feed hooks, guarded by `#ifdef WITH_MQTT_BRIDGE`;
  plus the `createObserverPacketManager()` call in each constructor (see below)
- `src/helpers/CommonCLI.{h,cpp}` - the three CLI hooks, `MQTTPrefs` load/save/migration
- `src/Dispatcher.{h,cpp}` - radio watchdog block, guarded by `#ifdef WITH_MQTT_BRIDGE`

### Capture vs. duty-cycle throttling

RX processing needs a free packet from the static pool before `logRx()` (and thus the
MQTT uplink) can run - `Dispatcher::checkRecv()` silently discards received data when
the pool is empty. Because the outbound queue holds pool packets with no expiry,
duty-cycle throttling can park the entire pool waiting on TX budget, capping capture at
the TX rate - and the parked repeats absorb every budget refill, starving the node's
own CLI responses and making it un-administrable over the mesh. Observer builds
therefore use `RxReservePacketManager` (fork-owned,
`src/helpers/RxReservePacketManager.h`): below the RX reserve (a quarter of the pool)
it sheds only low-priority outbound (multi-hop flood repeats, adverts, trace), keeping
the node's own responses/ACKs queueable; below a smaller emergency floor it sheds
everything to keep capture alive. Queued packets still untransmitted 30 s past their
scheduled time are expired at dequeue, so under throttle the queue holds only fresh
traffic and admin responses reach the trickle of TX budget. Non-observer builds keep
the upstream pool behavior.

### Neighbors publication path (PSRAM only)

Periodic neighbors publishing is gated on `WITH_MQTT_NEIGHBORS`
(`defined(BOARD_HAS_PSRAM) && defined(MAX_NEIGHBOURS) && MAX_NEIGHBOURS > 0`,
defined in `MQTTBridge.h`). It spans two subsystems and two cores:

- **Mesh side (Core 1), `MyMesh`**: the `loop()` runs a two-stage refresh driven by
  `mqtt_neighbors_interval`. Stage 1 sends a zero-hop `sendNodeDiscoverReq()` and waits
  out its 60 s collection window to refresh `neighbours[]`. Stage 2
  (`startNeighborDiscover`) fires one anon-regions scope query per heard neighbor,
  overlaying them onto the peer-index space at `NEIGHBOR_DISCOVER_PEER_BASE` so their
  `PAYLOAD_TYPE_RESPONSE` packets decrypt via `searchPeersByHash`/`getPeerSharedSecret`/
  `onPeerDataRecv` even when the neighbor is not an ACL client. After all responses land
  or a 30 s window expires, `finishNeighborDiscover()` builds the JSON with
  `MQTTMessageBuilder::buildNeighborsMessage` into a transient PSRAM buffer and hands it
  to the bridge.
- **Bridge side, handoff**: `requestPublishNeighbors(json, len)` (Core 1) memcpys into a
  persistent ~10 KB PSRAM buffer (`NEIGHBORS_JSON_BUFFER_SIZE`) and sets
  `_neighbors_publish_pending` with a release store; the MQTT task (`mqttTaskLoop`, Core 0)
  consumes it with an acquire load, calls `publishNeighbors()`, and clears the flag. A
  second snapshot is dropped while one is in flight. `publishNeighbors()` sends QoS 1,
  retain = `preset->allow_retain` (custom slots non-retained). MeshRank slots are skipped
  (the topic router rejects non-packets for MeshRank).
- **Status reporting**: `MyMesh` reports the schedule each loop via
  `setNeighborsSchedule(phase, secs)`; `formatMqttStatusReply` renders it as the trailing
  `nbr: <when>/<last>` field in `get mqtt.status` while the feature is enabled.

The JSON builder lives in the pure, host-tested `MQTTPayloadBuilder`
(`test/test_mqtt_payload_builder`); the topic type in `MQTTTopicRouter`
(`test/test_mqtt_topic_router`). The mesh<->bridge orchestration above is on-target only.

### `/mqtt_prefs` file format

`/mqtt_prefs` is written with an 8-byte `MQTTPrefsHeader` (`magic`, `version`,
`payload_len`) followed by the raw `MQTTPrefs` payload. The magic is
`{0xF5, 'M', 'Q', 'P'}` - its leading non-ASCII byte can never collide with the first
bytes of a legacy (headerless) file, whose payload begins with the `mqtt_origin`
string. Bump `MQTT_PREFS_VERSION` when the payload layout changes incompatibly; a file
whose version this firmware doesn't recognize is left untouched and the in-memory prefs
fall back to defaults (no downgrade, no misread). `saveMQTTPrefs()` also refuses to
write while such a file is present (`_mqtt_prefs_hold`), so a `set` command after a
firmware downgrade can't clobber the newer config - observer settings changed in that
state simply don't persist. The frozen legacy layouts are pinned with `static_assert`s
in `CommonCLI.h`, so every target build re-verifies the fleet's file offsets.

Adding a field to the current version stays backward compatible: append it to the end
of `MQTTPrefs`. An older, shorter payload still loads and the missing tail keeps its
default; a newer, longer one is truncated harmlessly.

### Settings upgrade / migration

`loadPrefs()` handles every historical on-device format one-time at boot:
- **`/mqtt_prefs`** - if the file has the version header it is read directly. Otherwise
  it is a legacy headerless file and its layout is detected by size: pre-slot
  (`OldMQTTPrefs`), 3-slot (`ThreeSlotMQTTPrefs`), or the 6-slot layout shipped on
  `observer-firmware` back when it was named `mqtt-bridge-implementation-flex`
  (`Legacy6SlotMQTTPrefs`). Each is field-copied into
  the current compact `MQTTPrefs` and re-saved with the version header - which also
  drops the vestigial `_legacy_*` fields the flex layout carried mid-struct. This is a
  one-time rewrite; every deployed device performs it on its first boot of versioned
  firmware, after which all reads take the header path.
- **`/com_prefs`** - a file written by fork firmware that predates the `MQTTPrefs` split
  (a zero-filled MQTT gap plus a trailing observer block) is detected by size; the
  trailing SNMP / radio-watchdog / fault-alert settings and the `rx_boosted_gain` /
  `flood_max_*` fields are recovered, carried into `/mqtt_prefs`, and both files are
  rewritten in the current formats.
- Settings the pre-split firmware stored *inside* the `/com_prefs` MQTT gap (the MQTT
  slot/WiFi config itself) are **not** recovered - users upgrading from firmware that
  old must re-enter their MQTT and WiFi configuration.
