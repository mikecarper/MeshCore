#pragma once

#include <ArduinoJson.h>
#include <stddef.h>
#include <stdint.h>
#include "Radio2DiscoverTrailer.h"

// A healthy slot reports sibling outages because a disconnected slot cannot
// report itself. Defaults omit the fields for existing callers.
struct MQTTConnHealth {
  int slots_up = -1;
  int slots_total = -1;       // enabled and fully configured, connected or not
  int worst_outage_secs = -1; // no timed outage until the first DISCONNECTED
  int heap_largest = -1;      // trend sampled at publish time, not per attempt
  int connect_failures = -1; // failed attempts/starts across all slots since boot
  int slots_breaker = -1;
};

// radio2 (MediaMesh) as an observer reports it: the node's own second profile
// in the status `radios` list and the neighbors message's `self.radio2`, or a
// neighbour's profile from its DISCOVER_RESP trailer. `radio` is
// "MHz,kHz,SF,CR" like the status `radio` field (nullptr when mode is "off");
// `mode` is "rx", "rxtx" or "off". No default initializers: aggregate.
struct MQTTRadio2Info {
  const char* radio;
  const char* mode;
  bool temporary;
};

// A neighbour's DISCOVER_RESP trailer as the neighbors message's `radio2`:
// nullptr when it sent none (the key is then left out), `radio` only when on.
inline const MQTTRadio2Info* radio2InfoFor(const Radio2DiscoverTrailer& t, MQTTRadio2Info& out,
                                           char* radio, size_t radio_size) {
  if (t.state == Radio2DiscoverTrailer::Unknown) return nullptr;
  out.mode = t.modeName();
  out.temporary = t.temporary;
  out.radio = nullptr;
  if (t.state == Radio2DiscoverTrailer::On && radio && radio_size) {
    t.formatRadio(radio, radio_size);
    out.radio = radio;
  }
  return &out;
}

// The id a radio2 packet carries (`rx_radio_id`/`tx_radio_id`), and the status
// `radios` entry's id: the multi-radio convention CoreScope reads (#1869).
static const char* const kMQTTRadio2Id = "radio2";

// Mesh-independent JSON serialization core for MQTT publication payloads.
// MQTTMessageBuilder keeps the firmware-facing API and delegates these three
// deterministic contracts here so they can be exercised by native tests.
class MQTTPayloadBuilder {
public:
  static int buildStatusMessage(
    JsonDocument& doc,
    const char* origin,
    const char* origin_id,
    const char* model,
    const char* firmware_version,
    const char* radio,
    const char* client_version,
    const char* status,
    const char* timestamp,
    char* buffer,
    size_t buffer_size,
    int battery_mv = -1,
    int uptime_secs = -1,
    int errors = -1,
    int queue_len = -1,
    int noise_floor = -999,
    int tx_air_secs = -1,
    int rx_air_secs = -1,
    int recv_errors = -1,
    int internal_heap = -1,
    int packets_sent = -1,
    int packets_received = -1,
    const char* repeat = nullptr,
    const MQTTConnHealth& conn_health = MQTTConnHealth(),
    // radio2 on (rx or rxtx, MeshCore): adds `radios: [{id: "radio2", radio,
    // mode, mediamesh: true, temporary}]`. nullptr (radio2 off) adds nothing.
    const MQTTRadio2Info* radio2 = nullptr
  );

  static int buildPacketMessage(
    JsonDocument& doc,
    const char* origin,
    const char* origin_id,
    const char* timestamp,
    const char* direction,
    const char* time,
    const char* date,
    int len,
    int packet_type,
    const char* route,
    int payload_len,
    const char* raw,
    float snr,
    int rssi,
    float score,
    const char* hash,
    const uint8_t* path_bytes,
    int path_hop_count,
    int path_hash_size,
    size_t max_path_bytes,
    char* buffer,
    size_t buffer_size,
    // Set for a packet received or sent on radio2: adds `rx_radio_id` (rx) or
    // `tx_radio_id` (tx) with this id, and `mediamesh: true`. nullptr (the main
    // radio) leaves the message byte-for-byte as before.
    const char* radio_id = nullptr
  );

  static int buildRawMessage(
    JsonDocument& doc,
    const char* origin,
    const char* origin_id,
    const char* timestamp,
    const char* raw,
    char* buffer,
    size_t buffer_size,
    const char* radio_id = nullptr,  // as buildPacketMessage
    bool is_tx = false
  );

  struct NeighborsMessageEntry {
    const char* pubkey_hex;
    float snr;
    uint32_t heard_secs_ago;
    const char* scopes;
    const char* status;
    int rssi;  // dBm; non-negative means no measurement is available
    // True renders heard_secs_ago as JSON null, for a neighbour whose stored
    // stamp cannot yield an age. No default initializer: the struct stays an
    // aggregate for the device toolchain, and a zeroed tail means "age known",
    // so size-measurement callers keep reserving the widest numeric value.
    bool heard_unknown;
    // The neighbour's radio2 from its DISCOVER_RESP trailer. nullptr = it sent
    // none (older firmware, or no answer): the `radio2` key is left out, which
    // consumers read as unknown, never as off.
    const MQTTRadio2Info* radio2;
  };

  // Build neighbors-table JSON for the meshcore/{iata}/{device}/neighbors topic.
  // Callers order entries most- to least-useful; document growth is bounded to
  // buffer_size and the remaining tail is dropped once the next entry won't fit.
  static int buildNeighborsMessage(
    JsonDocument& doc,
    const char* origin,
    const char* origin_id,
    const char* timestamp,
    const char* self_scopes,
    const char* self_default_scope,
    const NeighborsMessageEntry* neighbors,
    int neighbor_count,
    char* buffer,
    size_t buffer_size,
    int total_neighbors = -1,
    int queried_neighbors = -1,
    bool truncated = false,
    const MQTTRadio2Info* self_radio2 = nullptr  // `self.radio2`; nullptr leaves it out
  );

  // Exact serialized-size components used by paced neighbor discovery. The
  // base reserves the largest progress metadata values for this snapshot;
  // callers add each entry size plus one byte for commas after the first.
  static size_t measureNeighborsMessageBase(
    const char* origin,
    const char* origin_id,
    const char* timestamp,
    const char* self_scopes,
    const char* self_default_scope,
    int total_neighbors,
    const MQTTRadio2Info* self_radio2 = nullptr
  );
  static size_t measureNeighborsMessageEntry(const NeighborsMessageEntry& neighbor);
};
