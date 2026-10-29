#pragma once

// RAM-only delivery details for the last OWN_MESSAGE_TRACKER_SIZE own messages
// (session-only; nothing here is persisted). Channel messages count echoes of
// their packet hash heard back from repeaters; DMs track ACK / failure.

#include <MeshCore.h> // MAX_HASH_SIZE

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define OWN_MESSAGE_TRACKER_SIZE    8
#define OWN_MESSAGE_MAX_REPEATERS   8
#define OWN_ROUTE_FLOOD             0xFF // == OUT_PATH_UNKNOWN

struct OwnMessageInfo {
  uint32_t own_id;
  uint8_t packet_hash[MAX_HASH_SIZE]; // channel: GRP_TXT packet hash; DM: unused (zero)
  bool is_channel;
  uint16_t repeats;                   // channel echoes heard (saturates)
  uint8_t repeaters[OWN_MESSAGE_MAX_REPEATERS]; // distinct first bytes of the echoing repeater hash
  uint8_t repeater_count;
  enum State : uint8_t { Pending, Delivered, Failed } state;
  uint8_t route;                      // DM route used: packed out_path_len, OWN_ROUTE_FLOOD = flood
                                      // (sits in former padding; the struct stays 36 bytes)
  uint32_t sent_ms, rtt_ms;           // rtt_ms valid once a DM is Delivered
};
static_assert(sizeof(OwnMessageInfo) <= 36, "OwnMessageInfo must stay <= 36 bytes (RAM budget; route uses padding)");

class OwnMessageTracker {
  OwnMessageInfo _items[OWN_MESSAGE_TRACKER_SIZE];
  uint32_t _next_id = 1;
  uint8_t _count = 0;
  uint8_t _head = 0; // next slot to write (oldest once full)

  OwnMessageInfo *findMut(uint32_t own_id) {
    if (own_id == 0) {
      return nullptr;
    }
    for (uint8_t i = 0; i < _count; i++) {
      if (_items[i].own_id == own_id) {
        return &_items[i];
      }
    }
    return nullptr;
  }

public:
  OwnMessageTracker() { memset(_items, 0, sizeof(_items)); }

  // Returns the new message's own_id (never 0). Evicts the oldest when full.
  // hash may be null (DMs). route: packed out_path_len the DM was sent with.
  uint32_t add(const uint8_t hash[], bool is_channel, uint32_t now_ms, uint8_t route = OWN_ROUTE_FLOOD) {
    OwnMessageInfo &it = _items[_head];
    memset(&it, 0, sizeof(it));
    it.own_id = _next_id++;
    if (_next_id == 0) {
      _next_id = 1;
    }
    if (hash) {
      memcpy(it.packet_hash, hash, MAX_HASH_SIZE);
    }
    it.is_channel = is_channel;
    it.state = OwnMessageInfo::Pending;
    it.sent_ms = now_ms;
    it.route = route;
    _head = (uint8_t)((_head + 1) % OWN_MESSAGE_TRACKER_SIZE);
    if (_count < OWN_MESSAGE_TRACKER_SIZE) {
      _count++;
    }
    return it.own_id;
  }

  // A repeat of one of our channel packets was heard via repeater_hash.
  void onEcho(const uint8_t hash[], uint8_t repeater_hash) {
    for (uint8_t i = 0; i < _count; i++) {
      OwnMessageInfo &it = _items[i];
      if (!it.is_channel || memcmp(it.packet_hash, hash, MAX_HASH_SIZE) != 0) {
        continue;
      }
      if (it.repeats < 0xFFFF) {
        it.repeats++;
      }
      for (uint8_t r = 0; r < it.repeater_count; r++) {
        if (it.repeaters[r] == repeater_hash) {
          return;
        }
      }
      if (it.repeater_count < OWN_MESSAGE_MAX_REPEATERS) {
        it.repeaters[it.repeater_count++] = repeater_hash;
      }
      return;
    }
  }

  void onAck(uint32_t own_id, uint32_t now_ms) {
    OwnMessageInfo *it = findMut(own_id);
    if (it && it->state != OwnMessageInfo::Delivered) {
      it->state = OwnMessageInfo::Delivered;
      it->rtt_ms = now_ms - it->sent_ms;
    }
  }

  // A DM retry switched route (e.g. the direct path was reset to flood).
  void setRoute(uint32_t own_id, uint8_t route) {
    OwnMessageInfo *it = findMut(own_id);
    if (it) {
      it->route = route;
    }
  }

  void onFailed(uint32_t own_id) {
    OwnMessageInfo *it = findMut(own_id);
    if (it && it->state == OwnMessageInfo::Pending) {
      it->state = OwnMessageInfo::Failed;
    }
  }

  const OwnMessageInfo *find(uint32_t own_id) const {
    return const_cast<OwnMessageTracker *>(this)->findMut(own_id);
  }
};
static_assert(sizeof(OwnMessageTracker) <= OWN_MESSAGE_TRACKER_SIZE * 40 + 8,
              "OwnMessageTracker grew past the RAM budget");

// "Route Flood" / "Route Direct" / "Route <n> hops" for an OwnMessageInfo::route.
inline void formatOwnRoute(uint8_t route, char *out, size_t cap) {
  if (!out || cap == 0) {
    return;
  }
  if (route == OWN_ROUTE_FLOOD) {
    snprintf(out, cap, "Route Flood");
  } else if ((route & 63) == 0) {
    snprintf(out, cap, "Route Direct");
  } else {
    snprintf(out, cap, "Route %u hop%s", (unsigned)(route & 63), (route & 63) == 1 ? "" : "s");
  }
}
