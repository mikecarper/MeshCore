#pragma once

#include <helpers/IdentityStore.h>

namespace mesh { namespace wireless {

// Group Off pauses the individual saved choices; its restore mask is stored
// in the same transaction. Primary ESP-NOW has no bridge preference to reuse.
struct Policy {
  uint8_t mode;            // 0: individual choices, 1: group off
  uint8_t mask;            // WiFi/Bluetooth/ESP-NOW live restore snapshot
  uint8_t primary_espnow;  // 0: legacy default, 1: off, 2: on
  constexpr Policy(uint8_t selected_mode = 0, uint8_t restore_mask = 0,
                   uint8_t primary = 0)
      : mode(selected_mode), mask(restore_mask), primary_espnow(primary) {}
  bool operator==(const Policy& other) const {
    return mode == other.mode && mask == other.mask
        && primary_espnow == other.primary_espnow;
  }
};

class PolicyStore {
  FILESYSTEM* fs_ = nullptr;
  bool healthy_ = false;
  bool present_ = false;
  Policy value_;
 public:
  // An absent file preserves old defaults. Unreadable/corrupt storage leaves
  // a conservative group-Off choice; explicit saves may repair malformed data.
  bool begin(FILESYSTEM* fs);
  bool save(const Policy& value);
  const Policy& value() const { return value_; }
  bool healthy() const { return healthy_; }
  bool present() const { return present_; }
};

inline PolicyStore& policyStore() { static PolicyStore store; return store; }

}} // namespace mesh::wireless
