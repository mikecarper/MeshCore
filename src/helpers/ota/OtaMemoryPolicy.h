#pragma once

// Non-PSRAM Full Companions retain the complete source geometry without
// retaining every Merkle leaf in internal RAM. An explicit 0 keeps the legacy
// cache for matched qualification builds.
#ifndef OTA_BOUNDED_SOURCE_PROOFS
#if defined(ESP32_PLATFORM) && defined(COMPANION_RADIO_FULL) && !defined(BOARD_HAS_PSRAM) && \
    (!defined(OTA_PROOFGEN_SCRATCH) || \
     (OTA_PROOFGEN_SCRATCH >= 4 && OTA_PROOFGEN_SCRATCH <= 16384))
#define OTA_BOUNDED_SOURCE_PROOFS 1
#else
#define OTA_BOUNDED_SOURCE_PROOFS 0
#endif
#endif
#if OTA_BOUNDED_SOURCE_PROOFS != 0 && OTA_BOUNDED_SOURCE_PROOFS != 1
#error "OTA_BOUNDED_SOURCE_PROOFS must be 0 or 1"
#endif
#if OTA_BOUNDED_SOURCE_PROOFS && defined(OTA_PROOFGEN_SCRATCH) && \
    (OTA_PROOFGEN_SCRATCH < 4 || OTA_PROOFGEN_SCRATCH > 16384)
#error "Bounded source proofs require 4..16384 leaf bytes; use legacy mode for another capacity"
#endif

// Qualified Full Companions stream host-provided firmware and need no permanent
// mOTA workspace. Apply the same policy to build.sh and direct PlatformIO builds.
#if defined(OTA_SEEDER_ONLY) && defined(COMPANION_RADIO_FULL) && \
    (defined(NRF52_PLATFORM) || \
     (defined(ESP32_PLATFORM) && defined(HELTEC_WIRELESS_PAPER)))
#ifndef OTA_SHARED_COMPANION_QUEUE
#define OTA_SHARED_COMPANION_QUEUE 1
#endif
#endif
