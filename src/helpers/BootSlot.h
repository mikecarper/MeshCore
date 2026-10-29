#pragma once

#if defined(ESP32_PLATFORM)
#include <stddef.h>

namespace mesh {
class MainBoard;
namespace esp32 {

// Select an installed application for the next boot; never reboot inline.
bool handleBootSlotCommand(const char* command, MainBoard& board, char* reply,
                           size_t capacity, bool update_pending = false);

} // namespace esp32
} // namespace mesh
#endif
