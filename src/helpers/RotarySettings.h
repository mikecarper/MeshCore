#pragma once

#include <stddef.h>
#include <stdint.h>
#include <helpers/IdentityStore.h>
#include <helpers/CustomPeripheralConfig.h>

namespace mesh {
class MainBoard;
}
class RotaryInput;

namespace mesh { namespace ui {
#if MESH_CUSTOM_PERIPHERALS
// A separate versioned record preserves the existing NodePrefs layout.
// Returns true when the saved profile or explicit build default is active.
bool beginRotarySettings(MainBoard& board, FILESYSTEM* fs, bool ui_supported);
bool handleRotaryCommand(const char* command, char* reply, size_t capacity);
RotaryInput* customRotaryInput();
bool rotaryOwnsButtonPin(uint8_t pin);
bool rotarySettingsEnabled();
// Suspend/resume the live input when a display is disabled or re-detected.
// This never changes the saved preference and revalidates pins on resume.
bool setRotaryUiSupported(bool supported);
#else
inline bool beginRotarySettings(MainBoard&, FILESYSTEM*, bool) { return false; }
inline bool handleRotaryCommand(const char*, char*, size_t) { return false; }
inline RotaryInput* customRotaryInput() { return nullptr; }
inline bool rotaryOwnsButtonPin(uint8_t) { return false; }
inline bool rotarySettingsEnabled() { return false; }
inline bool setRotaryUiSupported(bool) { return false; }
#endif
} }
