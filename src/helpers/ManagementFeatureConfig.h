#pragma once

// Management reporting and its shared data.tx delivery route are included by
// default. Fixed-capacity builds can omit them without changing saved layouts.
#ifndef MESH_ENABLE_MANAGEMENT_REPORTING
  #define MESH_ENABLE_MANAGEMENT_REPORTING 1
#endif

#if MESH_ENABLE_MANAGEMENT_REPORTING != 0 && MESH_ENABLE_MANAGEMENT_REPORTING != 1
  #error "MESH_ENABLE_MANAGEMENT_REPORTING must be 0 or 1"
#endif
