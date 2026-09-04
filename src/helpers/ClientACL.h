#pragma once

#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include <helpers/IdentityStore.h>

#define PERM_ACL_ROLE_MASK     7   // lower 3 bits
#define PERM_ACL_GUEST         0
#define PERM_ACL_READ_ONLY     1
#define PERM_ACL_READ_WRITE    2
#define PERM_ACL_ADMIN         3
#define PERM_ACL_REGION_MGR    4
#define PERM_ACL_FILTER_MGR    5

#define OUT_PATH_FORCE_FLOOD  0xFE
#define OUT_PATH_UNKNOWN      0xFF

struct ClientInfo {
  mesh::Identity id;
  uint8_t permissions;
  uint8_t out_path_len;
  uint8_t out_path[MAX_PATH_SIZE];
  bool out_path_is_persistable;  // live route may replace, but not erase, durable route
  uint8_t alt_path_len;
  uint8_t alt_path[MAX_PATH_SIZE];
  uint8_t observed_path_len;       // reciprocal login PATH (transient)
  uint8_t observed_path[MAX_PATH_SIZE];
  bool observed_path_pending;
  uint32_t observed_path_expiry;
  uint8_t shared_secret[PUB_KEY_SIZE];
  uint32_t last_timestamp;   // by THEIR clock (exact live floor; reserved ceiling after load)
  uint32_t last_activity;    // by OUR clock    (transient)
  union  {
    struct {
      uint32_t sync_since;  // sync messages SINCE this timestamp (by OUR clock)
      uint32_t last_post_timestamp; // sender timestamp for room posts only (transient)
      uint32_t pending_ack;
      uint32_t push_post_timestamp;
      unsigned long ack_timeout;
      uint8_t  push_failures;
    } room;
  } extra;
  
  bool isAdmin() const { return (permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_ADMIN; }
  bool isRegionMgr() const { return (permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_REGION_MGR; }
  bool isFilterMgr() const { return (permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_FILTER_MGR; }
  bool isProtectedManager() const { return isAdmin() || isRegionMgr() || isFilterMgr(); }
};

#ifndef MAX_CLIENTS
  #define MAX_CLIENTS           32
#endif

struct ClientLoginReplayClampResult {
  uint16_t stored_matched;
  uint16_t stored_changed;
  uint16_t live_matched;
  uint16_t live_changed;
};

class ClientACL {
  FILESYSTEM* _fs;
  ClientInfo clients[MAX_CLIENTS];
  int num_clients;
  bool login_replay_store_available;

public:
  ClientACL() {
    _fs = NULL;
    memset(clients, 0, sizeof(clients));
    num_clients = 0;
    login_replay_store_available = false;
  }
  void load(FILESYSTEM* _fs, const mesh::LocalIdentity& self_id);
  bool save(FILESYSTEM* _fs, bool (*filter)(ClientInfo*)=NULL);
  bool clear();

  // Authenticate the sender/password first, then call this before allocating
  // or mutating a client.  A true result means any required replay high-water
  // reservation was durably published.  runtime_last_timestamp is zero when
  // this boot has no live state for the identity (including after eviction or
  // revocation), in which case the durable tombstone is authoritative.
  // login_permissions selects whether a missing identity may allocate durable
  // state.  Guest/read-only sessions still enforce and advance an existing
  // tombstone, but otherwise use only their exact in-boot floor.
  bool authorizeLoginTimestamp(const uint8_t* pubkey,
                               uint32_t sender_timestamp,
                               uint32_t runtime_last_timestamp,
                               uint8_t login_permissions);

  // Explicitly authorized recovery only: the caller validates its transport,
  // permissions and clock. A non-null selector is a complete PUB_KEY_SIZE key;
  // null selects all records, including historical identities outside the ACL.
  // Only lower existing values to now. Never insert/delete records or raise a
  // floor. Publish durable changes before live changes; false returns zero
  // counts and leaves live state untouched. Counts include duplicate records.
  bool clampLoginReplayTimestamps(const uint8_t* pubkey, uint32_t now,
                                  ClientLoginReplayClampResult& result);

  ClientInfo* getClient(const uint8_t* pubkey, int key_len);
  ClientInfo* putClient(const mesh::Identity& id, uint8_t init_perms);
  bool applyPermissions(const mesh::LocalIdentity& self_id, const uint8_t* pubkey, int key_len, uint8_t perms);

  int getNumClients() const { return num_clients; }
  ClientInfo* getClientByIdx(int idx) { return &clients[idx]; }
};
