#pragma once

#include "OtaManager.h"
#include "SignerAllowlist.h"

namespace mesh { namespace ota {

// Policy only: copying this must never copy an active transfer/workspace.
struct OtaConfigState {
  uint8_t autofetch = 0, autoinstall = 0, hops = OTA_HOP_LIMIT_DEFAULT;
  uint16_t checkpoint = OTA_CHECKPOINT_BLOCKS, advert = OTA_ADVERT_INTERVAL_MINS;
  SignerAllowlist allow;

  template <typename Context> static OtaConfigState capture(const Context& context) {
    OtaConfigState state;
    state.autofetch = context.manager.autofetch();
    state.autoinstall = context.autoinstall;
    state.hops = context.manager.max_hops();
    state.checkpoint = context.manager.checkpoint_blocks();
    state.advert = context.manager.advert_mins();
    state.allow = context.allow;
    return state;
  }

  template <typename Context> void apply(Context& context) const {
#if defined(OTA_SEEDER_ONLY)
    context.manager.set_autofetch(0);
    context.autoinstall = 0;
#else
    context.manager.set_autofetch(autofetch);
    context.autoinstall = autoinstall;
#endif
    context.manager.set_max_hops(hops);
    context.manager.set_checkpoint_blocks(checkpoint);
    context.manager.set_advert_mins(advert);
    context.allow = allow;
    context.config_dirty = false;
  }
};

} }
