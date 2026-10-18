#pragma once

// Optional unmodulated transmit tools. Packet TX/RX and the independently
// controlled Morse receiver do not depend on these bench/CLI transmit modes.
#ifndef MESH_ENABLE_CARRIER_TOOLS
#define MESH_ENABLE_CARRIER_TOOLS 1
#endif

#if MESH_ENABLE_CARRIER_TOOLS != 0 && MESH_ENABLE_CARRIER_TOOLS != 1
#error "MESH_ENABLE_CARRIER_TOOLS must be 0 or 1"
#endif
