#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {

struct RoomLoginAuthorization {
  bool accepted;
  uint8_t role;
};

// Empty or absent configured passwords disable that credential. The request
// and configured strings must be terminated by the caller's protocol/prefs
// parser before authorization; a missing request never matches a credential.
inline bool roomLoginPasswordMatches(const char* requested,
                                     const char* configured) {
  return requested != NULL && configured != NULL && configured[0] != 0
      && strcmp(requested, configured) == 0;
}

// A valid admin credential explicitly selects Admin. Otherwise the identity's
// existing ACL role is authoritative, including delegated/unassigned roles:
// a public, stale or omitted password must not downgrade that role or promote
// it to Read/Write. Roles are labels, not a numeric privilege hierarchy.
inline RoomLoginAuthorization authorizeRoomLogin(
    const char* requested_password,
    const char* admin_password,
    const char* guest_password,
    bool has_acl_identity,
    uint8_t stored_permissions,
    bool allow_read_only,
    uint8_t role_mask,
    uint8_t guest_role,
    uint8_t read_write_role,
    uint8_t admin_role) {
  if (roomLoginPasswordMatches(requested_password, admin_password)) {
    return {true, static_cast<uint8_t>(admin_role & role_mask)};
  }
  if (has_acl_identity) {
    return {true, static_cast<uint8_t>(stored_permissions & role_mask)};
  }
  if (roomLoginPasswordMatches(requested_password, guest_password)) {
    return {true, static_cast<uint8_t>(read_write_role & role_mask)};
  }
  return {allow_read_only, static_cast<uint8_t>(guest_role & role_mask)};
}

} // namespace mesh
