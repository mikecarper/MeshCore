#pragma once

// An LR2021 board must explicitly qualify the complete sleep-to-RX transition,
// including its own oscillator/TCXO startup and radio settling time. Do not
// infer this value from another board or from the RadioLib TCXO timeout.
#if defined(MESH_LR2021_RXPS_ENABLE) && MESH_LR2021_RXPS_ENABLE
#if !defined(MESH_LR2021_RXPS_QUALIFIED) || !MESH_LR2021_RXPS_QUALIFIED
#error "LR2021 RXPS requires board-specific timing qualification"
#endif
#if !defined(MESH_LR2021_RXPS_TRANSITION_US) || MESH_LR2021_RXPS_TRANSITION_US < 1000 || MESH_LR2021_RXPS_TRANSITION_US > 30000000
#error "LR2021 RXPS requires a qualified transition in microseconds"
#endif
#define MESH_LR2021_RXPS_AVAILABLE 1
#elif defined(MESHNOLOGY_W12) && defined(MESH_W12_PRAM_EXPERIMENT) && MESH_W12_PRAM_EXPERIMENT && defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT
// Existing, hardware-tested W12 crystal configuration.
#define MESH_LR2021_RXPS_AVAILABLE 1
#define MESH_LR2021_RXPS_TRANSITION_US 1000
#else
#define MESH_LR2021_RXPS_AVAILABLE 0
#endif

// Preserve the W12 hold when only its RXPS control is compiled, and expose
// the same conservative extra-SF protection to any qualified LR2021 board.
#if MESH_LR2021_RXPS_AVAILABLE || (defined(MESHNOLOGY_W12) && defined(MESH_W12_RXPS_EXPERIMENT) && MESH_W12_RXPS_EXPERIMENT)
#define MESH_LR2021_RXPS_HOLD_AVAILABLE 1
#else
#define MESH_LR2021_RXPS_HOLD_AVAILABLE 0
#endif
