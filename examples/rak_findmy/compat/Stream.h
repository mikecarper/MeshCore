#pragma once

#include <stdint.h>
#include <stddef.h>

// The portable MeshCore and MicroNMEA sources also expose Arduino printing
// helpers. On Zephyr all console I/O is through the shell.
class Stream {
public:
  virtual size_t print(char) = 0;
  virtual size_t print(const char*) = 0;
};
