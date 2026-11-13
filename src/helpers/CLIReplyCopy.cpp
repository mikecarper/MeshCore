#include "CLIReplyCopy.h"

#if defined(MESH_CLI_SHARE_REPLY_COPIES) && MESH_CLI_SHARE_REPLY_COPIES
#undef strcpy
#undef memcpy
#undef memset

namespace mesh { namespace cli {
char* copyReply(char* destination, const char* source) {
  return strcpy(destination, source);
}
#if defined(MESH_CLI_SHARE_MEMORY_OPERATIONS) && MESH_CLI_SHARE_MEMORY_OPERATIONS
void* copyBytes(void* destination, const void* source, size_t length) {
  return memcpy(destination, source, length);
}
void* fillBytes(void* destination, int value, size_t length) {
  return memset(destination, value, length);
}
#endif
}}
#endif
