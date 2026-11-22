#pragma once

#include "../../rak_findmy/src/gnss.h"
#include <Packet.h>
#include <zephyr/shell/shell.h>

namespace collar_fence {

// Persisted together so a power loss cannot separate a transition from its alert.
enum Side : uint8_t { Unknown, Inside, Outside };
struct Point { int32_t lat, lon; };
struct Shape {
  Point points[9];  // The USB shell accepts 20 words, including "dog fence".
  uint32_t radius_m;
  uint8_t count;
};
struct Crossing { uint32_t utc; int32_t lat, lon; Side side; };
struct Record {
  Shape shape;
  uint8_t owner[32];
  uint32_t confirmed_utc;
  Side side;
  uint8_t count, motion_enabled;
  Crossing queue[8];
};
constexpr char settings_key[] = "tagmesh/fence2";
extern Record record;
void after_load(int result);
int command(const shell* sh, size_t argc, char** argv, const uint8_t* owner, bool owner_ready);
bool needs_gps(bool dog_on, const uint8_t* owner, int64_t now);
void observe(const TagFix& fix, int64_t now);
bool alert_due(int64_t now);
void make_alert(mesh::Packet& packet, const uint8_t* sender, const uint8_t* secret, int64_t now);
bool acknowledge(const uint8_t* frame, size_t length);
} // namespace collar_fence
