#pragma once

#include <helpers/IdentityStore.h>

namespace mesh { namespace ui {

// Infrastructure uses a separate checked file so its existing node/MQTT
// preference layouts stay compatible. Companion retains its NodePrefs field.
bool loadDisplayRotationSettings(FILESYSTEM* fs);
uint16_t displayRotationDegrees();
// 0 restores the compiled board orientation; 90/180/270 turn clockwise from it.
// The UI observes saved changes on its next loop, even with the display off.
bool handleDisplayRotationCommand(const char* command, char* reply,
                                  size_t reply_size, bool supported);

}}  // namespace mesh::ui
