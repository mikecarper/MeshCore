#pragma once

#include "MotaContainer.h"
#include "SignerAllowlist.h"
#include <Identity.h>

namespace mesh {
namespace ota {

// The signed flag is advisory until the allowlisted Ed25519 key verifies the
// complete signed manifest. This runs before automatic staging can erase or
// replace a previously retained image; installation verifies again separately.
inline bool ota_manifest_trusted(const MotaManifest& manifest, const SignerAllowlist& allow) {
  if (!manifest.is_signed() || !manifest.manifest_start || !manifest.signer_pubkey ||
      !manifest.signature || manifest.signed_len != MOTA_SIGNED_LEN ||
      !allow.contains(manifest.signer_pubkey)) return false;
  mesh::Identity signer(manifest.signer_pubkey);
  return signer.verify(manifest.signature, manifest.manifest_start, (int)manifest.signed_len);
}

} // namespace ota
} // namespace mesh
