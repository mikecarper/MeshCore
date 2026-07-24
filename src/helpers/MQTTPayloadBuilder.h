#pragma once

#include <ArduinoJson.h>
#include <stddef.h>
#include <stdint.h>

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
    const char* repeat = nullptr
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
    size_t buffer_size
  );

  static int buildRawMessage(
    const char* origin,
    const char* origin_id,
    const char* timestamp,
    const char* raw,
    char* buffer,
    size_t buffer_size
  );

  struct NeighborsMessageEntry {
    const char* pubkey_hex;
    float snr;
    uint32_t heard_secs_ago;
    const char* scopes;
    const char* status;
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
    const NeighborsMessageEntry* neighbors,
    int neighbor_count,
    char* buffer,
    size_t buffer_size
  );
};
