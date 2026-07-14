#pragma once

#include <cstddef>
#include <cstdint>

// Mock Stream class for native testing
// Provides minimal interface needed by Utils.h

class Stream {
public:
    virtual void print(char c) {}
    virtual void print(const char* str) {}
    virtual void println() {}
    virtual size_t readBytes(uint8_t*, size_t) { return 0; }
    virtual size_t write(const uint8_t*, size_t) { return 0; }
};
