#pragma once
#include "RegulatorRecovery.h"

namespace mesh { namespace power {
volatile RecoveryRecord& platformRecoveryRecord();
ResetKind platformResetKind();
bool platformRecoverySupported();
}}
