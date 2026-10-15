#pragma once
#include <stddef.h>
#include <stdint.h>
namespace fake { inline uint32_t now = 0; }
inline unsigned long millis() { return fake::now; }
