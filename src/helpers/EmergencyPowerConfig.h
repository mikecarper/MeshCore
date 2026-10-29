#pragma once

// The source-role build policy enables this for supported infrastructure
// images. Companion and constrained profiles retain their existing footprint.
#ifndef MESH_ENABLE_EMERGENCY_POWER
#define MESH_ENABLE_EMERGENCY_POWER 0
#endif

#if MESH_ENABLE_EMERGENCY_POWER != 0 && MESH_ENABLE_EMERGENCY_POWER != 1
#error "MESH_ENABLE_EMERGENCY_POWER must be 0 or 1"
#endif
