// This implementation is selected only by the reviewed STM32 Companion hook.
// Generated literals remain separate arrays so unused replies can be collected.
#define MESH_STM32_PACKED_CLI_IMPLEMENTATION 1
#include "Stm32PackedCliReplies.h"

#if defined(STM32_PLATFORM) && defined(MESH_STM32_COMPANION_CLI_PACKING) && MESH_STM32_COMPANION_CLI_PACKING
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace cli { namespace outputpack {
char* copyPacked(char* output, const uint8_t* packed) {
  unpack(output, static_cast<size_t>(-1), packed);
  return output;
}

int printfPacked(char* output, const uint8_t* packed, ...) {
  char format[FormatCapacity];
  unpack(format, sizeof(format), packed);
  va_list arguments;
  va_start(arguments, packed);
  const int result = vsprintf(output, format, arguments);
  va_end(arguments);
  return result;
}

int snprintfPacked(char* output, size_t capacity, const uint8_t* packed, ...) {
  char format[FormatCapacity];
  unpack(format, sizeof(format), packed);
  va_list arguments;
  va_start(arguments, packed);
  const int result = vsnprintf(output, capacity, format, arguments);
  va_end(arguments);
  return result;
}

void appendPacked(char* output, size_t capacity, const uint8_t* packed, ...) {
  if (!capacity) return;
  const size_t used = strlen(output);
  if (used >= capacity) return;
  char format[FormatCapacity];
  unpack(format, sizeof(format), packed);
  va_list arguments;
  va_start(arguments, packed);
  vsnprintf(output + used, capacity - used, format, arguments);
  va_end(arguments);
}

void errorPacked(char* output, size_t capacity, const uint8_t* packed) {
  if (!capacity) return;
  const size_t length = capacity - 1 < 7 ? capacity - 1 : 7;
  memcpy(output, "Error: ", length);
  if (capacity > 7) unpack(output + 7, capacity - 7, packed);
  else output[length] = 0;
}
}}}
#endif
