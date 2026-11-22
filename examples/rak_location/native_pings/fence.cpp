#include "fence.h"
#include "../../rak_findmy/src/TagPackets.h"
#include <zephyr/settings/settings.h>
#include <stdio.h>
#include <stdlib.h>

extern "C" void rak_collar_motion_start(void);
extern "C" bool rak_collar_motion_detected(void);
extern "C" void rak_collar_motion_stop(void);

namespace collar_fence {
Record record{};
namespace {
constexpr int32_t scale = 10000000;
constexpr int32_t pole = 90 * scale, dateline = 180 * scale;
constexpr int64_t acquisition_ms = 120000, quiet_ms = 10000;
bool armed, fence_active, side_verified, acquiring, pending_motion, fresh_pair;
TagFix last_fix{};
int64_t poll_at, last_motion, acquisition_start, retry_gps_at, retry_alert_at;
Side candidate;
uint32_t latest_utc, ack_utc;
uint8_t ack_hash[4], attempt;
int storage_error;

// Keep the 64-bit geometry primitives shared across edge checks on Cortex-M4.
[[gnu::noinline]] int32_t longitude_offset(int32_t lon, int32_t origin) {
  int64_t diff = int64_t(lon) - origin;
  if (diff > dateline) diff -= 2LL * dateline;
  if (diff < -dateline) diff += 2LL * dateline;
  return int32_t(diff);
}

bool between(int32_t v, int32_t a, int32_t b) {
  return (a <= v && v <= b) || (b <= v && v <= a);
}

// Compare the cross products separately so even world-scale inputs fit in int64.
[[gnu::noinline]] int orientation(Point a, Point b, Point p) {
  int64_t left = (int64_t(b.lon) - a.lon) * (p.lat - a.lat);
  int64_t right = (int64_t(p.lon) - a.lon) * (b.lat - a.lat);
  return (left > right) - (left < right);
}

bool in_bounds(Point a, Point b, Point p) {
  return between(p.lat, a.lat, b.lat) && between(p.lon, a.lon, b.lon);
}

bool intersects(Point a, Point b, Point c, Point d) {
  int abc = orientation(a, b, c), abd = orientation(a, b, d);
  int cda = orientation(c, d, a), cdb = orientation(c, d, b);
  return (abc * abd < 0 && cda * cdb < 0) ||
      (!abc && in_bounds(a, b, c)) || (!abd && in_bounds(a, b, d)) ||
      (!cda && in_bounds(c, d, a)) || (!cdb && in_bounds(c, d, b));
}

bool valid(const Shape& shape) {
  unsigned n = shape.count;
  if (!n || n > ARRAY_SIZE(shape.points)) return false;
  Point v[ARRAY_SIZE(shape.points)];
  int32_t west = 0, east = 0;
  for (unsigned i = 0; i < n; ++i) {
    Point p = shape.points[i];
    if (p.lat < -pole || p.lat > pole || p.lon < -dateline || p.lon > dateline) return false;
    v[i] = {p.lat, longitude_offset(p.lon, shape.points[0].lon)};
    west = MIN(west, v[i].lon);
    east = MAX(east, v[i].lon);
  }
  if (n == 1) return shape.radius_m > 0;
  if (n == 2) return v[0].lat != v[1].lat && v[1].lon != 0;
  if (int64_t(east) - west >= dateline) return false;
  for (unsigned i = 0; i < n; ++i) {
    Point a = v[i], b = v[(i + 1) % n], c = v[(i + 2) % n];
    if ((a.lat == b.lat && a.lon == b.lon) ||
        (!orientation(a, b, c) && !in_bounds(a, c, b))) return false;
    for (unsigned j = i + 2; j < n; ++j) {
      if ((j + 1) % n != i && intersects(a, b, v[j], v[(j + 1) % n])) return false;
    }
  }
  return true;
}

// All haversine arguments below are in [-pi/2, pi/2]. On this interval the
// sine series through x^19 has truncation error below 3e-16. This avoids
// linking the embedded math library's general-purpose angle reduction tables.
double bounded_sine(double x) {
  double step = -x * x, term = x, sum = x;
  for (int i = 2; i <= 18; i += 2) {
    term *= step / (i * (i + 1));
    sum += term;
  }
  return sum;
}

bool contains(const Shape& shape, Point p) {
  const Point a = shape.points[0];
  const int32_t x = longitude_offset(p.lon, a.lon);
  if (shape.count == 1) {
    // Compare haversines directly. A metre-scale delta retains its precision
    // because we subtract the integer coordinates before converting to radians.
    constexpr double rad = 1.7453292519943295e-9, earth_radius_m = 6371008.8;
    if (shape.radius_m >= 20015115) return true;  // Half the Earth's circumference.
    double dy = bounded_sine((p.lat - a.lat) * (rad / 2));
    double dx = bounded_sine(x * (rad / 2));
    double limit = bounded_sine(shape.radius_m / (2 * earth_radius_m));
    double cos_a = bounded_sine((pole - abs(a.lat)) * rad);
    double cos_p = bounded_sine((pole - abs(p.lat)) * rad);
    return dy * dy + cos_a * cos_p * dx * dx <= limit * limit;
  }
  if (shape.count == 2) {
    Point b = shape.points[1];
    int32_t west = MIN(a.lon, b.lon), east = MAX(a.lon, b.lon);
    if (p.lon == dateline) p.lon = -dateline;
    bool within = int64_t(east) - west <= dateline ? between(p.lon, west, east) : p.lon <= west || p.lon >= east;
    return between(p.lat, a.lat, b.lat) && within;
  }
  // Ray crossing in longitude/latitude space. Unwrap around the first vertex
  // so a local fence can straddle the date line. Points on an edge are inside.
  p.lon = x;
  bool inside = false;
  Point prev = {a.lat, 0};
  for (unsigned i = 1; i <= shape.count; ++i) {
    Point next = shape.points[i % shape.count];
    next.lon = longitude_offset(next.lon, a.lon);
    int side = orientation(prev, next, p);
    if (!side && in_bounds(prev, next, p)) return true;
    if ((prev.lat > p.lat) != (next.lat > p.lat) && (side > 0) == (next.lat > prev.lat)) inside = !inside;
    prev = next;
  }
  return inside;
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
  armed = fence_active = side_verified = acquiring = pending_motion = fresh_pair = false;
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
  if (!result && (!valid(record.shape) || record.count > ARRAY_SIZE(record.queue) ||
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
    if (!record.shape.count) shell_print(sh, "fence=off storage_error=%d", storage_error);
    else {
      const Shape& shape = record.shape;
      const char* type = shape.count == 1 ? "circle" : shape.count == 2 ? "box" : "polygon";
      shell_print(sh, "fence=%s shape=%s side=%s queued=%u storage_error=%d",
                  fence_active ? "armed" : "paused", type, name(record.side), record.count, storage_error);
      for (unsigned i = 0; i < shape.count; ++i) {
        char lat[16], lon[16];
        format(lat, shape.points[i].lat); format(lon, shape.points[i].lon);
        shell_print(sh, "%s %s", lat, lon);
      }
      if (shape.count == 1) shell_print(sh, "radius_m=%lu", (unsigned long)shape.radius_m);
    }
    return 0;
  }
  Record next{};
  next.motion_enabled = record.motion_enabled;
  if (argc != 3 || strcmp(argv[2], "off")) {
    if (!owner_ready) return -ENODEV;
    Shape& shape = next.shape;
    if (argc == 5) {
      for (const char* s = argv[4]; *s; ++s) {
        unsigned digit = unsigned(*s - '0');
        if (digit > 9 || shape.radius_m > (UINT32_MAX - digit) / 10) return -EINVAL;
        shape.radius_m = shape.radius_m * 10 + digit;
      }
      shape.count = 1;
    } else {
      if (argc < 6 || argc % 2 || argc > 2 + 2 * ARRAY_SIZE(shape.points)) return -EINVAL;
      shape.count = (argc - 2) / 2;
    }
    for (unsigned i = 0; i < shape.count; ++i) {
      Point& p = shape.points[i];
      if (!coordinate(argv[2 + 2 * i], p.lat) || !coordinate(argv[3 + 2 * i], p.lon)) return -EINVAL;
      if (p.lon == dateline) p.lon = -dateline;
    }
    if (!valid(shape)) return -EINVAL;
    memcpy(next.owner, owner, 32);
    if (!memcmp(&record.shape, &shape, sizeof(shape)) && !memcmp(record.owner, owner, 32)) return 0;
  }
  int rc = save(next);
  if (!rc) pause();
  return rc;
}

bool needs_gps(bool pet_on, const uint8_t* owner, int64_t now) {
  fence_active = pet_on && record.shape.count && !memcmp(record.owner, owner, 32);
  if (!pet_on || !(fence_active || record.motion_enabled)) {
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
  Side side = contains(record.shape, {fix.lat, fix.lon}) ? Inside : Outside;
  bool confirmed = side == candidate && fix.utc - latest_utc <= 15 &&
      fix.received_ms > previous.received_ms && fix.received_ms - previous.received_ms <= 15000;
  latest_utc = fix.utc;
  candidate = side;
  fresh_pair = confirmed;
  // An ordinary collar check can also discover a new side. Keep GPS on long
  // enough to confirm it even if the accelerometer missed that movement.
  if (!acquiring && !confirmed && side != record.side) pending_motion = true;
  if (!confirmed) return;
  if (side == record.side) { side_verified = true; return; }
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
  else side_verified = true;
}

bool outside() { return fence_active && side_verified && record.side == Outside; }

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
                       "Collar %s the fence at %s, %s", event.side == Inside ? "returned to" : "left", lat, lon);
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
