#pragma once

#include <Arduino.h>
#include <Mesh.h>
#include "ContactCachePolicy.h"
#if MESH_CONTACT_CACHE
#include "ContactPathCache.h"
#endif
#if defined(NRF52_PLATFORM)
#include "PersistentStoreFormat.h"
#endif

#define OUT_PATH_UNKNOWN   0xFF

struct ContactInfo {
  mesh::Identity id;
  char name[32] = {};
  uint8_t type = 0;   // one of ADV_TYPE_*
  uint8_t flags = 0;
  uint8_t out_path_len = 0;
  // Both fit in the original cache-valid byte; no extra RAM per contact.
  mutable uint8_t shared_secret_valid : 1;
  uint8_t tx_radio : 3;
#if MESH_CONTACT_CACHE
  mesh::ContactPathRef path_ref;
#else
  uint8_t out_path[MAX_PATH_SIZE];
#endif
  uint32_t last_advert_timestamp = 0;   // by THEIR clock
  uint32_t lastmod = 0;  // by OUR clock
  int32_t gps_lat = 0, gps_lon = 0;    // 6 dec places
  uint32_t sync_since = 0;

  // Runtime-only position in the paged companion contact store.  It is not
  // part of the on-disk record; the record's page/slot supplies it on load.
  // Mutable keeps persistence bookkeeping out of contact protocol semantics.
#if defined(NRF52_PLATFORM)
  mutable uint16_t storage_slot = mesh::storage::CONTACT_SLOT_NONE;
#endif

  ContactInfo() : shared_secret_valid(false), tx_radio(mesh::RADIO_TX_AUTO) {}

  // The pointer is a short-lived borrow. Copy bytes for a queued operation;
  // another contact-cache access may replace the resident entry.
  const uint8_t* getPath() const {
#if MESH_CONTACT_CACHE
    return path_ref.view();
#else
    return out_path;
#endif
  }
  bool copyPathTo(uint8_t path[MAX_PATH_SIZE]) const {
#if MESH_CONTACT_CACHE
    return path_ref.read(path);
#else
    memcpy(path, out_path, MAX_PATH_SIZE);
    return true;
#endif
  }
  bool setRawPath(const uint8_t path[MAX_PATH_SIZE]) {
#if MESH_CONTACT_CACHE
    return path_ref.set(path);
#else
    memcpy(out_path, path, MAX_PATH_SIZE);
    return true;
#endif
  }
  bool setPath(const uint8_t* path, uint8_t encoded_len) {
    if (encoded_len != OUT_PATH_UNKNOWN &&
        !mesh::Packet::isValidPathLen(encoded_len)) return false;
    uint8_t normalized[MAX_PATH_SIZE] = {};
    if (encoded_len != OUT_PATH_UNKNOWN && (encoded_len & 63)) {
      if (!path) return false;
      mesh::Packet::copyPath(normalized, path, encoded_len);
    }
    if (!setRawPath(normalized)) return false;
    out_path_len = encoded_len;
    return true;
  }

#if MESH_CONTACT_CACHE
  const uint8_t* getSharedSecret(const mesh::LocalIdentity& self_id) const;
#else
  const uint8_t* getSharedSecret(const mesh::LocalIdentity& self_id) const {
    if (!shared_secret_valid) {
      self_id.calcSharedSecret(shared_secret, id.pub_key);
      shared_secret_valid = true;
    }
    return shared_secret;
  }
#endif

  bool isFav() const { return flags & 0x01; }
  bool isTelemBaseAllowed() const { return flags & 0x02; }
  bool isTelemLocAllowed() const { return flags & 0x04; }
  bool isTelemEnvAllowed() const { return flags & 0x08; }
  bool isRemoteCLIAllowed() const { return flags & 0x10; }

private:
#if !MESH_CONTACT_CACHE
  mutable uint8_t shared_secret[PUB_KEY_SIZE];
#endif
};
