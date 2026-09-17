#pragma once
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>

// Models Adafruit's bounded printf so a long verse sent via printf is caught.
class Stream {
public:
  std::string text;
  size_t print(const char* value) { text += value; return std::strlen(value); }
  size_t printf(const char* format, ...) {
    char buf[256];
    va_list args;
    va_start(args, format);
    vsnprintf(buf, sizeof(buf), format, args);
    va_end(args);
    return print(buf);
  }
};
