# MQTT Bridge Cross-Core Ownership Model

> **Historical design record, status reconciled 2026-08-18.** The ownership
> analysis was written during Phases 4-5. File/line references describe that
> snapshot and must be re-located before editing current code. Cooperative
> shutdown and the OTA barrier are implemented and hardware-characterized;
> immutable status publication and replacement of the remaining volatile
> NTP/reconfigure handshakes are still deferred.

This document is the Phase 4 deliverable from `STABILITY_TESTABILITY_HANDOFF.md`:
it records **one owner for each mutable runtime domain**, maps every place a
non-owner reads owned state across cores today, and states the target primitive
for each. It is paired with the fork-owned lifecycle test seam
(`src/helpers/MQTTLifecycle.h`, `test/test_mqtt_lifecycle/`).

**Status:** ownership model documented and the lifecycle/teardown test seam
landed (Phase 4). **Phase 5 (branch `phase5/cooperative-mqtt-shutdown`) has now
implemented the cooperative shutdown and the OTA barrier -- hazard Section 4 below.**
Still **deferred** (carried to Phase 5b / Phase 6): publishing a plain-data
snapshot and repointing the Section 1/Section 2 consumers, and replacing the Section 3 `volatile`
handshakes with a command channel. This document is a design record; Sections
1-3 describe hazards that still exist conceptually, while Section 4 is resolved.
Line
references are against the tree at the time of writing and should be re-verified
before editing.

## Execution contexts

- **MQTT task -- Core 0** (`"MQTTBridge"`, `xTaskCreatePinnedToCore(..., MQTT_TASK_CORE=0)`,
  entry `mqttTask` -> `mqttTaskLoop`, `MQTTBridge.cpp:923`, `:991`). Owns all
  WiFi / MQTT / NTP I/O and every mutation of `_slots[]`, connection state, and
  NTP state.
- **Loop task -- Core 1** (`MyMesh::loop()`). Runs the CLI, WebConfig `tick()`,
  and `AlertReporter::onLoop()`. `examples/simple_repeater/MyMesh.cpp:1529`
  notes the bridge loop is *not* called here -- it lives on Core 0.
- **Producer / radio context.** Stages raw radio bytes before queue handoff
  (`storeRawRadioData` -> `_staged_*`, consumed by `queuePacket`; both Core 1, in
  guaranteed sequence, `MQTTBridge.h:232-239`).
- **Async TCP context.** WebConfig request parsing and immutable response
  handoff only.

## Ownership model (one owner per mutable domain)

| Domain | Owner | Notes |
|--------|-------|-------|
| MQTT clients, slot connection state, publish counters, packet drain, NTP I/O | **MQTT task (Core 0)** | `_slots[]`, `PsychicMqttClient`s, `_ntp_client`, `_last_raw_*` |
| CLI execution, preference persistence, WebConfig batch draining, bridge lifecycle requests | **Loop task (Core 1)** | issues start/stop/reconfigure, drains the WebConfig batch |
| Packet staging before queue handoff | **Producer / radio (Core 1)** | `_staged_*`, no lock needed (sequential) |
| Request parse + immutable response | **Async TCP** | must not touch mutable bridge state |

The rule that follows: **loop/WebConfig/CLI/AlertReporter code must not directly
inspect mutable MQTT slot objects or client counters.** The MQTT task must
publish a plain-data snapshot they can read instead.

## Remaining cross-core hazards after the minimal Phase 5 change

All of the following run on **Core 1** and read state mutated by **Core 0**
without a lock or a published snapshot.

### 1. Diagnostic reads of live `_slots[]` via the singleton

Four `static` accessors reach the live object through the file-scope
`s_mqtt_bridge_instance` (`MQTTBridge.cpp:201`, set at `begin()` end `:849`,
nulled first in `end()` `:858` -- a plain, non-atomic pointer):

- `getSlotStatusSnapshot` (`:299`) -- despite its name, built on demand from
  live `_slots[slot_index]`. **Refined premise:** the returned `name`/`state`
  `const char*`s point at static rodata (string literals / `MQTT_PRESETS[]`),
  so they are *not* dangling. The real hazards are (a) reading the
  `slot.preset` **pointer value**, which Core 0 can null/reassign mid-read
  (`applySlotPreset`, `teardownSlot`), and (b) `slot.client->getPublishOk()`,
  a live client pointer Core 0 can `delete` during teardown (UAF window).
- `formatMqttStatusReply` (`:207`), `formatMqttStatsReply` (`:263`),
  `formatSlotDiagReply` (`:397`) -- same singleton + live `_slots[]` reads.

Consumers: CLI (`CommonCLI_Observer.cpp:726, :728, :791`) and the app-layer
stats JSON (`examples/simple_repeater/MyMesh.cpp:1423`, mirrored in
`examples/simple_room_server/MyMesh.cpp:1050`). **Refined premise:**
`WebConfigServer.cpp` itself reads only a compile-time constant
(`getMaxActiveSlots`, `:479`); the live-bridge web reads are in `buildStatsJson`
in the `MyMesh.cpp` app layer.

### 2. Instance reads that survive `end()`

`AlertReporter` (Core 1, `onLoop`, `AlertReporter.cpp:208`) reads live `_slots[]`
via instance methods it reaches through its own `_bridge` pointer
(`AlertReporter.cpp:281` `isSlotEnabledAndAttempted`, `:285`
`getSlotCurrentOutageStartMs`, `:296`/`:311` `getSlotPresetName`). The stats
JSON also calls `bridge->getQueueSize()` (`MyMesh.cpp:1419`).

**Refined premise (teardown hazard):** `end()` nulls only
`s_mqtt_bridge_instance`. The app's `bridge` pointer and `AlertReporter::_bridge`
are **not** cleared by `end()`, so these instance reads can touch a torn-down
bridge. (`getConnectedBrokers()` at `MQTTBridge.cpp:3676` is defined but has
zero consumers.)

### 3. `volatile` cross-core handshakes

Plain `volatile` flags (no atomics/barriers), Core 1 sets the request, Core 0
clears/processes and writes a "done" flag last, Core 1 spins:

- `_slot_reconfigure_pending[]` -- set `MQTTBridge.cpp:2057` (Core 1), read/clear
  `:1118` (Core 0).
- `_ntp_force_{requested,done,result}` -- request `:3298`, process `:1063-1068`,
  spin-wait `:3308-3315`.
- `_ntp_diag_{requested,done}` (+ non-volatile `_ntp_diag_results[]`) -- request
  `:3344`, process `:1073-1076`, spin-wait `:3347`.

The two blocking waiters (`requestForcedNtpSync` `:3298`, `ntpDiag` `:3344`) spin
on Core 1 while `end()` could tear down the singleton/task concurrently.

### 4. Abrupt teardown, no start guard -- RESOLVED in Phase 5

Original hazard (retained for context): `end()` used
`vTaskDelete(_mqtt_task_handle)` to kill the task wherever it was (possibly
mid-`_slots[]` mutation or inside mbedTLS), then ran slot/client cleanup *after*
deletion on the caller's context -- the OTA teardown heap-panic path. `begin()`
had no double-call guard, and lifecycle state was a single `_initialized` bool.

**Phase 5 resolution** (branch `phase5/cooperative-mqtt-shutdown`):

- `end()` now requests a cooperative stop through `MQTTLifecycle::Coordinator`.
  The MQTT task (Core 0) tears down its own clients where the mbedTLS contexts
  live, acknowledges via `_stop_acked`, and self-terminates; `end()` waits for
  the ack before freeing the queue/buffers. The blind `vTaskDelete` survives
  only as the bounded-timeout fallback, which sets a dirty latch that withholds
  OTA flashing (`canFlashAfterStop()`).
- `begin()` has an idempotent double-call guard and drives the Coordinator to
  `Running`; the lifecycle state now lives in the tested state machine, not a
  bare bool.
- The OTA barrier gates `simple_repeater`'s deferred flash on a clean stop.

The shutdown path and timeout were subsequently hardware-characterized. The
flat 8-second placeholder was replaced by a per-stop budget of 5 seconds plus
8 seconds per enabled slot. A two-WSS-slot stop that previously timed out
acknowledged cleanly in about 11.7 seconds within its 21-second budget. The
residual Section 1/Section 2 instance-pointer reads remain deferred, so those
consumers can still touch live or torn-down bridge state as tracked above.

## Remaining target primitives

- **Task notifications or a command queue** for one-way lifecycle / reconfigure
  / NTP requests -- replacing every `volatile` flag in Section 3.
- **Immutable published snapshots** for WebConfig, CLI diagnostics, and alerting
  -- the MQTT task publishes a plain-data `SlotStatusSnapshot` (owned char
  buffers + scalars, no live pointers) that Section 1/Section 2 consumers read. This also
  removes the "instance pointer survives `end()`" hazard because consumers stop
  dereferencing the live bridge.
- **Atomics** only for truly independent scalar state.
- **A mutex** only where ownership transfer or snapshot publication cannot
  express the operation cleanly.

## Lifecycle contract (the test seam)

`src/helpers/MQTTLifecycle.h` encodes the cooperative lifecycle implemented by
Phase 5 as a pure state machine plus a narrow injected `Ops` seam
(clock / task control / resource owner / OTA barrier). The invariants proven by
`test/test_mqtt_lifecycle/` and preserved by the production wiring are:

- `Stopped -> Starting -> Running -> StopRequested -> Stopping -> Stopped`.
- Idempotent start and stop; safe restart only from `Stopped` (`mayRestart`).
- New connects/publishes/retries/reconfigurations cease once a stop is requested
  (`acceptsNewWork`).
- Resources are released **only** after a stop acknowledgment (or the reviewed
  timeout fallback) -- never mid-run.
- A late/stale callback consults `mayTouchOwnedState()` and is a no-op once the
  owner has released resources.
- Bounded stop timeout -> reviewed fallback (models replacing the abrupt
  `vTaskDelete`).
- **OTA barrier:** `mayBeginFlash()` is true only after a **clean** stop
  acknowledgment; a timed-out stop leaves flashing blocked so OTA aborts rather
  than writing under uncertain ownership.

### Hardware characterization result

WSS teardown was measured as roughly 5-6 seconds per slot and is sequential.
Production now sets the coordinator timeout to `5 s + 8 s * enabled_slots`.
Representative non-PSRAM and PSRAM start/stop matrices found no downward heap
or largest-block trend; multi-day soak, task-stack high-water, and exhaustive
callback ordering remain outside that run.

## Implementation status after Phase 5

- Publishing the plain-data snapshot and repointing the Section 1/Section 2 consumers at it.
- Replacing the Section 3 `volatile` handshakes with a command queue / task
  notifications.
- **Completed:** the cooperative shutdown state machine in `MQTTBridge`, the
  `begin()` double-call guard, slot-scaled timeout, and OTA teardown barrier.

Historical note: `MQTTBridge.cpp` was intentionally untouched during Phase 4;
Phase 5 later made the planned lifecycle changes as one reviewable unit.
