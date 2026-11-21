#pragma once

#include "CLICommandUtils.h"
#include "SensorTrackerStore.h"
#include "TrackerProtocol.h"
#include <stdio.h>

namespace mesh { namespace tracker {

// Sensor and Companion expose the same local settings and durable record.
inline const char* editSetting(const char* command, const TrackerRecord& original,
                               TrackerRecord& candidate, const uint8_t* self_key,
                               bool gps_detected) {
  if (strncmp(command, "set tracker.owner ", 18) == 0) {
    const char* key = command + 18;
    if (strlen(key) != 64) return "ERR: full owner key required";
    uint8_t nonzero = 0;
    for (size_t i = 0; i < 64; ++i) {
      const char c = key[i];
      const int digit = c >= '0' && c <= '9' ? c - '0'
          : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
      if (digit < 0) return "ERR: invalid owner key";
      if ((i & 1) == 0) candidate.owner[i / 2] = uint8_t(digit << 4);
      else { candidate.owner[i / 2] |= uint8_t(digit); nonzero |= candidate.owner[i / 2]; }
    }
    if (!nonzero || memcmp(candidate.owner, self_key, sizeof(candidate.owner)) == 0) {
      return "ERR: invalid owner key";
    }
    if (memcmp(candidate.owner, original.owner, sizeof(candidate.owner)) != 0) {
      candidate.path_len = mesh::tracker::kUnknownPath;
      memset(candidate.path, 0, sizeof(candidate.path));
      candidate.last_lost = mesh::tracker::Unknown;
    }
  } else if (strcmp(command, "set tracker.mode dog") == 0) {
    if (!gps_detected) { return "ERR: GPS required"; }
    candidate.enabled = true;
  } else if (strcmp(command, "set tracker.mode off") == 0) candidate.enabled = false;
  else if (strncmp(command, "set tracker.interval ", 21) == 0
      || strncmp(command, "set tracker.lost.interval ", 26) == 0) {
    const bool lost = command[12] == 'l';
    int32_t seconds;
    if (!mesh::cli::parseIntegerStrict(command + (lost ? 26 : 21), seconds)
        || seconds < 60 || seconds > 86400) {
      return "ERR: interval must be 60..86400 seconds";
    }
    if (lost) candidate.lost_interval = uint32_t(seconds);
    else candidate.normal_interval = uint32_t(seconds);
  } else if (strncmp(command, "set tracker.path ", 17) == 0) {
    mesh::cli::TerminalPath parsed;
    memset(candidate.path, 0, sizeof(candidate.path));
    if (mesh::cli::parseTerminalPath(command + 17, candidate.path, sizeof(candidate.path), 63, parsed)
        != mesh::cli::TerminalPathParseResult::Valid) {
      return "ERR: invalid tracker path";
    }
    candidate.path_len = parsed.mode == mesh::cli::TerminalPathMode::Clear
        ? mesh::tracker::kUnknownPath : parsed.encoded_len;
  } else return "ERR: unknown tracker setting";
  return nullptr;
}

// Both local consoles may supply a shorter output buffer.
inline bool formatQuery(const char* command, const TrackerRecord& tracker, bool storage_fault,
                        char* reply, size_t capacity) {
  if (strcmp(command, "get tracker.owner") == 0) {
    static const char hex[] = "0123456789abcdef";
    if (!capacity) return true;
    size_t digits = 0;
    for (size_t i = 0; i < sizeof(tracker.owner) && digits + 2 < capacity; ++i) {
      digits += 2;
      reply[i * 2] = hex[tracker.owner[i] >> 4]; reply[i * 2 + 1] = hex[tracker.owner[i] & 15];
    }
    reply[digits] = 0;
    return true;
  }
  if (strcmp(command, "get tracker.mode") == 0) {
    snprintf(reply, capacity, "%s", tracker.enabled ? "dog" : "off"); return true;
  }
  if (strcmp(command, "get tracker.interval") == 0
      || strcmp(command, "get tracker.lost.interval") == 0) {
    snprintf(reply, capacity, "%lu", (unsigned long)(command[12] == 'l'
        ? tracker.lost_interval : tracker.normal_interval)); return true;
  }
  if (strcmp(command, "get tracker.path") == 0) {
    snprintf(reply, capacity, "path=%s hops=%u hash.bytes=%u",
        tracker.path_len == mesh::tracker::kUnknownPath ? "unknown"
            : (tracker.path_len & 63) == 0 ? "direct" : "saved",
        tracker.path_len == mesh::tracker::kUnknownPath ? 0 : tracker.path_len & 63,
        tracker.path_len == mesh::tracker::kUnknownPath ? 0 : (tracker.path_len >> 6) + 1);
    return true;
  }
  if (strcmp(command, "get tracker") != 0 && strcmp(command, "get tracker status") != 0) {
    snprintf(reply, capacity, "ERR: unknown tracker setting"); return true;
  }
  snprintf(reply, capacity, "mode=%s status=%s interval=%lu lost.interval=%lu flood=%s clock=%s storage=%s",
      tracker.enabled ? "dog" : "off", tracker.last_lost == 2 ? "yes" : tracker.last_lost == 1 ? "no" : "unknown",
      (unsigned long)tracker.normal_interval, (unsigned long)tracker.lost_interval,
      tracker.route.flags & mesh::tracker::kFloodLocked ? "locked" : "6h",
      tracker.route.flags & mesh::tracker::kClockFault ? "fault" : "ok",
      storage_fault ? "fault" : "ok");
  return true;
}

}} // namespace mesh::tracker
