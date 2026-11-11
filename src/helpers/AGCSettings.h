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
  (void)radio; (void)value;
  cli::formatError(reply, capacity, "unsupported");
  return false;
}
#endif
inline bool handleStoredOldAGCCommand(Radio* radio, const char* command, char* reply, size_t capacity) {
#if MESH_ENABLE_OLD_AGC
  if (!command || !reply || !capacity || strncmp(command, "set agc.old ", 12) != 0) return false;
  setStoredOldAGCInterval(radio, command + 12, reply, capacity);
  return true;
#else
  // No persistence interceptor when the existing parser reports unsupported.
  // Keep constrained Companions on their single compact dispatch path.
  (void)radio; (void)command; (void)reply; (void)capacity;
  return false;
#endif
}
} // namespace mesh
