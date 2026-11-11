#pragma once

#include "WirelessControl.h"
#include <helpers/WirelessPolicyStore.h>

namespace mesh { namespace wireless {
// Persist the master pause and the service snapshot together. Individual
// service preferences retain their own stores and are never overwritten by Off.
class PolicyBackend : public Backend {
public:
  bool hasGroupPreference() const override { return true; }
  bool groupPreference(bool& off, uint8_t& restore) const override {
    const Policy& policy = policyStore().value();
    off = policy.mode == 1;
    restore = policy.mask;
    return true;
  }
  bool saveGroupPreference(bool off, uint8_t restore) override {
    Policy policy = policyStore().value();
    policy.mode = off ? 1 : 0;
    policy.mask = off ? (restore & All) : 0;
    return policyStore().save(policy);
  }
  static bool primaryEspNowPreference() {
    return policyStore().value().primary_espnow != 1;
  }
  static bool savePrimaryEspNowPreference(bool enabled) {
    Policy policy = policyStore().value();
    policy.primary_espnow = enabled ? 2 : 1;
    return policyStore().save(policy);
  }
};

// Mount first, gate radios second, then load the role's ordinary preferences
// and bind its full backend. A failed policy read defaults to radios off.
inline void loadBootPolicy(FILESYSTEM* fs, bool primary_espnow) {
  policyStore().begin(fs);
  const Policy& policy = policyStore().value();
  control().loadGroupPolicy(policy.mode == 1, policy.mask);
  if (primary_espnow) control().loadEspNowPolicy(PolicyBackend::primaryEspNowPreference());
}
}}
