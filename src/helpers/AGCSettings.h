#pragma once
#include <Dispatcher.h>
#include <helpers/IdentityStore.h>
#include "AGCMaintenanceCLI.h"

namespace mesh {
#if MESH_ENABLE_OLD_AGC
// A separate record preserves existing application preference layouts.
// Missing record: auto. Unreadable/corrupt record: old resets disabled.
bool beginAGCSettings(Radio* radio, FILESYSTEM* fs);
bool setStoredOldAGCInterval(Radio* radio, const char* value, char* reply, size_t capacity);
#else
inline bool beginAGCSettings(Radio*, FILESYSTEM*) { return true; }
inline bool setStoredOldAGCInterval(Radio* radio, const char* value, char* reply, size_t capacity) {
  return applyOldAGCIntervalValue(radio, value, reply, capacity);
}
#endif
inline bool handleStoredOldAGCCommand(Radio* radio, const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity || strncmp(command, "set agc.old ", 12) != 0) return false;
  setStoredOldAGCInterval(radio, command + 12, reply, capacity);
  return true;
}
} // namespace mesh
