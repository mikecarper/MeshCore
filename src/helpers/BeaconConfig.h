#pragma once

#include "BeaconFeatureConfig.h"

#include <RadioProfiles.h>

namespace mesh {

struct BeaconSlot {
  static constexpr unsigned TextCapacity = 81;
  RadioProfileParams radio;
  char text[TextCapacity] = {};
  int8_t power = 0;
  uint8_t preset = 0; // Meshtext preset; ignored for MeshCore Public.
  uint8_t hops = 0;
  bool enabled = false;
};

} // namespace mesh
