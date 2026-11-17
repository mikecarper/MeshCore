#pragma once

#if defined(STM32_PLATFORM) && defined(MESH_STM32_COMPANION_CLI_PACKING) && MESH_STM32_COMPANION_CLI_PACKING
#include <stddef.h>
#include <stdint.h>
#include <Stm32PackedCliRepliesGenerated.h>

namespace mesh { namespace cli { namespace outputpack {
#if defined(__GNUC__) && !defined(__clang__)
#define MESH_PACKED_CLI_NOINLINE __attribute__((noipa))
#elif defined(__GNUC__)
#define MESH_PACKED_CLI_NOINLINE __attribute__((noinline))
#else
#define MESH_PACKED_CLI_NOINLINE
#endif
MESH_PACKED_CLI_NOINLINE char* copyPacked(char*, const uint8_t*);
MESH_PACKED_CLI_NOINLINE int printfPacked(char*, const uint8_t*, ...);
MESH_PACKED_CLI_NOINLINE int snprintfPacked(char*, size_t, const uint8_t*, ...);
MESH_PACKED_CLI_NOINLINE void errorPacked(char*, size_t, const uint8_t*);
MESH_PACKED_CLI_NOINLINE void appendPacked(char*, size_t, const uint8_t*, ...);
#undef MESH_PACKED_CLI_NOINLINE
}}}
#endif
