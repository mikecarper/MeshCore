#pragma once

#include "W12MultiSfPlan.h"

namespace mesh { namespace w12multisf {

// The adapter must use the radio's guarded reconfiguration admission, retain
// the pre-transaction physical tuple, and restore that exact tuple on rollback.
// It must not clear an unread FIFO. These helpers do not key the transmitter.
enum class Transition : uint8_t { Applied, Busy, Failed, RecoveryRequired };

template <class Adapter>
Transition setReceive(Adapter& io, const Plan& plan) {
  const Transition admitted = io.begin();
  if (admitted != Transition::Applied) return admitted;
  if (io.setSpreadingFactor(plan.rx_primary)
      && io.setDetectors(plan.side_sf, plan.side_count)
      && io.startReceive()) {
    io.commit();
    return Transition::Applied;
  }
  return io.rollback() ? Transition::Failed : Transition::RecoveryRequired;
}

template <class Adapter>
Transition prepareTransmit(Adapter& io, const Plan& plan) {
  const Transition admitted = io.begin();
  if (admitted != Transition::Applied) return admitted;
  if (io.setDetectors(nullptr, 0) && io.setSpreadingFactor(plan.tx_sf)
      && io.prepareTransmit()) {
    io.commit();
    return Transition::Applied;
  }
  return io.rollback() ? Transition::Failed : Transition::RecoveryRequired;
}

} }  // namespace mesh::w12multisf
