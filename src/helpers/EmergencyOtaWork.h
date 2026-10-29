#pragma once

#include "EmergencyPowerConfig.h"
#include <stdint.h>

#if MESH_ENABLE_EMERGENCY_POWER
namespace mesh { namespace power {

class EmergencyOtaWork {
  mutable uint32_t served_at_ = 0, serve_hold_ms_ = 0;
public:
  template <typename Context>
  bool busy(const Context* context, uint32_t now) const {
    bool receiving = false, cleanup = false;
    if (context) {
      const auto& manager = context->manager;
      using Manager = decltype(context->manager);
      const auto state = manager.fetchState();
      receiving = state == Manager::WANT_MANIFEST || state == Manager::WANT_LEAVES
          || state == Manager::VERIFYING_STAGED || state == Manager::FETCHING;
      if (manager.pendingServeJobs() || manager.pendingManifestJobs()) {
        // Keep listening between a proof and the peer's next block request.
        // Idle self-serving/catalog adverts never renew this bounded hold.
        const uint32_t retry = manager.fetchRetryTimeoutMs();
        serve_hold_ms_ = retry < 30000UL ? 30000UL : retry;
        served_at_ = now;
      }
#if defined(OTA_SD_STORE)
      cleanup = context->sdCacheFetching();
#endif
    }
    if (serve_hold_ms_ && uint32_t(now - served_at_) >= serve_hold_ms_) serve_hold_ms_ = 0;
    return receiving || cleanup || serve_hold_ms_ != 0;
  }
};

}} // namespace mesh::power
#endif
