// newlib-nano's default assertion handler pulls in a second printf engine for
// FILE streams. These small Companions use a fixed stderr descriptor, including
// on allocation failure. Keep its full diagnostic without allocating memory.
#if defined(STM32_PLATFORM) && defined(MESH_STM32_COMPACT_ASSERT) && MESH_STM32_COMPACT_ASSERT
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void writeDiagnostic(const char* text) {
  size_t left = strlen(text);
  while (left) {
    ssize_t count = write(STDERR_FILENO, text, left);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) break;
    text += count;
    left -= count;
  }
}

extern "C" void __assert_func(const char* file, int line, const char* function,
                              const char* expression) {
  char number[16];
  snprintf(number, sizeof(number), "%d", line);
  writeDiagnostic("assertion \"");
  writeDiagnostic(expression);
  writeDiagnostic("\" failed: file \"");
  writeDiagnostic(file);
  writeDiagnostic("\", line ");
  writeDiagnostic(number);
  if (function) {
    writeDiagnostic(", function: ");
    writeDiagnostic(function);
  }
  writeDiagnostic("\n");
  abort();
}
#endif
