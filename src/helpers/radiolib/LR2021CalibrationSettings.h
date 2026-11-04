#pragma once

#include <helpers/IdentityStore.h>
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
#include "LR2021PacketFrequency.h"
#endif

class CustomLR2021Wrapper;

namespace mesh { namespace lr2021 {

// Load before radio initialization. No record leaves native trim unchanged.
bool loadLR2021CalibrationSettings(FILESYSTEM* fs, CustomLR2021Wrapper& radio);
void loopLR2021CalibrationSettings(CustomLR2021Wrapper& radio);
bool handleLR2021CalibrationCommand(const char* command, char* reply,
                                   size_t reply_size, CustomLR2021Wrapper& radio);
#if defined(MESHNOLOGY_W12) && defined(MESH_ENABLE_LR2021_AUTO_XTRIM) && MESH_ENABLE_LR2021_AUTO_XTRIM
void observeLR2021AutoTrimAdvert(const uint8_t* sender_key, uint32_t signed_time,
    uint32_t utc_now, const PacketFrequencyObservation& frequency, bool eligible);
#endif

}}  // namespace mesh::lr2021
