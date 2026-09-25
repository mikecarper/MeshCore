#pragma once

#include <helpers/IdentityStore.h>
#include "OtaTiming.h"

namespace mesh { namespace ota {

// A small, separate settings image shared by every OTA-capable role. Reading
// or changing speed never borrows the Companion's OTA workspace.
void beginSpeedConfig(FILESYSTEM* fs);
float speedFactor();
void formatSpeed(char* text, size_t capacity);
void formatSpeedFactor(float speed, char* text, size_t capacity);
// Automatic sender pacing only adds packet quiet time. The saved setting is
// still the upper bound and continues to control the other OTA timers.
float effectivePacketPace(float adaptive_speed);
bool handleSpeedCommand(const char* command, char* reply, size_t capacity,
                        float adaptive_speed = OTA_SPEED_DEFAULT);

} }
