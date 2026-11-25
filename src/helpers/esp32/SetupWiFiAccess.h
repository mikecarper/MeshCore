#pragma once

#include <stdint.h>

// Configured retains the existing boot/CLI policy. Button choices last one session.
enum class SetupWiFiAccess : uint8_t { Configured, Protected, Open };
