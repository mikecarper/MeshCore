#include "fence.h"
#include "../../rak_findmy/src/TagPackets.h"
#include <zephyr/settings/settings.h>
#include <stdio.h>

extern "C" void rak_collar_motion_start(void);
extern "C" bool rak_collar_motion_detected(void);
extern "C" void rak_collar_motion_stop(void);

namespace collar_fence {
Record record{};
namespace {
constexpr int32_t scale = 10000000;
constexpr int32_t pole = 90 * scale, dateline = 180 * scale;
constexpr int64_t acquisition_ms = 120000, quiet_ms = 10000;
bool armed, fence_active, acquiring, pending_motion, fresh_pair;
TagFix last_fix{};
int64_t poll_at, last_motion, acquisition_start, retry_gps_at, retry_alert_at;
Side candidate;
uint32_t latest_utc, ack_utc;
uint8_t ack_hash[4], attempt;
int storage_error;

bool valid(const Bounds& b) {
  return b.south >= -pole && b.south < b.north && b.north <= pole &&
      b.west >= -dateline && b.west <= dateline && b.east >= -dateline && b.east <= dateline &&
      b.west != b.east && !(b.west == dateline && b.east == -dateline);
}

bool contains(const Bounds& b, int32_t lat, int32_t lon) {
  if (lat < b.south || lat > b.north) return false;
  if (lon == dateline && b.west == -dateline) lon = -dateline;
  else if (lon == -dateline && b.east == dateline) lon = dateline;
  return b.west < b.east ? lon >= b.west && lon <= b.east : lon >= b.west || lon <= b.east;
}

// Parse the same signed, seven-decimal-degree format that the tracker sends.
bool coordinate(const char* s, int32_t& value) {
  const bool negative = *s == '-';
  if (negative || *s == '+') ++s;
  if (*s < '0' || *s > '9') return false;
  int32_t whole = 0, fraction = 0;
  while (*s >= '0' && *s <= '9') {
    whole = whole * 10 + (*s++ - '0');
    if (whole > 180) return false;
  }
  if (*s == '.') {
    ++s;
    int32_t place = scale / 10;
    if (*s < '0' || *s > '9') return false;
    while (*s >= '0' && *s <= '9' && place) {
      fraction += (*s++ - '0') * place;
      place /= 10;
    }
  }
  value = (whole * scale + fraction) * (negative ? -1 : 1);
  return *s == 0;
}
void format(char (&out)[16], int32_t value) {
  long magnitude = value < 0 ? -long(value) : long(value);
  snprintf(out, sizeof(out), "%s%ld.%07ld", value < 0 ? "-" : "", magnitude / scale, magnitude % scale);
}
const char* name(Side side) {
  return side == Inside ? "inside" : side == Outside ? "outside" : "awaiting-fix";
}
int save(const Record& next) {
  storage_error = settings_save_one(settings_key, &next, sizeof(next));
  if (!storage_error) record = next;
  return storage_error;
}
void pause() {
  if (armed) rak_collar_motion_stop();
  armed = fence_active = acquiring = pending_motion = fresh_pair = false;
  last_fix = {};
  candidate = Unknown;
  latest_utc = ack_utc = 0;
  attempt = 0;
  poll_at = retry_gps_at = retry_alert_at = 0;
}
}

void after_load(int result) {
  if (!result && record.motion_enabled > 1) result = -EBADMSG;
  if (!result && mesh::Utils::isZeroes(record.owner, 32)) {
    uint8_t motion_enabled = record.motion_enabled;
    record = {};
    record.motion_enabled = motion_enabled;
    return;
  }
  if (!result && (!valid(record.box) || record.count > ARRAY_SIZE(record.queue) ||
      record.side > Outside || (record.side == Unknown) != (record.confirmed_utc == 0))) result = -EBADMSG;
  uint32_t previous = 0;
  for (unsigned i = 0; !result && i < record.count; ++i) {
    const auto& event = record.queue[i];
    if (event.utc <= previous || event.utc > record.confirmed_utc ||
        (event.side != Inside && event.side != Outside) || event.lat < -pole || event.lat > pole ||
        event.lon < -dateline || event.lon > dateline) result = -EBADMSG;
    previous = event.utc;
  }
  if (result) record = {};
  storage_error = result == -ENOENT ? 0 : result;
}

int command(const shell* sh, size_t argc, char** argv, const uint8_t* owner, bool owner_ready) {
  if (!strcmp(argv[1], "motion")) {
    if (argc == 2) {
      shell_print(sh, "motion=%s gps_on_motion=%s", record.motion_enabled ? "on" : "off",
                  armed ? "armed" : "idle");
      if (last_fix.utc) {
        char lat[16], lon[16];
        format(lat, last_fix.lat); format(lon, last_fix.lon);
        shell_print(sh, "last_fix_utc=%lu latitude=%s longitude=%s", (unsigned long)last_fix.utc, lat, lon);
      }
      return 0;
    }
    if (argc != 3 || (strcmp(argv[2], "on") && strcmp(argv[2], "off"))) return -EINVAL;
    Record next = record;
    next.motion_enabled = !strcmp(argv[2], "on");
    if (next.motion_enabled == record.motion_enabled) return 0;
    return save(next);
  }
  if (argc == 2) {
    if (!valid(record.box)) shell_print(sh, "fence=off storage_error=%d", storage_error);
    else {
      char s[16], w[16], n[16], e[16];
      format(s, record.box.south); format(w, record.box.west);
      format(n, record.box.north); format(e, record.box.east);
      shell_print(sh, "fence=%s south=%s west=%s north=%s east=%s side=%s queued=%u storage_error=%d",
                  fence_active ? "armed" : "paused", s, w, n, e, name(record.side), record.count, storage_error);
    }
    return 0;
  }
  Record next{};
  next.motion_enabled = record.motion_enabled;
  if (argc == 6) {
    if (!owner_ready) return -ENODEV;
    auto& b = next.box;
    if (!coordinate(argv[2], b.south) || !coordinate(argv[3], b.west) ||
        !coordinate(argv[4], b.north) || !coordinate(argv[5], b.east) || !valid(b)) return -EINVAL;
    memcpy(next.owner, owner, 32);
    if (!memcmp(&record.box, &b, sizeof(b)) && !memcmp(record.owner, owner, 32)) return 0;
  } else if (argc != 3 || strcmp(argv[2], "off")) return -EINVAL;
  int rc = save(next);
  if (!rc) pause();
  return rc;
}

bool needs_gps(bool dog_on, const uint8_t* owner, int64_t now) {
  fence_active = dog_on && valid(record.box) && !memcmp(record.owner, owner, 32);
  if (!dog_on || !(fence_active || record.motion_enabled)) {
    if (armed) pause();
    return false;
  }
  if (!armed) {
    rak_collar_motion_start();
    armed = true;
    pending_motion = fence_active;  // Verify the initial or last saved side once.
    last_motion = now;
  }
  if (now >= poll_at) {
    poll_at = now + 1000;
    if (rak_collar_motion_detected()) {
      last_motion = now;
      pending_motion = true;
    }
  }
  if (acquiring && ((fresh_pair && now - last_motion >= quiet_ms) ||
                    now - acquisition_start >= acquisition_ms)) {
    acquiring = false;
    // A failed acquisition backs off before servicing motion latched during it.
    if (!fresh_pair) retry_gps_at = now + 30000;
    if (fresh_pair && now - last_motion >= quiet_ms) pending_motion = false;
  }
  if (!acquiring && pending_motion && now >= retry_gps_at) {
    acquiring = true;
    acquisition_start = now;
    pending_motion = fresh_pair = false;
  }
  return acquiring;
}

void observe(const TagFix& fix, int64_t now) {
  if (!armed || !fix.utc || fix.utc <= latest_utc || (fence_active && fix.utc <= record.confirmed_utc) ||
      fix.received_ms > now || now - fix.received_ms > 15000 ||
      fix.lat < -pole || fix.lat > pole || fix.lon < -dateline || fix.lon > dateline) return;
  TagFix previous = last_fix;
  last_fix = fix;
  if (!fence_active) {
    latest_utc = fix.utc;
    fresh_pair = true;
    return;
  }
  Side side = contains(record.box, fix.lat, fix.lon) ? Inside : Outside;
  bool confirmed = side == candidate && fix.utc - latest_utc <= 15 &&
      fix.received_ms > previous.received_ms && fix.received_ms - previous.received_ms <= 15000;
  latest_utc = fix.utc;
  candidate = side;
  fresh_pair = confirmed;
  // An ordinary collar check can also discover a new side. Keep GPS on long
  // enough to confirm it even if the accelerometer missed that movement.
  if (!acquiring && !confirmed && side != record.side) pending_motion = true;
  if (!confirmed || side == record.side) return;
  Record next = record;
  if (next.side != Unknown) {
    if (next.count == ARRAY_SIZE(next.queue)) {
      --next.count;
      memmove(next.queue, next.queue + 1, next.count * sizeof(Crossing));
    }
    next.queue[next.count++] = {fix.utc, fix.lat, fix.lon, side};
  }
  next.side = side;
  next.confirmed_utc = fix.utc;
  if (save(next)) fresh_pair = false;
}

bool alert_due(int64_t now) {
  return fence_active && record.count && now >= retry_alert_at;
}

void make_alert(mesh::Packet& packet, const uint8_t* sender, const uint8_t* secret, int64_t now) {
  const auto& event = record.queue[0];
  uint8_t data[96]{};
  char lat[16], lon[16];
  format(lat, event.lat); format(lon, event.lon);
  mesh::tracker::put32(data, event.utc);
  uint8_t sequence = attempt++;
  data[4] = sequence & 3;
  int chars = snprintf(reinterpret_cast<char*>(data + 5), sizeof(data) - 5,
                       "Collar %s the box at %s, %s", event.side == Inside ? "returned to" : "left", lat, lon);
  size_t length = 5 + chars;
  mesh::Utils::sha256(ack_hash, sizeof(ack_hash), data, length, sender, 32);
  if (sequence > 3) {
    data[length++] = 0;
    data[length++] = sequence;
  }
  tag_packets::seal(packet, PAYLOAD_TYPE_TXT_MSG, sender, record.owner, secret, data, length);
  ack_utc = event.utc;
  retry_alert_at = now + 60000;
}

bool acknowledge(const uint8_t* frame, size_t length) {
  mesh::Packet packet;
  if (!fence_active || !record.count || !ack_utc || ack_utc != record.queue[0].utc ||
      length > UINT8_MAX || !packet.readFrom(frame, length) ||
      packet.getPayloadVer() != PAYLOAD_VER_1 || packet.getPayloadType() != PAYLOAD_TYPE_ACK ||
      packet.payload_len < sizeof(ack_hash) || memcmp(packet.payload, ack_hash, sizeof(ack_hash))) return false;
  Record next = record;
  --next.count;
  memmove(next.queue, next.queue + 1, next.count * sizeof(Crossing));
  if (save(next)) return false;
  retry_alert_at = ack_utc = attempt = 0;
  return true;
}
} // namespace collar_fence
