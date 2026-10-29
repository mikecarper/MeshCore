#include "CLIReplyCopy.h"

#if defined(MESH_CLI_SHARE_REPLY_COPIES) && MESH_CLI_SHARE_REPLY_COPIES
#undef strcpy

namespace mesh { namespace cli {
char* copyReply(char* destination, const char* source) {
  return strcpy(destination, source);
}
}}
#endif
