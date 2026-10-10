#pragma once

#include "CLICommandUtils.h"
#include "ClientPathPersistence.h"

namespace mesh {

enum class RoomClientPathCommand : uint8_t {
  None, GetOut, GetObserved, GetAlt, SetOut, SetObserved, SetAlt,
};

// Normalize a private CLI request, not a room-chat post. A single Companion
// correlation prefix is echoed; nested prefixes never enter the allowlist.
inline char* normalizeRoomClientPathCommand(char* command, char* reply) {
  char* end = command + strlen(command);
  while (end > command && (end[-1] == ' ' || end[-1] == '\t'
      || end[-1] == '\r' || end[-1] == '\n')) *--end = 0;
  while (*command == ' ' || *command == '\t') command++;
  if (strlen(command) > 4 && command[2] == '|') {
    memcpy(reply, command, 3);
    command += 3;
  }
  while (*command == ' ' || *command == '\t') command++;
  cli::normalizeCommandVerb(command);
  return command;
}

inline RoomClientPathCommand classifyRoomClientPathCommand(const char* command) {
  if (strcmp(command, "get outpath") == 0) return RoomClientPathCommand::GetOut;
  if (strcmp(command, "get outpath path") == 0) return RoomClientPathCommand::GetObserved;
  if (strcmp(command, "get altpath") == 0) return RoomClientPathCommand::GetAlt;
  if (strcmp(command, "set outpath path") == 0) return RoomClientPathCommand::SetObserved;
  if (strcmp(command, "set outpath") == 0 || strncmp(command, "set outpath ", 12) == 0)
    return RoomClientPathCommand::SetOut;
  if (strcmp(command, "set altpath") == 0 || strncmp(command, "set altpath ", 12) == 0)
    return RoomClientPathCommand::SetAlt;
  return RoomClientPathCommand::None;
}

// One outstanding delivery per recipient, with separate namespaces for topic
// and chat. The complete room/recipient identities participate in the key.
template <typename Hash>
inline void roomDeliveryRetryKey(uint8_t* output, size_t size,
                                 const uint8_t* room, const uint8_t* recipient,
                                 bool topic, Hash hash) {
  const uint8_t domain[] = {'r','o','o','m', topic ? uint8_t(1) : uint8_t(0)};
  uint8_t material[5 + 64];
  memcpy(material, domain, sizeof(domain));
  memcpy(material + sizeof(domain), room, 32);
  memcpy(material + sizeof(domain) + 32, recipient, 32);
  hash(output, size, material, sizeof(material));
}

inline void formatRoomClientPathReply(const uint8_t* path, uint8_t length,
                                      char* reply, size_t capacity) {
  if (length == 0xfe) { snprintf(reply, capacity, "> flood"); return; }
  if (length == 0xff) { snprintf(reply, capacity, "> unknown"); return; }
  if (!isValidEncodedClientPathLength(length, 64)) {
    snprintf(reply, capacity, "> invalid"); return;
  }
  const size_t hops = length & 63;
  if (hops == 0) { snprintf(reply, capacity, "> direct"); return; }
  const size_t width = (length >> 6) + 1;
  const size_t bytes = hops * width;
  // Compact fallback remains complete when comma separators would not fit.
  const bool separated = bytes * 2 + hops + 2 <= capacity;
  if (bytes * 2 + 3 > capacity) {
    snprintf(reply, capacity, "> path too long"); return;
  }
  static const char hex[] = "0123456789ABCDEF";
  size_t position = 0;
  reply[position++] = '>'; reply[position++] = ' ';
  for (size_t i = 0; i < bytes; i++) {
    if (separated && i != 0 && i % width == 0) reply[position++] = ',';
    reply[position++] = hex[path[i] >> 4];
    reply[position++] = hex[path[i] & 15];
  }
  reply[position] = 0;
}

} // namespace mesh
