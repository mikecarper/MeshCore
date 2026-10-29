#pragma once

#if defined(MESH_CLI_SHARE_REPLY_COPIES) && MESH_CLI_SHARE_REPLY_COPIES
#include <string.h>

namespace mesh { namespace cli {
// Keep the same libc copy and return value, but prevent LTO from expanding
// constant human CLI replies into repeated stores in the two command parsers.
__attribute__((noipa)) char* copyReply(char* destination, const char* source);
}}

// Include this source-local remap only after every ordinary header.
#define strcpy ::mesh::cli::copyReply
#endif
