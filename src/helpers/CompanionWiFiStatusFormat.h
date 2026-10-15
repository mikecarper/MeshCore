#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace wifi {

// Collected by the loop-task owner. Formatting never reads NVS, touches a
// wireless driver, allocates memory, or retains pointers after the call.
struct CompanionWiFiStatusSnapshot {
  const char* state;
  const char* ssid;
  const char* ip;
  bool has_rssi;
  int rssi;
  const char* protocol_saved;
  const char* protocol_boot;
  const char* phy;
  const char* mode_saved;
  const char* mode_boot;
  const char* powersave;
  bool coexist;
  bool ota_listening;
  bool ota_attached;
  uint16_t ota_port;
};

namespace status_format_detail {

inline const char* knownText(const char* value) {
  return value && value[0] ? value : "n/a";
}

class BoundedReply {
 public:
  BoundedReply(char* output, size_t capacity)
      : output_(output), capacity_(capacity), used_(0), complete_(true) {
    output_[0] = 0;
  }

  void append(const char* text) {
    for (const char* cursor = text; *cursor; ++cursor) {
      if (used_ < capacity_ - 1) output_[used_++] = *cursor;
      else complete_ = false;
    }
    output_[used_] = 0;
  }

  void appendNumber(int number) {
    char digits[16]; // Enough for every signed 32-bit int, including INT_MIN.
    snprintf(digits, sizeof(digits), "%d", number);
    append(digits);
  }

  bool complete() const { return complete_; }

 private:
  char* output_;
  size_t capacity_;
  size_t used_;
  bool complete_;
};

} // namespace status_format_detail

// Protocol is always saved/boot/live; mode repeats the boot choice only when
// it differs from the saved choice. All applicable fields are emitted in a
// fixed order, rather than dropping diagnostics to fit a particular buffer.
// Returns false on an invalid destination or truncation. Any nonempty valid
// destination is NUL-terminated, including a one-byte destination.
inline bool formatCompanionWiFiStatusSnapshot(
    char* output, size_t capacity, const CompanionWiFiStatusSnapshot& snapshot) {
  if (!output || !capacity) return false;
  using status_format_detail::knownText;
  status_format_detail::BoundedReply reply(output, capacity);
  reply.append("> ");
  reply.append(knownText(snapshot.state));
  reply.append(" ssid=");
  reply.append(knownText(snapshot.ssid));
  if (snapshot.ip && snapshot.ip[0]) {
    reply.append(" ip=");
    reply.append(snapshot.ip);
  }
  if (snapshot.has_rssi) {
    reply.append(" rssi=");
    reply.appendNumber(snapshot.rssi);
  }
  reply.append("\nprotocol=");
  reply.append(knownText(snapshot.protocol_saved));
  reply.append("/");
  reply.append(knownText(snapshot.protocol_boot));
  reply.append("/");
  reply.append(knownText(snapshot.phy));
  reply.append(" mode=");
  const char* saved_mode = knownText(snapshot.mode_saved);
  const char* boot_mode = knownText(snapshot.mode_boot);
  reply.append(saved_mode);
  if (strcmp(saved_mode, boot_mode) != 0) {
    reply.append("/");
    reply.append(boot_mode);
  }
  reply.append(" ps=");
  reply.append(knownText(snapshot.powersave));
  reply.append(" coex=");
  reply.append(snapshot.coexist ? "yes" : "no");
  reply.append(" ota=");
  if (snapshot.ota_attached || snapshot.ota_listening) {
    reply.appendNumber(snapshot.ota_port);
    reply.append(snapshot.ota_attached ? ":client" : ":listen");
  } else {
    reply.append("off");
  }
  return reply.complete();
}

} // namespace wifi
} // namespace mesh
