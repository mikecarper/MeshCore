#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

extern uint32_t mock_millis;
inline unsigned long millis() { return mock_millis; }
