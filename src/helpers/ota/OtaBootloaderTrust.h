#pragma once

#include "MotaContainer.h"
#include "SignerAllowlist.h"
#include <string.h>

namespace mesh {
namespace ota {

// Published OTAFIX 2.4.12 signer. This fallback is limited to the explicit
// signed bootloader format; it does not add an application signer or consume
// a user's persisted allowlist slot. Callers must still verify the signature.
inline bool ota_signer_trusted_for_manifest(const MotaManifest& manifest,
                                            const SignerAllowlist& allow) {
  if (!manifest.signer_pubkey) return false;
  if (allow.contains(manifest.signer_pubkey)) return true;
  static const uint8_t official[32] = {
    0x27,0x25,0x64,0xCC,0x58,0x8D,0x3D,0x12,0x22,0x85,0xA1,0x5E,0x6E,0x25,0x66,0xD2,
    0xAB,0xE7,0x17,0x7B,0xB7,0xEA,0x1D,0x1E,0x41,0xB2,0x3B,0x29,0xF0,0xF8,0x5D,0x2D
  };
  return manifest.format_ver == MOTA_BOOT_FORMAT_VER &&
         manifest.flags == (MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER) &&
         memcmp(manifest.signer_pubkey, official, sizeof(official)) == 0;
}

} // namespace ota
} // namespace mesh
