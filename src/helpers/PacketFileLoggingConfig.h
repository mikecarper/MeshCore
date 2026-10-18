#pragma once

// Persistent packet-log files are independent of live USB and bridge logging.
// Normal boards retain file logging; constrained builds may omit it.
#ifndef MESH_ENABLE_PACKET_FILE_LOGGING
  #define MESH_ENABLE_PACKET_FILE_LOGGING 1
#endif

#if MESH_ENABLE_PACKET_FILE_LOGGING != 0 && MESH_ENABLE_PACKET_FILE_LOGGING != 1
  #error "MESH_ENABLE_PACKET_FILE_LOGGING must be 0 or 1"
#endif
