#pragma once

#include <stddef.h>
#include <stdint.h>
#include <helpers/IdentityStore.h>
#include <helpers/CustomPeripheralConfig.h>

namespace mesh {
class MainBoard;
namespace peripherals {

#if MESH_CUSTOM_PERIPHERALS
// Optional external outputs use the firmware's Arduino pin numbers. No pin
// is driven until a complete saved record passes this board's pin policy.
// Call from the main loop; transport callbacks must defer commands there.
void begin(MainBoard& board, FILESYSTEM* fs);
// Terminal power-off: release runtime pins without changing saved settings.
void shutdown();
bool handleCommand(const char* command, char* reply, size_t capacity);
void loop(bool gps_active, bool bluetooth_enabled, bool bluetooth_connected);
void notificationLed(bool on);
void playNotification(const char* rtttl);
void stopNotification();
// Default arrivals retain the UI's existing DM/channel tones. A short LED
// indication also works on headless builds without programmable alert rules.
void notifyMessage(bool channel, bool sound, bool led);
void setBuzzerQuiet(bool quiet);
bool configuredBuzzer();
int8_t configuredBuzzerPin();
bool configuredNotificationLed();
bool needsPolling();
// Compare physical pad identities too: some nRF52 Arduino pins are aliases.
bool claims(uint8_t pin);
#else
inline void begin(MainBoard&, FILESYSTEM*) {}
inline void shutdown() {}
inline bool handleCommand(const char*, char*, size_t) { return false; }
inline void loop(bool, bool, bool) {}
inline void notificationLed(bool) {}
inline void playNotification(const char*) {}
inline void stopNotification() {}
inline void notifyMessage(bool, bool, bool) {}
inline void setBuzzerQuiet(bool) {}
inline bool configuredBuzzer() { return false; }
inline int8_t configuredBuzzerPin() { return -1; }
inline bool configuredNotificationLed() { return false; }
inline bool needsPolling() { return false; }
inline bool claims(uint8_t) { return false; }
#endif

} // namespace peripherals
} // namespace mesh
