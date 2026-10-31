#!/usr/bin/env python3
"""Execute repeater guest authorization, saved toggles, and receive admission."""
from pathlib import Path
import re
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/guest_login"

HARNESS = r'''
#include <helpers/CLIReplyText.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/GuestLoginSettings.h>
#include <helpers/DiscoveryResponsePrefs.h>
#include <helpers/ClientLoginPersistence.h>
#include <helpers/ClientPathObservation.h>
#include <helpers/ClientPathPersistence.h>
#include <helpers/LazyPersistence.h>
#include <helpers/FloodCommandAliases.h>
#include <helpers/LoginHistory.h>
#include <helpers/DefaultPasswordWarning.h>
#include <helpers/RadioPresetConfig.h>
#define PUB_KEY_SIZE 32
#define MAX_PATH_SIZE 64
#define MAX_PACKET_PAYLOAD 184
#define PAYLOAD_TYPE_ANON_REQ 0x07
#define PAYLOAD_TYPE_RESPONSE 0x01
#define PAYLOAD_TYPE_TXT_MSG 0x02
#define TXT_TYPE_CLI_DATA 1
#define ADMIN_PASSWORD "password"
#define MAX_CLIENTS 16
#define MESH_CLIENT_REPEATER_ONLY 1
#define MESH_ENABLE_FLOOD_RULE_ENGINE 1
#define MESH_DEBUG_PRINTLN(...) ((void)0)
@CONSTANTS@
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE];
  bool matches(const Identity& other) const {
    return memcmp(pub_key, other.pub_key, sizeof(pub_key)) == 0;
  }
  bool isHashMatch(const uint8_t* hash) const { return pub_key[0] == hash[0]; }
};
struct Packet {
  bool direct = true;
  bool radio_reply = false, path_return = false;
  uint8_t type = PAYLOAD_TYPE_ANON_REQ;
  uint8_t path_len = 0, path[MAX_PATH_SIZE]{}, payload[MAX_PACKET_PAYLOAD]{};
  size_t payload_len = 0;
  bool isRouteDirect() const { return direct; }
  bool isRouteFlood() const { return !direct; }
  uint8_t getPayloadType() const { return type; }
  uint8_t getPathHashSize() const { return (path_len >> 6) + 1; }
  static bool isValidPathLen(uint8_t);
  static size_t writePath(uint8_t*, const uint8_t*, uint8_t);
};
@PACKET_HELPERS@
@ROUTING_HELPERS@
}
@CLIENT_INFO@;
struct ClientACL {
  ClientInfo clients[MAX_CLIENTS]{};
  int num_clients = 0;
  unsigned authorizations = 0, allocations = 0;
  bool authorize_ok = true, allocate_ok = true;
  uint8_t authorized_role = 255;
  ClientInfo* getClient(const uint8_t* key, int length) {
    for (int i = 0; i < num_clients; ++i)
      if (memcmp(clients[i].id.pub_key, key, size_t(length)) == 0) return &clients[i];
    return nullptr;
  }
  int getNumClients() const { return num_clients; }
  ClientInfo* getClientByIdx(int i) { return &clients[i]; }
  const ClientInfo* getClientByIdx(int i) const { return &clients[i]; }
  ClientInfo* putClient(const mesh::Identity& id, uint8_t permissions) {
    ++allocations;
    if (auto* old = getClient(id.pub_key, PUB_KEY_SIZE)) return old;
    if (!allocate_ok || num_clients == MAX_CLIENTS) return nullptr;
    auto* client = &clients[num_clients++];
    *client = ClientInfo{};
    client->id = id;
    client->permissions = permissions;
    client->out_path_len = OUT_PATH_UNKNOWN;
    client->alt_path_len = OUT_PATH_UNKNOWN;
    client->observed_path_len = OUT_PATH_UNKNOWN;
    client->out_path_is_persistable = true;
    return client;
  }
  bool authorizeLoginTimestamp(const uint8_t*, uint32_t timestamp,
                               uint32_t floor, uint8_t role) {
    ++authorizations;
    authorized_role = role;
    return authorize_ok && mesh::planClientLoginReplay(floor, 0, timestamp).fresh;
  }
  int removeGuestClients();
};
@REMOVE_GUESTS@
struct Clock {
  uint32_t now = 1234567890;
  uint32_t getCurrentTime() const { return now; }
  uint32_t getCurrentTimeUnique() { return now + ++unique; }
  uint32_t unique = 0;
};
struct RNG { void random(uint8_t* data, size_t len) { memset(data, 0xB5, len); } };
@MANAGER_HELPERS@
struct MyMesh {
  GuestMemoryFS fs;
  mesh::GuestLoginSettings guest_login;
  mesh::LoginHistory login_history;
  ClientACL acl;
  Clock clock;
  RNG rng;
  struct Prefs {
    char password[16] = "admin";
    char guest_password[16] = "";
    uint8_t discovery_responses = 3;
  } _prefs;
  unsigned long dirty_contacts_expiry = 0;
  uint8_t contacts_save_failures = 0, reply_data[160]{};
  int matching_peer_indexes[MAX_CLIENTS]{};
  unsigned received = 0;
  unsigned anonymous_requests = 0;
  uint8_t reply_path_len = 0;
  uint8_t reply_path[MAX_PATH_SIZE]{};
  struct Sent { mesh::Packet packet; unsigned long delay; uint8_t hash_size; };
  std::vector<Sent> sent;
  mesh::Packet response;
  unsigned creations = 0, path_returns = 0, client_replies = 0, fail_creation_on = 0;
  bool create_ok = true, queue_ok = true;
  bool metadata_reply = false;
  bool login_full_admin = false;
  struct PresetCommits { unsigned admin_logins = 0; void onAdminLogin() { ++admin_logins; } };
  struct Cli {
    PresetCommits presets;
    PresetCommits& radioPresets() { return presets; }
  } _cli;
  MyMesh() { assert(guest_login.begin(&fs)); }
  Clock* getRTCClock() { return &clock; }
  RNG* getRNG() { return &rng; }
  unsigned long futureMillis(unsigned long delay) { return 10000 + delay; }
  uint8_t handleLoginReq(const mesh::Identity&, const uint8_t*, uint32_t, const uint8_t*, bool);
  void onAnonDataRecv(mesh::Packet*, const uint8_t*, const mesh::Identity&, uint8_t*, size_t);
  void sendDefaultPasswordWarning(ClientInfo*, uint32_t, uint8_t);
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity& to, const uint8_t* key,
                               const uint8_t* data, size_t length) {
    ++creations;
    assert(length <= sizeof(response.payload));
    assert(to.matches(acl.clients[0].id) && !memcmp(key, acl.clients[0].shared_secret, PUB_KEY_SIZE));
    if (!create_ok || creations == fail_creation_on) return nullptr;
    response = mesh::Packet{}; response.type = type; response.payload_len = length;
    memcpy(response.payload, data, length); return &response;
  }
  mesh::Packet* createPathReturn(const mesh::Identity& to, const uint8_t* key,
      const uint8_t*, uint8_t, uint8_t type, const uint8_t* data, size_t length) {
    ++path_returns;
    auto* packet = createDatagram(type, to, key, data, length);
    if (packet) packet->path_return = true;
    return packet;
  }
  bool sendDirect(mesh::Packet* packet, const uint8_t*, uint8_t length, unsigned long delay) {
    assert(packet && mesh::Packet::isValidPathLen(length));
    packet->direct = true; packet->path_len = length;
    if (queue_ok) sent.push_back({*packet, delay, 0});
    return queue_ok;
  }
  bool sendFloodReply(mesh::Packet* packet, unsigned long delay, uint8_t hash_size) {
    assert(packet); packet->direct = false;
    if (queue_ok) sent.push_back({*packet, delay, hash_size});
    return queue_ok;
  }
  bool sendClientReply(ClientInfo* client, mesh::Packet* packet, unsigned long delay, uint8_t hash_size) {
    ++client_replies;
    assert(client);
    return mesh::Packet::isValidPathLen(client->out_path_len)
        ? sendDirect(packet, client->out_path, client->out_path_len, delay)
        : sendFloodReply(packet, delay, hash_size);
  }
  uint8_t handleAnonRegionsReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t) { ++anonymous_requests; return metadata_reply ? 13 : 0; }
  uint8_t handleAnonOwnerReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t) { ++anonymous_requests; return metadata_reply ? 13 : 0; }
  uint8_t handleAnonClockReq(const mesh::Identity&, uint32_t, const uint8_t*, size_t) { ++anonymous_requests; return metadata_reply ? 13 : 0; }
  bool handleGuestCommand(const char*, char*, size_t);
  int searchPeersByHash(const uint8_t*);
  void onPeerDataRecv(mesh::Packet*, uint8_t, int, const uint8_t*, uint8_t*, size_t);
  bool onPeerPathRecv(mesh::Packet*, int, const uint8_t*, uint8_t*, uint8_t, uint8_t, uint8_t*, uint8_t);
  int handleRequest(ClientInfo*, uint32_t, uint8_t*, size_t, size_t);
  void dispatch(ClientInfo*, const char*, char*);
};
@LOGIN@
@PASSWORD_WARNING@
@ANONYMOUS@
@COMMAND@
@SEARCH@
@PATH@
@DATA_ADMISSION@
@REQUEST_ADMISSION@
@DISPATCH@

static mesh::Identity identity(unsigned number) {
  mesh::Identity id;
  memset(id.pub_key, 0xAA, PUB_KEY_SIZE);
  id.pub_key[PUB_KEY_SIZE - 1] = uint8_t(number);
  return id;
}
static const uint8_t secret[PUB_KEY_SIZE] = {0xB7};
using LoginResult = mesh::LoginHistory::Result;
static void history(const MyMesh& mesh, unsigned id, uint32_t attempts, LoginResult result) {
  const auto* entry = mesh.login_history.get(0);
  assert(entry && memcmp(entry->key, identity(id).pub_key, PUB_KEY_SIZE) == 0);
  assert(entry->timestamp == mesh.clock.now && entry->attempts == attempts && entry->result == result);
}
static uint8_t login(MyMesh& mesh, unsigned id, const char* password,
                     uint32_t timestamp = 100, bool flood = true) {
  guest_metadata_fs = &mesh.fs;
  return mesh.handleLoginReq(identity(id), secret, timestamp,
                             reinterpret_cast<const uint8_t*>(password), flood);
}
static ClientInfo* add(MyMesh& mesh, unsigned number, uint8_t role) {
  auto* client = mesh.acl.putClient(identity(number), role);
  assert(client);
  memcpy(client->shared_secret, secret, sizeof(secret));
  client->last_activity = 7;
  client->last_timestamp = 10;
  client->out_path_len = 1;
  client->out_path[0] = 0x42;
  return client;
}
static void command(MyMesh& mesh, const char* text, const char* expected) {
  guest_metadata_fs = &mesh.fs;
  char reply[160];
  memset(reply, '#', sizeof(reply));
  assert(mesh.handleGuestCommand(text, reply, 157));
  assert(strcmp(reply, expected) == 0);
  assert(reply[157] == '#' && reply[158] == '#' && reply[159] == '#');
}
static void defaultsAndPasswords() {
  MyMesh mesh;
  assert(!mesh.guest_login.enabled() && mesh.fs.files.empty());
  assert(login(mesh, 1, "") == 0 && mesh.acl.authorizations == 0);
  assert(login(mesh, 1, "wrong") == 0 && mesh.acl.num_clients == 0);
  command(mesh, "get guest", "> off");
  command(mesh, "set guest on", "OK - guest on");
  assert(login(mesh, 1, "") == 13);
  assert(mesh.reply_data[6] == 0 && mesh.reply_data[7] == PERM_ACL_GUEST);
  assert(mesh.acl.clients[0].last_activity == mesh.clock.now);
  assert(mesh.dirty_contacts_expiry == 0); // Guest sessions stay out of the ACL file.
  strcpy(mesh._prefs.guest_password, "guest-code");
  const ClientInfo before = mesh.acl.clients[0];
  assert(login(mesh, 1, "", 101) == 0); // Cached identity cannot bypass changed password.
  assert(login(mesh, 1, "wrong", 102) == 0);
  assert(memcmp(&before, &mesh.acl.clients[0], sizeof(before)) == 0);
  assert(login(mesh, 1, "guest-code", 103) == 13);
  assert(login(mesh, 1, "guest-code", 103) == 0); // Existing replay floor still applies.
  MyMesh reboot;
  reboot.fs.files = mesh.fs.files;
  guest_metadata_fs = &reboot.fs;
  assert(reboot.guest_login.begin(&reboot.fs) && reboot.guest_login.enabled());
  assert(reboot.acl.num_clients == 0);
  command(reboot, "set guest off", "OK - guest off");
  MyMesh off_reboot;
  off_reboot.fs.files = reboot.fs.files;
  guest_metadata_fs = &off_reboot.fs;
  assert(off_reboot.guest_login.begin(&off_reboot.fs) && !off_reboot.guest_login.enabled());
  assert(login(off_reboot, 2, "") == 0);
  MyMesh blank_admin;
  blank_admin._prefs.password[0] = 0;
  assert(login(blank_admin, 1, "") == 0);
  command(blank_admin, "set guest on", "OK - guest on");
  assert(login(blank_admin, 1, "") == 13 && blank_admin.reply_data[7] == PERM_ACL_GUEST);
}
static void assignedRoles() {
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    if ((permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_GUEST) continue;
    MyMesh mesh;
    auto* client = add(mesh, 1, uint8_t(permissions));
    assert(login(mesh, 1, "wrong", 100, false) == 13);
    assert(client->permissions == permissions && client->out_path_len == 1);
    assert(client->last_timestamp == 100 && client->last_activity == mesh.clock.now);
    assert(mesh.reply_data[7] == permissions);
    assert(login(mesh, 1, "admin", 101) == 13);
    assert(client->permissions == ((permissions & ~PERM_ACL_ROLE_MASK) | PERM_ACL_ADMIN));
  }
  MyMesh guest;
  add(guest, 1, 0xA0);
  const ClientInfo before = guest.acl.clients[0];
  assert(login(guest, 1, "", 100) == 0 && guest.acl.authorizations == 0);
  assert(memcmp(&before, &guest.acl.clients[0], sizeof(before)) == 0);
  assert(login(guest, 1, "admin", 101) == 13);
  assert(guest.acl.clients[0].permissions == (0xA0 | PERM_ACL_ADMIN));
  MyMesh failure;
  failure.acl.authorize_ok = false;
  assert(login(failure, 1, "admin") == 0 && failure.acl.allocations == 0);
  failure.acl.authorize_ok = true;
  failure.acl.allocate_ok = false;
  assert(login(failure, 1, "admin") == 0 && failure.acl.num_clients == 0);
}
static void revocationAndFailures() {
  MyMesh mesh;
  command(mesh, "set guest on", "OK - guest on");
  add(mesh, 1, PERM_ACL_GUEST);
  const ClientInfo admin = *add(mesh, 2, PERM_ACL_ADMIN);
  add(mesh, 3, 0xF8);
  const ClientInfo manager = *add(mesh, 4, PERM_ACL_FILTER_MGR);
  command(mesh, "set guest off", "OK - guest off");
  assert(mesh.acl.num_clients == 2);
  assert(memcmp(&admin, &mesh.acl.clients[0], sizeof(admin)) == 0);
  assert(memcmp(&manager, &mesh.acl.clients[1], sizeof(manager)) == 0);
  ClientInfo zero{};
  assert(memcmp(&zero, &mesh.acl.clients[2], sizeof(zero)) == 0);
  assert(memcmp(&zero, &mesh.acl.clients[3], sizeof(zero)) == 0);
  command(mesh, "set guest on", "OK - guest on");
  assert(mesh.acl.getClient(identity(1).pub_key, PUB_KEY_SIZE) == nullptr);
  assert(login(mesh, 1, "", 200) == 13);
  mesh.fs.fail_open_write = true;
  command(mesh, "set guest off", "Err - guest setting save unconfirmed; reboot may differ");
  assert(mesh.guest_login.healthy() && mesh.guest_login.enabled());
  assert(mesh.acl.num_clients == 3); // Confirmed old setting remains live.
  mesh.fs.fail_open_write = false;
  mesh.fs.fail_public_read = true;
  command(mesh, "set guest off", "Err - guest setting save unconfirmed; reboot may differ");
  assert(!mesh.guest_login.healthy() && !mesh.guest_login.enabled());
  assert(mesh.acl.num_clients == 2); // Uncertain storage fails closed and revokes.
  assert(login(mesh, 1, "", 201) == 0);
  command(mesh, "get guest", "> off (settings unavailable)");
  assert(login(mesh, 2, "wrong", 300) == 13); // Assigned admin survives fail-closed guest storage.
}
static void commandPermissions() {
  MyMesh mesh;
  for (const char* invalid : {"get guest on", "set guest", "set guest 1", "set guest ON",
                             "set guest on extra", "set guest off extra"}) {
    command(mesh, invalid, "Err - use get guest or set guest on|off");
    assert(!mesh.guest_login.enabled() && mesh.fs.writes == 0);
  }
  for (const char* unrelated : {"get guests", "get guest.password", "set guest.password secret",
                               "get guestx", "set guestx on"}) {
    char reply[160] = "untouched";
    assert(!mesh.handleGuestCommand(unrelated, reply, sizeof(reply)));
    assert(strcmp(reply, "untouched") == 0);
  }
  for (uint8_t role : {PERM_ACL_GUEST, PERM_ACL_READ_ONLY, PERM_ACL_READ_WRITE,
                       PERM_ACL_REGION_MGR, PERM_ACL_FILTER_MGR, 6, 7}) {
    ClientInfo caller{};
    caller.permissions = role;
    char reply[160]{};
    mesh.dispatch(&caller, "set guest on", reply);
    assert(strcmp(reply, "Err - not permitted") == 0);
    assert(!mesh.guest_login.enabled());
    mesh.dispatch(&caller, "get guest", reply);
    assert(strcmp(reply, "Err - not permitted") == 0);
  }
  ClientInfo admin{};
  admin.permissions = PERM_ACL_ADMIN;
  char reply[160]{};
  mesh.dispatch(&admin, "set guest on", reply);
  assert(strcmp(reply, "OK - guest on") == 0 && mesh.guest_login.enabled());
  mesh.dispatch(nullptr, "set guest off", reply);
  assert(strcmp(reply, "OK - guest off") == 0 && !mesh.guest_login.enabled());
}
static void receiveAdmission() {
  MyMesh mesh;
  // Deliberately leave stale/in-flight guest state to exercise receive gates.
  auto* guest = add(mesh, 1, 0xA0);
  auto* admin = add(mesh, 2, PERM_ACL_ADMIN);
  const uint8_t hash[] = {0xAA};
  assert(mesh.searchPeersByHash(hash) == 1 && mesh.matching_peer_indexes[0] == 1);
  mesh::Packet packet;
  uint8_t data[8] = {1}, path[] = {0x55, 0x66};
  mesh.matching_peer_indexes[0] = 0;
  const ClientInfo before = *guest;
  mesh.onPeerDataRecv(&packet, 0, 0, secret, data, sizeof(data));
  assert(mesh.received == 0);
  assert(mesh.handleRequest(guest, 100, data, sizeof(data), 160) == 0);
  assert(!mesh.onPeerPathRecv(&packet, 0, secret, path, 2, 0x0F, nullptr, 0));
  assert(memcmp(&before, guest, sizeof(before)) == 0);
  mesh.matching_peer_indexes[0] = 1;
  mesh.onPeerDataRecv(&packet, 0, 0, secret, data, sizeof(data));
  assert(mesh.received == 1);
  assert(mesh.handleRequest(admin, 100, data, sizeof(data), 160) == 1);
  assert(!mesh.onPeerPathRecv(&packet, 0, secret, path, 2, 0x0F, nullptr, 0));
  assert(admin->out_path_len == 2 && admin->last_activity == mesh.clock.now);
  command(mesh, "set guest on", "OK - guest on");
  assert(mesh.searchPeersByHash(hash) == 2);
  mesh.matching_peer_indexes[0] = 0;
  mesh.onPeerDataRecv(&packet, 0, 0, secret, data, sizeof(data));
  assert(mesh.received == 3);
  assert(mesh.handleRequest(guest, 100, data, sizeof(data), 160) == 1);
  assert(!mesh.onPeerPathRecv(&packet, 0, secret, path, 2, 0x0F, nullptr, 0));
  assert(guest->out_path_len == 2 && guest->last_activity == mesh.clock.now);
  // A flood login still clears the old selected path and transient observation.
  guest->observed_path_pending = true;
  assert(login(mesh, 1, "", 200, true) == 13);
  assert(guest->out_path_len == OUT_PATH_UNKNOWN && !guest->observed_path_pending);
}
static void loginHistoryDecisions() {
  MyMesh mesh;
  assert(mesh.login_history.size() == 0);
  assert(login(mesh, 1, "") == 0);
  assert(mesh.login_history.size() == 1);
  history(mesh, 1, 1, LoginResult::GuestsDisabled);
  assert(login(mesh, 1, "wrong") == 0);
  history(mesh, 1, 2, LoginResult::GuestsDisabled);
  command(mesh, "set guest on", "OK - guest on");
  strcpy(mesh._prefs.guest_password, "private-guest");
  assert(login(mesh, 1, "wrong") == 0);
  history(mesh, 1, 3, LoginResult::WrongPassword);
  assert(login(mesh, 1, "private-guest") == 13);
  history(mesh, 1, 4, LoginResult::Accepted);
  assert(login(mesh, 1, "private-guest") == 0);
  history(mesh, 1, 5, LoginResult::ReplayOrStateRejected);
  mesh.acl.authorize_ok = false;
  assert(login(mesh, 2, "admin") == 0);
  history(mesh, 2, 1, LoginResult::ReplayOrStateRejected);
  mesh.acl.authorize_ok = true;
  mesh.acl.allocate_ok = false;
  assert(login(mesh, 2, "admin") == 0);
  history(mesh, 2, 2, LoginResult::ACLUnavailable);
  mesh.acl.allocate_ok = true;
  assert(login(mesh, 2, "admin") == 13);
  history(mesh, 2, 3, LoginResult::Accepted);
  command(mesh, "set guest off", "OK - guest off");
  assert(mesh.login_history.size() == 2); // Revoking sessions preserves attempts.
  assert(login(mesh, 2, "wrong", 101) == 13); // Authorized ACL identity.
  history(mesh, 2, 4, LoginResult::Accepted);
  auto* manager = add(mesh, 3, PERM_ACL_FILTER_MGR);
  manager->last_timestamp = 10;
  assert(login(mesh, 3, "wrong", 101) == 13);
  history(mesh, 3, 1, LoginResult::Accepted);
  assert(login(mesh, 1, "private-guest", 102) == 0);
  history(mesh, 1, 6, LoginResult::GuestsDisabled);
  const auto before = mesh.fs.files;
  const auto writes = mesh.fs.writes;
  const uint32_t before_attempts = mesh.login_history.get(0)->attempts;
  char reply[160];
  assert(mesh::cli::handleLoginsGet(mesh.login_history, "get logins", reply, sizeof(reply)));
  assert(!strstr(reply, "private-guest") && !strstr(reply, "admin") && !strstr(reply, "wrong"));
  assert(mesh.fs.files == before && mesh.fs.writes == writes);
  assert(mesh.login_history.get(0)->attempts == before_attempts);
  MyMesh reboot;
  assert(reboot.login_history.size() == 0); // History is deliberately volatile.
}
static void anonymousRequestsAreNotLogins() {
  MyMesh mesh;
  mesh::Packet packet;
  uint8_t data[MAX_PACKET_PAYLOAD + 1]{};
  const auto id = identity(1);
  guest_metadata_fs = &mesh.fs;
  for (uint8_t type : {ANON_REQ_TYPE_REGIONS, ANON_REQ_TYPE_OWNER, ANON_REQ_TYPE_BASIC}) {
    data[4] = type;
    mesh.onAnonDataRecv(&packet, secret, id, data, 6);
    assert(mesh.login_history.size() == 0);
  }
  assert(mesh.anonymous_requests == 3);
  data[4] = 0x04; // Unknown anonymous query.
  mesh.onAnonDataRecv(&packet, secret, id, data, 6);
  packet.direct = false;
  data[4] = ANON_REQ_TYPE_REGIONS;
  mesh.onAnonDataRecv(&packet, secret, id, data, 6);
  mesh.onAnonDataRecv(nullptr, secret, id, data, 6);
  mesh.onAnonDataRecv(&packet, secret, id, nullptr, 6);
  for (size_t length : {size_t(0), size_t(3), size_t(MAX_PACKET_PAYLOAD)})
    mesh.onAnonDataRecv(&packet, secret, id, data, length);
  packet.type = 0;
  data[4] = 0;
  mesh.onAnonDataRecv(&packet, secret, id, data, 4);
  assert(mesh.login_history.size() == 0);
  packet.type = PAYLOAD_TYPE_ANON_REQ;
  // A timestamp-only anonymous packet is the valid empty-password login form.
  mesh.onAnonDataRecv(&packet, secret, id, data, 4);
  history(mesh, 1, 1, LoginResult::GuestsDisabled);
  assert(mesh.anonymous_requests == 3);
}
static void useFactoryPassword(MyMesh& mesh) {
  memset(mesh._prefs.password, 0, sizeof(mesh._prefs.password));
  const size_t limit = sizeof(mesh._prefs.password) - 1;
  const size_t length = strlen(ADMIN_PASSWORD) < limit ? strlen(ADMIN_PASSWORD) : limit;
  memcpy(mesh._prefs.password, ADMIN_PASSWORD, length);
}
static void defaultPasswordWarnings() {
  const auto dispatch = [](MyMesh& mesh, const char* password, bool flood, uint32_t timestamp = 100) {
    guest_metadata_fs = &mesh.fs;
    uint8_t data[40]{}; memcpy(data, &timestamp, 4);
    memcpy(data + 4, password, strlen(password));
    mesh::Packet request; request.direct = !flood; request.path_len = 0x42;
    mesh.onAnonDataRecv(&request, secret, identity(1), data, 4 + strlen(password));
  };
  for (bool flood : {false, true}) {
    MyMesh mesh; useFactoryPassword(mesh);
    dispatch(mesh, mesh._prefs.password, flood);
    assert(mesh.sent.size() == 2 && mesh.creations == 2);
    const auto& login = mesh.sent[0]; const auto& warning = mesh.sent[1];
    assert(login.packet.type == PAYLOAD_TYPE_RESPONSE && login.packet.payload_len == 13);
    assert(login.packet.payload[4] == RESP_SERVER_LOGIN_OK && login.packet.payload[12] == FIRMWARE_VER_LEVEL);
    assert(login.delay == SERVER_RESPONSE_DELAY && !login.packet.radio_reply);
    assert(warning.packet.type == PAYLOAD_TYPE_TXT_MSG && warning.packet.payload_len == 25);
    assert(warning.packet.payload[4] == (TXT_TYPE_CLI_DATA << 2));
    assert(!memcmp(warning.packet.payload + 5, "CHANGE YOUR PASSWORD", 20));
    assert(!warning.packet.radio_reply && warning.delay == SERVER_RESPONSE_DELAY + 2000UL);
    assert(!warning.packet.direct && warning.hash_size == 2);
    assert(mesh.path_returns == (flood ? 1U : 0U));
    assert(mesh.client_replies == 0); // Unknown routes use the scoped flood reply boundary.
  }
  // An assigned administrator may reconnect without resending the password.
  MyMesh assigned; useFactoryPassword(assigned);
  add(assigned, 1, PERM_ACL_ADMIN); dispatch(assigned, "", false);
  assert(assigned.sent.size() == 2 && assigned.sent[1].packet.direct);
  assert(assigned.client_replies == 1);
  assigned.metadata_reply = true;
  uint8_t query[7]{}; query[4] = ANON_REQ_TYPE_OWNER;
  mesh::Packet metadata;
  assigned.onAnonDataRecv(&metadata, secret, identity(1), query, sizeof(query) - 1);
  assert(assigned.sent.size() == 3 && assigned.client_replies == 1);
  for (unsigned failure = 0; failure < 9; ++failure) {
    MyMesh mesh; useFactoryPassword(mesh);
    const char* password = mesh._prefs.password;
    if (failure == 0) { strcpy(mesh._prefs.password, "changed-secret"); password = "changed-secret"; }
    if (failure == 1) password = "wrong";
    if (failure == 2) { assert(mesh.guest_login.setEnabled(true)); password = ""; }
    if (failure == 3) mesh.acl.authorize_ok = false;
    if (failure == 4) mesh.create_ok = false;
    if (failure == 5) mesh.queue_ok = false;
    if (failure == 6) { add(mesh, 1, PERM_ACL_FILTER_MGR); password = ""; }
    if (failure == 7) mesh.acl.allocate_ok = false;
    if (failure == 8) mesh.fail_creation_on = 2;
    dispatch(mesh, password, false);
    assert(mesh.sent.size() == (failure == 0 || failure == 2 || failure == 6 || failure == 8 ? 1U : 0U));
    for (const auto& sent : mesh.sent) assert(sent.packet.type == PAYLOAD_TYPE_RESPONSE);
  }
  MyMesh replay; useFactoryPassword(replay);
  dispatch(replay, replay._prefs.password, false);
  dispatch(replay, replay._prefs.password, false);
  assert(replay.sent.size() == 2);
  MyMesh empty; auto* admin = add(empty, 1, PERM_ACL_ADMIN);
  empty._prefs.password[0] = 0;
  empty.sendDefaultPasswordWarning(admin, 100, 1);
  empty.sendDefaultPasswordWarning(nullptr, 100, 1);
  assert(empty.creations == 0 && empty.sent.empty());
}
// Only a fresh strict-admin login commits a remotely loaded radio preset.
static void presetCommitOnlyForAdmin() {
  const auto dispatch = [](MyMesh& mesh, const char* password, uint32_t timestamp) {
    guest_metadata_fs = &mesh.fs;
    uint8_t data[40]{}; memcpy(data, &timestamp, 4);
    memcpy(data + 4, password, strlen(password));
    mesh::Packet request; request.direct = false; request.path_len = 0x42;
    mesh.onAnonDataRecv(&request, secret, identity(1), data, 4 + strlen(password));
  };
  MyMesh admin;
  dispatch(admin, "admin", 100);
  assert(admin.login_full_admin && admin._cli.presets.admin_logins == 1);
  dispatch(admin, "admin", 100);  // replayed timestamp: rejected, nothing committed
  assert(!admin.login_full_admin && admin._cli.presets.admin_logins == 1);
  MyMesh stranger;
  dispatch(stranger, "wrong", 100);
  assert(!stranger.login_full_admin && stranger._cli.presets.admin_logins == 0);
  for (uint8_t role : {uint8_t(PERM_ACL_REGION_MGR), uint8_t(PERM_ACL_FILTER_MGR),
                       uint8_t(PERM_ACL_READ_WRITE)}) {
    MyMesh manager;
    add(manager, 1, role);
    dispatch(manager, "wrong", 100);  // preauthorized role logs in without a password
    assert(manager.sent.size() >= 1 && manager.sent[0].packet.payload[4] == RESP_SERVER_LOGIN_OK);
    assert(!manager.login_full_admin && manager._cli.presets.admin_logins == 0);
  }
  MyMesh guest;
  command(guest, "set guest on", "OK - guest on");
  dispatch(guest, "", 100);
  assert(guest.sent.size() >= 1 && !guest.login_full_admin);
  assert(guest._cli.presets.admin_logins == 0);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "passwords")) defaultsAndPasswords();
  else if (!strcmp(argv[1], "roles")) assignedRoles();
  else if (!strcmp(argv[1], "revoke")) revocationAndFailures();
  else if (!strcmp(argv[1], "commands")) commandPermissions();
  else if (!strcmp(argv[1], "receive")) receiveAdmission();
  else if (!strcmp(argv[1], "history")) loginHistoryDecisions();
  else if (!strcmp(argv[1], "anonymous")) anonymousRequestsAreNotLogins();
  else if (!strcmp(argv[1], "default-password")) defaultPasswordWarnings();
  else if (!strcmp(argv[1], "preset-commit")) presetCommitOnlyForAdmin();
  else assert(false);
  printf("repeater guest %s passed\n", argv[1]);
}
'''


class RepeaterGuestAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++17 compiler required")
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        acl_header = (ROOT / "src/helpers/ClientACL.h").read_text()
        acl_source = (ROOT / "src/helpers/ClientACL.cpp").read_text()
        constants = "\n".join(line for line in acl_header.splitlines()
                              if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
        for name in ("FIRMWARE_VER_LEVEL", "RESP_SERVER_LOGIN_OK", "LAZY_CONTACTS_WRITE_DELAY",
                     "ANON_REQ_TYPE_REGIONS", "ANON_REQ_TYPE_OWNER", "ANON_REQ_TYPE_BASIC"):
            constants += "\n" + re.search(r"^#define\s+" + name + r"\s+.*$", source, re.MULTILINE).group()
        constants += "\n" + re.search(
            r"static constexpr uint32_t LOGIN_PATH_OBSERVATION_TIMEOUT_MS[^;]*;", source).group()
        constants += "\n" + re.search(r"^\s*#define SERVER_RESPONSE_DELAY[^\n]*",
            source, re.MULTILINE).group()
        manager_helpers = "\n".join(extract_braced(source, signature) for signature in (
            "static bool commandFamilyMatches(", "static bool commandListMatches(",
            "static bool isCommonManagerReadOnlyAllowed(", "static bool isRegionMgrAllowed(",
            "static bool isFilterMgrAllowed(",
        ))
        dispatch = extract_braced(source, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        call = dispatch.index("handleGuestCommand(command, reply, 157)")
        guard_start = dispatch.rindex("if (sender && !sender->isAdmin())", 0, call)
        guard = extract_braced(dispatch[guard_start:], "if (sender && !sender->isAdmin())")
        # Exercise the actual authorization branch and setter, excluding unrelated
        # radio commands and the independently tested guest-list formatter.
        command_dispatch = "void MyMesh::dispatch(ClientInfo* sender, const char* command, char* reply) {\n" + guard + "\nif (handleGuestCommand(command, reply, 157)) return;\nstrcpy(reply, \"unhandled\");\n}"
        peer = extract_braced(source, "void MyMesh::onPeerDataRecv(")
        gate_end = peer.index("#if defined(WITH_MQTT_NEIGHBORS)", peer.index("ClientInfo* client = acl.getClientByIdx(i);"))
        # Compile the real receive admission through the guest gate. Radio request
        # execution is replaced by a counter only after authorization succeeds.
        data_admission = peer[:gate_end] + "++received;\n}"
        request = extract_braced(source, "int MyMesh::handleRequest(")
        request_admission = request[:request.index("  if (reply_capacity >")] + "++received; return 1;\n}"
        anonymous = extract_braced(source, "void MyMesh::onAnonDataRecv(")
        cls.source_text = source
        packet = (ROOT / "src/Packet.cpp").read_text()
        routing = (ROOT / "src/helpers/RoutingPolicy.h").read_text()
        replacements = {
            "@CONSTANTS@": constants,
            "@CLIENT_INFO@": extract_braced(acl_header, "struct ClientInfo {"),
            "@REMOVE_GUESTS@": extract_braced(acl_source, "int ClientACL::removeGuestClients("),
            "@MANAGER_HELPERS@": manager_helpers,
            "@LOGIN@": extract_braced(source, "uint8_t MyMesh::handleLoginReq("),
            "@PASSWORD_WARNING@": extract_braced(source, "void MyMesh::sendDefaultPasswordWarning("),
            "@PACKET_HELPERS@": "\n".join(extract_braced(packet, signature) for signature in (
                "bool Packet::isValidPathLen(", "size_t Packet::writePath(")),
            "@ROUTING_HELPERS@": extract_braced(routing, "enum ReplyRoute") + ";\n"
                + extract_braced(routing, "inline ReplyRoute chooseReplyRoute("),
            "@ANONYMOUS@": anonymous,
            "@COMMAND@": extract_braced(source, "bool MyMesh::handleGuestCommand("),
            "@SEARCH@": extract_braced(source, "int MyMesh::searchPeersByHash("),
            "@PATH@": extract_braced(source, "bool MyMesh::onPeerPathRecv("),
            "@DATA_ADMISSION@": data_admission,
            "@REQUEST_ADMISSION@": request_admission,
            "@DISPATCH@": command_dispatch,
        }
        generated = HARNESS
        for marker, text in replacements.items():
            generated = generated.replace(marker, text)
        cls.work = tempfile.TemporaryDirectory(prefix="mesh-repeater-guest-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        cpp = work / "test.cpp"
        cpp.write_text(generated)
        cls.binary = work / ("guest.exe" if sys.platform == "win32" else "guest")
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        command = [
            compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-misleading-indentation",
            "-DESP32_PLATFORM", "-DESP32", *sanitizer,
            "-I", str(FIXTURE / "mocks"), "-I", str(ROOT / "src"),
            str(cpp), str(ROOT / "src/helpers/GuestLoginSettings.cpp"), "-o", str(cls.binary),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.generated = generated
        cls.compile_command = command
        cls.source = cpp

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("repeater guest " + name + " passed", result.stdout)

    def test_default_password_change_and_reboot(self):
        self.scenario("passwords")

    def test_assigned_roles_and_admin_upgrade(self):
        self.scenario("roles")

    def test_disable_revokes_only_guests_and_storage_failure_fails_closed(self):
        self.scenario("revoke")

    def test_command_validation_and_admin_authorization(self):
        self.scenario("commands")

    def test_peer_request_and_path_admission(self):
        self.scenario("receive")

    def test_login_history_records_actual_authorization_results(self):
        self.scenario("history")

    def test_only_login_requests_enter_history(self):
        self.scenario("anonymous")

    def test_default_password_admin_login_warns_after_the_binary_reply(self):
        self.scenario("default-password")

    def test_only_strict_admin_login_commits_radio_preset(self):
        self.scenario("preset-commit")

    def test_custom_factory_password_and_bounded_long_factory_password(self):
        for index, factory in enumerate(("factory-admin", "12345678901234567890")):
            with self.subTest(factory=factory):
                source = Path(self.work.name) / ("factory-" + str(index) + ".cpp")
                source.write_text(self.generated.replace('#define ADMIN_PASSWORD "password"',
                    '#define ADMIN_PASSWORD ' + json.dumps(factory)))
                binary = source.with_suffix("")
                command = [str(source) if value == str(self.source)
                           else str(binary) if value == str(self.binary) else value
                           for value in self.compile_command]
                built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                checked = subprocess.run([str(binary), "default-password"],
                                         capture_output=True, text=True, timeout=15)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_password_bytes_are_not_logged(self):
        login = extract_braced(self.source_text, "uint8_t MyMesh::handleLoginReq(")
        debug_calls = re.findall(r"MESH_DEBUG_PRINTLN\([\s\S]*?\);", login)
        self.assertFalse(any(re.search(r"\bdata\b", call) for call in debug_calls), debug_calls)

    def test_guest_controls_precede_portable_role_handoff(self):
        # Current FULL builds and legacy PORTABLE_MQTT_OBSERVER builds both need
        # the same saved policy and permissions, rather than a generic CLI fallback.
        body = extract_braced(self.source_text, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        self.assertEqual(body.count("handleGuestCommand(command, reply, 157)"), 2)
        self.assertEqual(body.count("mesh::cli::handleGuestsGet(acl, command, reply, 157)"), 2)
        self.assertEqual(body.count("mesh::cli::handleLoginsGet(login_history, command, reply, 157)"), 2)


if __name__ == "__main__":
    unittest.main()
