#pragma once

#if defined(NRF52_PLATFORM)
#include "RegulatorRecovery.h"

namespace mesh { namespace power {

volatile RecoveryRecord& platformRecoveryRecord();
ResetKind platformResetKind();
// False for an unsupported linker layout, so an unreserved orphan section
// cannot accidentally enable automatic recovery.
bool platformRecoverySupported();

}} // namespace mesh::power
#endif
