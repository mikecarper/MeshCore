#pragma once

#include <stddef.h>

namespace mesh {
#if defined(NRF52_PLATFORM) || defined(ESP32)
#if defined(ARDUINO_ARCH_ESP32)
// The saved setting also selects the on-disk format immediately.
bool readUsbDataLockSetting(bool& locked);
void resumeUsbStorageConversion(bool identityFormatChanged);
#endif
bool usbDataLocked();
void applyUsbDataLockAtBoot();
bool handleUsbDataLockCommand(const char* command, char* reply, size_t capacity);
#else
inline bool usbDataLocked() { return false; }
inline void applyUsbDataLockAtBoot() {}
inline bool handleUsbDataLockCommand(const char*, char*, size_t) { return false; }
#endif
}
