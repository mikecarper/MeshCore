#include "MQTTMessageBuilder.h"

#ifdef WITH_MQTT_BRIDGE

#include <ArduinoJson.h>
#include <cstring>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include <Timezone.h>
#include "MeshCore.h"

void MQTTMessageBuilder::formatIsoTimestampForMqtt(time_t now, long usec, Timezone* timezone, char* buffer, size_t buffer_size) {
  if (!buffer || buffer_size == 0) return;
  // Always emit UTC with an explicit "+00:00" offset, matching Python's
  // datetime.now(timezone.utc).isoformat(). The system clock is UTC (SNTP offset 0),
  // so gmtime() is correct regardless of the prefs Timezone (now unused here).
  (void)timezone;
  // Clamp the sub-second to a valid microsecond range so the "%06ld" field can never
  // overflow to 7 digits or go negative on a bad clock read.
  if (usec < 0) usec = 0;
  else if (usec > 999999) usec = 999999;
  struct tm* tm_info = gmtime(&now);
  if (tm_info) {
    int n = snprintf(buffer, buffer_size, "%04d-%02d-%02dT%02d:%02d:%02d",
                     tm_info->tm_year + 1900, tm_info->tm_mon + 1, tm_info->tm_mday,
                     tm_info->tm_hour, tm_info->tm_min, tm_info->tm_sec);
    if (n > 0 && static_cast<size_t>(n) < buffer_size &&
        snprintf(buffer + n, buffer_size - n, ".%06ld+00:00", usec) > 0) {
      return;
    }
  }
  strncpy(buffer, "2024-01-01T12:00:00.000000+00:00", buffer_size - 1);
  buffer[buffer_size - 1] = '\0';
}

int MQTTMessageBuilder::buildStatusMessage(
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
  int battery_mv,
  int uptime_secs,
  int errors,
  int queue_len,
  int noise_floor,
  int tx_air_secs,
  int rx_air_secs,
  int recv_errors,
  int internal_heap,
  int packets_sent,
  int packets_received,
  const char* repeat
) {
  // doc is provided by the caller (heap-allocated DynamicJsonDocument in MQTTBridge),
  // keeping this 768-byte scratch space off the MQTT task stack.
  doc.clear();
  JsonObject root = doc.to<JsonObject>();
  
  root["status"] = status;
  root["timestamp"] = timestamp;
  root["origin"] = origin;
  root["origin_id"] = origin_id;
  root["model"] = model;
  root["firmware_version"] = firmware_version;
  root["radio"] = radio;
  root["client_version"] = client_version;
  if (repeat != nullptr) {
    root["repeat"] = repeat;
  }

  // Add stats object if any stats are provided
  if (battery_mv >= 0 || uptime_secs >= 0 || errors >= 0 || queue_len >= 0 ||
      noise_floor > -999 || tx_air_secs >= 0 || rx_air_secs >= 0 || recv_errors >= 0 ||
      internal_heap >= 0 || packets_sent >= 0 || packets_received >= 0) {
    JsonObject stats = root.createNestedObject("stats");
    
    if (battery_mv >= 0) {
      stats["battery_mv"] = battery_mv;
    }
    if (uptime_secs >= 0) {
      stats["uptime_secs"] = uptime_secs;
    }
    if (packets_sent >= 0) {
      stats["packets_sent"] = packets_sent;
    }
    if (packets_received >= 0) {
      stats["packets_received"] = packets_received;
    }
    if (errors >= 0) {
      stats["errors"] = errors;
    }
    if (queue_len >= 0) {
      stats["queue_len"] = queue_len;
    }
    if (noise_floor > -999) {
      stats["noise_floor"] = noise_floor;
    }
    if (tx_air_secs >= 0) {
      stats["tx_air_secs"] = tx_air_secs;
    }
    if (rx_air_secs >= 0) {
      stats["rx_air_secs"] = rx_air_secs;
    }
    if (recv_errors >= 0) {
      stats["recv_errors"] = recv_errors;
    }
    if (internal_heap >= 0) {
      stats["internal_heap"] = internal_heap;
    }
  }
  
  size_t len = serializeJson(root, buffer, buffer_size);
  return (len > 0 && len < buffer_size) ? len : 0;
}

int MQTTMessageBuilder::buildPacketMessage(
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
  char* buffer,
  size_t buffer_size
) {
  // doc is provided by the caller (heap-allocated DynamicJsonDocument in MQTTBridge),
  // keeping this 2048-byte scratch space off the MQTT task stack.
  doc.clear();
  JsonObject root = doc.to<JsonObject>();
  
  // Format numeric values as strings to avoid String object allocations
  char len_str[16];
  char packet_type_str[16];
  char payload_len_str[16];
  char snr_str[16];
  char rssi_str[16];
  char score_str[16];

  snprintf(len_str, sizeof(len_str), "%d", len);
  snprintf(packet_type_str, sizeof(packet_type_str), "%d", packet_type);
  snprintf(payload_len_str, sizeof(payload_len_str), "%d", payload_len);
  snprintf(snr_str, sizeof(snr_str), "%.1f", snr);
  snprintf(rssi_str, sizeof(rssi_str), "%d", rssi);
  
  root["timestamp"] = timestamp;
  root["hash"] = hash;
  root["origin"] = origin;
  root["type"] = "PACKET";
  root["direction"] = direction;
  root["time"] = time;
  root["date"] = date;
  root["len"] = len_str;
  root["packet_type"] = packet_type_str;
  root["route"] = route;
  root["payload_len"] = payload_len_str;
  root["raw"] = raw;
  root["origin_id"] = origin_id;
  // SNR and RSSI are only meaningful for RX packets (received from radio)
  if (strcmp(direction, "rx") == 0) {
    root["SNR"] = snr_str;
    root["RSSI"] = rssi_str;
    // Firmware's rebroadcast "score" for this RX packet, scaled x1000 to match the
    // integer form printed in the serial RX log (see Dispatcher::checkRecv()).
    if (!isnan(score)) {
      snprintf(score_str, sizeof(score_str), "%d", (int)(score * 1000));
      root["score"] = score_str;
    }
  }
  
  // Routing path as an array of lowercase hex hop tokens, one element per hop
  // (e.g. ["aa","bb","cc"], or ["aaaa","bbbb"] for multi-byte hashes). This matches
  // meshcore-packet-capture's _split_path_hops() representation.
  if (path_bytes && path_hop_count > 0 && path_hash_size > 0) {
    JsonArray path_arr = root.createNestedArray("path");
    char hop_hex[2 * 4 + 1]; // hop hash is 1-4 bytes -> up to 8 hex chars + null
    for (int i = 0; i < path_hop_count; i++) {
      size_t pos = 0;
      for (int b = 0; b < path_hash_size && b < 4; b++) {
        size_t idx = (size_t)i * path_hash_size + b;
        if (idx >= MAX_PATH_SIZE) break;
        snprintf(hop_hex + pos, 3, "%02x", path_bytes[idx]);
        pos += 2;
      }
      hop_hex[pos] = '\0';
      path_arr.add(hop_hex); // char[] (non-const) -> ArduinoJson copies the string
    }
  }

  size_t json_len = serializeJson(root, buffer, buffer_size);
  return (json_len > 0 && json_len < buffer_size) ? json_len : 0;
}

int MQTTMessageBuilder::buildRawMessage(
  const char* origin,
  const char* origin_id,
  const char* timestamp,
  const char* raw,
  char* buffer,
  size_t buffer_size
) {
  // Use StaticJsonDocument to avoid heap fragmentation (fixed-size stack allocation)
  StaticJsonDocument<512> doc;
  JsonObject root = doc.to<JsonObject>();
  
  root["origin"] = origin;
  root["origin_id"] = origin_id;
  root["timestamp"] = timestamp;
  root["type"] = "RAW";
  root["data"] = raw;
  
  size_t len = serializeJson(root, buffer, buffer_size);
  return (len > 0 && len < buffer_size) ? len : 0;
}

int MQTTMessageBuilder::buildPacketJSON(
  JsonDocument& doc,
  mesh::Packet* packet,
  bool is_tx,
  const char* origin,
  const char* origin_id,
  Timezone* timezone,
  char* buffer,
  size_t buffer_size
) {
  if (!packet) return 0;
  
  // One wall-clock read: tv_sec feeds both the timestamp and the UTC time/date
  // fields below (kept consistent), tv_usec is the real sub-second.
  struct timeval now_tv;
  gettimeofday(&now_tv, nullptr);
  time_t now = now_tv.tv_sec;
  char timestamp[40];
  formatIsoTimestampForMqtt(now, now_tv.tv_usec, timezone, timestamp, sizeof(timestamp));
  
  // Packet time/date: UTC (gmtime), same family as meshcoretomqtt serial fields
  struct tm* utc_timeinfo = gmtime(&now);
  
  // Format time and date (ALWAYS UTC)
  char time_str[16];
  char date_str[16];
  if (utc_timeinfo) {
    snprintf(time_str, sizeof(time_str), "%02d:%02d:%02d",
             utc_timeinfo->tm_hour, utc_timeinfo->tm_min, utc_timeinfo->tm_sec);
    snprintf(date_str, sizeof(date_str), "%02d/%02d/%04d",
             utc_timeinfo->tm_mday, utc_timeinfo->tm_mon + 1, utc_timeinfo->tm_year + 1900);
  } else {
    strcpy(time_str, "12:00:00");
    strcpy(date_str, "01/01/2024");
  }
  
  // Convert packet to hex
  // MAX_TRANS_UNIT is 255 bytes, hex = 510 chars, but allow for larger with headers
  char raw_hex[1024];
  packetToHex(packet, raw_hex, sizeof(raw_hex));
  
  // Get packet characteristics
  int packet_type = packet->getPayloadType();
  const char* route_str = getRouteTypeString(packet->isRouteDirect() ? 1 : 0);
  
  // Create proper packet hash using MeshCore's calculatePacketHash method
  char hash_str[17];
  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);
  bytesToHex(packet_hash, MAX_HASH_SIZE, hash_str, sizeof(hash_str));
  
  // Routing path (direct packets only): pass raw hop bytes to buildPacketMessage,
  // which emits them as an array of lowercase hex hop tokens.
  bool has_path = packet->isRouteDirect() && packet->getPathHashCount() > 0;
  
  return buildPacketMessage(
    doc,
    origin, origin_id, timestamp,
    is_tx ? "tx" : "rx",
    time_str, date_str,
    packet->getRawLength(),
    packet_type, route_str,
    packet->payload_len,
    raw_hex,
    12.5f, // SNR - using reasonable default
    -65,   // RSSI - using reasonable default
    NAN,   // score - unknown on this reconstruction-less fallback path
    hash_str,
    has_path ? packet->path : nullptr,
    has_path ? packet->getPathHashCount() : 0,
    has_path ? packet->getPathHashSize() : 0,
    buffer, buffer_size
  );
}

int MQTTMessageBuilder::buildPacketJSONFromRaw(
  JsonDocument& doc,
  const uint8_t* raw_data,
  int raw_len,
  mesh::Packet* packet,
  bool is_tx,
  const char* origin,
  const char* origin_id,
  float snr,
  float rssi,
  float score,
  Timezone* timezone,
  char* buffer,
  size_t buffer_size
) {
  if (!packet || !raw_data || raw_len <= 0) return 0;
  
  // One wall-clock read: tv_sec feeds both the timestamp and the UTC time/date
  // fields below (kept consistent), tv_usec is the real sub-second.
  struct timeval now_tv;
  gettimeofday(&now_tv, nullptr);
  time_t now = now_tv.tv_sec;
  char timestamp[40];
  formatIsoTimestampForMqtt(now, now_tv.tv_usec, timezone, timestamp, sizeof(timestamp));
  
  struct tm* utc_timeinfo = gmtime(&now);
  
  // Format time and date (ALWAYS UTC)
  char time_str[16];
  char date_str[16];
  if (utc_timeinfo) {
    snprintf(time_str, sizeof(time_str), "%02d:%02d:%02d",
             utc_timeinfo->tm_hour, utc_timeinfo->tm_min, utc_timeinfo->tm_sec);
    snprintf(date_str, sizeof(date_str), "%02d/%02d/%04d",
             utc_timeinfo->tm_mday, utc_timeinfo->tm_mon + 1, utc_timeinfo->tm_year + 1900);
  } else {
    strcpy(time_str, "12:00:00");
    strcpy(date_str, "01/01/2024");
  }
  
  // Convert raw radio data to hex (this includes radio headers)
  // MAX_TRANS_UNIT is 255 bytes, hex = 510 chars, but allow for larger with headers
  char raw_hex[1024];
  bytesToHex(raw_data, raw_len, raw_hex, sizeof(raw_hex));
  
  // Get packet characteristics from the parsed packet
  int packet_type = packet->getPayloadType();
  const char* route_str = getRouteTypeString(packet->isRouteDirect() ? 1 : 0);
  
  // Create proper packet hash using MeshCore's calculatePacketHash method
  char hash_str[17];
  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);
  bytesToHex(packet_hash, MAX_HASH_SIZE, hash_str, sizeof(hash_str));
  
  // Routing path (direct packets only): pass raw hop bytes to buildPacketMessage,
  // which emits them as an array of lowercase hex hop tokens.
  bool has_path = packet->isRouteDirect() && packet->getPathHashCount() > 0;
  
  return buildPacketMessage(
    doc,
    origin, origin_id, timestamp,
    is_tx ? "tx" : "rx",
    time_str, date_str,
    raw_len, // Use actual raw radio data length
    packet_type, route_str,
    packet->payload_len,
    raw_hex,
    snr,  // Use actual SNR from radio
    rssi, // Use actual RSSI from radio
    score, // Firmware rebroadcast score (NaN for tx / when unavailable)
    hash_str,
    has_path ? packet->path : nullptr,
    has_path ? packet->getPathHashCount() : 0,
    has_path ? packet->getPathHashSize() : 0,
    buffer, buffer_size
  );
}

int MQTTMessageBuilder::buildRawJSON(
  mesh::Packet* packet,
  const char* origin,
  const char* origin_id,
  Timezone* timezone,
  char* buffer,
  size_t buffer_size
) {
  if (!packet) return 0;

  // One wall-clock read: tv_sec for the timestamp, tv_usec for the real sub-second.
  struct timeval now_tv;
  gettimeofday(&now_tv, nullptr);
  char timestamp[40];
  formatIsoTimestampForMqtt(now_tv.tv_sec, now_tv.tv_usec, timezone, timestamp, sizeof(timestamp));

  // Convert packet to hex
  // MAX_TRANS_UNIT is 255, so max hex size is 510 chars + null = 511 bytes
  char raw_hex[1024];
  packetToHex(packet, raw_hex, sizeof(raw_hex));
  
  return buildRawMessage(origin, origin_id, timestamp, raw_hex, buffer, buffer_size);
}

const char* MQTTMessageBuilder::getRouteTypeString(int route_type) {
  switch (route_type) {
    case 0: return "F"; // FLOOD
    case 1: return "D"; // DIRECT
    case 2: return "T"; // TRANSPORT_DIRECT
    default: return "U"; // UNKNOWN
  }
}

void MQTTMessageBuilder::bytesToHex(const uint8_t* data, size_t len, char* hex, size_t hex_size) {
  if (hex_size < len * 2 + 1) return;

  // Nibble lookup instead of a per-byte snprintf("%02X"): same uppercase hex
  // output, but avoids re-parsing the format string up to ~512 times per publish.
  static const char HEX_DIGITS[] = "0123456789ABCDEF";
  for (size_t i = 0; i < len; i++) {
    hex[i * 2]     = HEX_DIGITS[data[i] >> 4];
    hex[i * 2 + 1] = HEX_DIGITS[data[i] & 0x0F];
  }
  hex[len * 2] = '\0';
}

void MQTTMessageBuilder::packetToHex(mesh::Packet* packet, char* hex, size_t hex_size) {
  // Serialize full on-air/wire format using Packet::writeTo()
  // This includes header, transport codes (if present), path_len, path, and payload
  uint8_t raw_buf[512];
  uint8_t raw_len = packet->writeTo(raw_buf);
  if (raw_len == 0 || raw_len > sizeof(raw_buf)) return;
  
  // Check if hex buffer is large enough (2 hex chars per byte + null terminator)
  if (hex_size < (size_t)raw_len * 2 + 1) return;
  
  // Convert serialized packet to hex
  bytesToHex(raw_buf, raw_len, hex, hex_size);
}

#endif
