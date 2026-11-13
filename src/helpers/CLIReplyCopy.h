#pragma once

#if defined(MESH_CLI_SHARE_REPLY_COPIES) && MESH_CLI_SHARE_REPLY_COPIES
#include <string.h>

namespace mesh { namespace cli {
// Keep the same libc copy and return value, but prevent LTO from expanding
// constant human CLI replies into repeated stores in the two command parsers.
__attribute__((noipa)) char* copyReply(char* destination, const char* source);
#if defined(MESH_CLI_SHARE_MEMORY_OPERATIONS) && MESH_CLI_SHARE_MEMORY_OPERATIONS
__attribute__((noipa)) void* copyBytes(void* destination, const void* source, size_t length);
__attribute__((noipa)) void* fillBytes(void* destination, int value, size_t length);
#endif
}}

// Include this source-local remap only after every ordinary header.
#define strcpy ::mesh::cli::copyReply
#if defined(MESH_CLI_SHARE_MEMORY_OPERATIONS) && MESH_CLI_SHARE_MEMORY_OPERATIONS
// Small-flash CLI objects can share fixed-size settings copies too. Use the
// same libc semantics; parser bytes, settings layouts and replies are intact.
#define memcpy ::mesh::cli::copyBytes
#define memset ::mesh::cli::fillBytes
#endif
#endif
