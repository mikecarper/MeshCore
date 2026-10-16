#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace fake_pico { inline uint32_t now = 0; }
inline uint32_t millis() { return fake_pico::now; }
