#!/usr/bin/env python3
"""Execute room per-client path commands and delivery against production bodies."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/room_client_paths.cpp"

CASES = r'''
static void roleCases() {
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    for (bool logged_in : {false, true}) {
      MyMesh mesh;
      auto& own = mesh.acl.clients[0];
      own.permissions = permissions;
      own.last_activity = logged_in ? 800 : 0;
      const ClientInfo other = mesh.acl.clients[1];
      const uint8_t role = permissions & PERM_ACL_ROLE_MASK;
      const bool allowed = role == PERM_ACL_READ_ONLY || role == PERM_ACL_READ_WRITE
          || role == PERM_ACL_ADMIN || (role == PERM_ACL_GUEST && logged_in);
      mesh.command("set altpath B1,B2");
      assert(mesh.general_commands == 0);
      assert(memcmp(&mesh.acl.clients[1], &other, sizeof(other)) == 0);
      assert(own.permissions == permissions && !own.permissions_are_explicit);
      if (allowed) {
        assert(own.alt_path_len == 2 && own.alt_path[0] == 0xB1 && own.alt_path[1] == 0xB2);
        assert(mesh.reply() == "> B1,B2");
        assert(mesh.acl.saves == (role == PERM_ACL_ADMIN ? 1U : 0U));
      } else {
        assert(own.alt_path_len == OUT_PATH_UNKNOWN && mesh.acl.saves == 0);
        if (role == PERM_ACL_GUEST) {
          assert(mesh.sent.empty() && own.last_timestamp == 99 && own.last_activity == 0);
          mesh.command("set altpath C1", 101);
          assert(own.alt_path_len == OUT_PATH_UNKNOWN && own.last_activity == 0);
          assert(mesh.sent.empty() && own.last_timestamp == 99);
        } else assert(mesh.reply().find("Err") != std::string::npos);
      }
    }
  }
  MyMesh mesh;
  char command[] = "get outpath", reply[180]{};
  assert(mesh.handleClientPathCommand(nullptr, command, reply));
  assert(strstr(reply, "Err") && mesh.acl.saves == 0);
  puts("256 permission combinations and live reader roles remain self-only passed");
}

static void exactAllowlist() {
  const char* rejected[] = {
    "get prv.key", "erase", "set password owned", "set radio 915,500,5,8",
    "get outpathx", "get outpath path extra", "get altpath path", "set outpathx direct",
    "set altpath path", "get outpath;erase", "set outpath A1;erase",
    "AA|BB|set outpath direct", "set outpath direct;erase", "set altpath B1,,B2",
    "set outpath B1,", "set outpath B1,1234", "set outpath +B", "set outpath",
  };
  for (const char* text : rejected) {
    MyMesh mesh;
    const ClientInfo before = mesh.acl.clients[0], other = mesh.acl.clients[1];
    mesh.command(text);
    const auto& own = mesh.acl.clients[0];
    assert(mesh.general_commands == 0 && mesh.acl.saves == 0);
    assert(own.permissions == before.permissions && own.out_path_len == before.out_path_len);
    assert(memcmp(own.out_path, before.out_path, MAX_PATH_SIZE) == 0);
    assert(own.alt_path_len == before.alt_path_len);
    assert(memcmp(&mesh.acl.clients[1], &other, sizeof(other)) == 0);
    assert(mesh.reply().find("Err") != std::string::npos);
  }
  MyMesh normalized;
  normalized.command("  AA|  SET outpath B1,B2 \t\r\n");
  assert(normalized.acl.clients[0].out_path_len == 2);
  assert(normalized.reply() == "AA|> B1,B2" && normalized.general_commands == 0);
  MyMesh chat;
  chat.command("set outpath B1", 100, 0, 0, TXT_TYPE_PLAIN);
  assert(chat.post_count == 1 && chat.acl.clients[0].out_path[0] == 0xA1);
  MyMesh banned;
  assert(banned.room_access.addBan(&banned.policy_fs, banned.acl.clients[0].id.pub_key)
         == mesh::RoomAccessPolicy::BanResult::Saved);
  banned.command("set outpath B1");
  assert(banned.sent.empty() && banned.acl.clients[0].out_path[0] == 0xA1);
  puts("exact allowlist, correlation normalization, chat isolation, and ban gates passed");
}

static void pathFormats() {
  struct Case { const char* command; uint8_t length; const char* response; };
  const Case cases[] = {
    {"set outpath B1,B2", 2, "> B1,B2"},
    {"set outpath b100, b200", 0x42, "> B100,B200"},
    {"set outpath B10011\tB20022", 0x82, "> B10011,B20022"},
    {"set outpath direct", 0, "> direct"},
    {"set outpath clear", OUT_PATH_UNKNOWN, "> outpath cleared"},
    {"set outpath flood", OUT_PATH_FORCE_FLOOD, "> flood"},
    {"set altpath direct", 0, "> direct"},
    {"set altpath clear", OUT_PATH_UNKNOWN, "> unknown"},
  };
  for (const auto& c : cases) {
    MyMesh mesh; mesh.command(c.command);
    const bool alt = strstr(c.command, "altpath") != nullptr;
    assert((alt ? mesh.acl.clients[0].alt_path_len : mesh.acl.clients[0].out_path_len) == c.length);
    assert(mesh.reply() == c.response);
    assert(mesh.acl.saves == 0 && !mesh.acl.clients[0].permissions_are_explicit);
  }
  MyMesh observed;
  auto& client = observed.acl.clients[0];
  mesh::beginObservedClientPath(client, OUT_PATH_UNKNOWN, ticks + 50);
  observed.command("get outpath path"); assert(observed.reply() == "> path pending");
  observed.command("set outpath path", 101); assert(observed.reply().find("pending") != std::string::npos);
  const uint8_t route[] = {0x12, 0x34, 0x56, 0x78};
  assert(mesh::captureObservedClientPath(client, route, 0x42, ticks));
  observed.command("get outpath path", 102); assert(observed.reply() == "> 1234,5678");
  assert(client.out_path_len == 1 && client.out_path[0] == 0xA1);
  observed.command("set outpath path", 103);
  assert(client.out_path_len == 0x42 && memcmp(client.out_path, route, sizeof(route)) == 0);
  MyMesh unavailable;
  unavailable.command("set outpath path"); assert(unavailable.reply().find("no path") != std::string::npos);
  assert(unavailable.acl.clients[0].out_path[0] == 0xA1);
  uint8_t path[MAX_PATH_SIZE]; memset(path, 0xAB, sizeof(path));
  for (unsigned length = 0; length < 256; ++length) {
    for (size_t capacity = 0; capacity <= 180; ++capacity) {
      uint8_t bounded[184]; memset(bounded, 0xCC, sizeof(bounded));
      mesh::formatRoomClientPathReply(path, length, (char*)bounded + 2, capacity);
      assert(bounded[0] == 0xCC && bounded[1] == 0xCC);
      for (size_t i = 2 + capacity; i < sizeof(bounded); ++i) assert(bounded[i] == 0xCC);
      if (capacity) assert(memchr(bounded + 2, 0, capacity) != nullptr);
    }
  }
  puts("one-to-three byte paths, route sentinels, observed paths, and bounded formatting passed");
}

static void persistenceCases() {
  for (uint8_t role : {uint8_t(PERM_ACL_GUEST), uint8_t(PERM_ACL_READ_ONLY),
                       uint8_t(PERM_ACL_READ_WRITE), uint8_t(PERM_ACL_ADMIN)}) {
    for (bool explicit_role : {false, true}) {
      MyMesh mesh; auto& own = mesh.acl.clients[0];
      own.permissions = role; own.permissions_are_explicit = explicit_role; own.last_activity = 900;
      const uint8_t old_permissions = own.permissions;
      mesh.command("set outpath B1");
      const bool durable = explicit_role || role == PERM_ACL_ADMIN;
      assert(mesh.acl.saves == unsigned(durable));
      assert(own.out_path_is_persistable);
      assert(own.permissions_are_explicit == explicit_role && own.permissions == old_permissions);
      if (durable) {
        assert(mesh.acl.retained[0] && !mesh.acl.retained[1]);
        assert(mesh.acl.durable[0].out_path[0] == 0xB1);
      }
      mesh.command("set outpath B1", 101);
      assert(mesh.acl.saves == unsigned(durable)); // A no-op does not wear flash.
      mesh.command("set altpath C1", 102);
      assert(mesh.acl.saves == (durable ? 2U : 0U));
      mesh.command("set altpath C1", 103);
      assert(mesh.acl.saves == (durable ? 2U : 0U));
    }
  }
  for (const char* command_text : {"set outpath B1", "set altpath C1"}) {
    MyMesh mesh; auto& own = mesh.acl.clients[0];
    own.permissions = PERM_ACL_ADMIN; own.out_path_is_persistable = true;
    const ClientInfo before = own;
    mesh.acl.save_ok = false; mesh.dirty_contacts_expiry = 50000; mesh.contacts_save_failures = 3;
    char command[80], reply[180]{}; strcpy(command, command_text);
    assert(mesh.handleClientPathCommand(&own, command, reply));
    assert(strstr(reply, "save failed") && mesh.acl.saves == 1);
    assert(memcmp(&own, &before, sizeof(own)) == 0);
    assert(mesh.dirty_contacts_expiry == 50000 && mesh.contacts_save_failures == 3);
  }
  puts("transient roles stay transient, filtered durability, no-op writes, and save rollback passed");
}

static void retryCases() {
  MyMesh mesh; auto& own = mesh.acl.clients[0];
  own.permissions_are_explicit = true;
  mesh.command("set outpath B1,B2", 100, 0, 777);
  assert(mesh.acl.saves == 1 && mesh.reply() == "> B1,B2");
  mesh.command("set outpath C1", 101);
  assert(mesh.acl.saves == 2 && own.out_path_len == 1 && own.out_path[0] == 0xC1);
  mesh.command("set outpath B1,B2", 102, 0, 777);
  assert(mesh.acl.saves == 2 && own.out_path_len == 1 && own.out_path[0] == 0xC1);
  assert(mesh.reply() == "> B1,B2"); // Recover the original result without repeating mutation.
  const size_t before_reject = mesh.sent.size();
  mesh.command("set outpath D1", 102); // Equal-time different request is not fresh.
  mesh.command("set outpath D1", 99); // Old uncached request is not fresh.
  assert(mesh.acl.saves == 2 && mesh.sent.size() == before_reject && own.out_path[0] == 0xC1);
  mesh.acl.clients[1].permissions_are_explicit = true;
  mesh.command("set outpath B1,B2", 100, 1, 777);
  assert(mesh.acl.saves == 3 && mesh.acl.clients[1].out_path_len == 2);
  // A prior Admin result must not become readable after the role is demoted.
  MyMesh demoted; demoted.acl.clients[0].permissions = PERM_ACL_ADMIN;
  demoted.command("get prv.key", 100, 0, 999);
  assert(demoted.general_commands == 1 && demoted.reply() == "OK admin");
  demoted.acl.clients[0].permissions = PERM_ACL_READ_WRITE;
  demoted.command("get prv.key", 101, 0, 999);
  assert(demoted.general_commands == 1 && demoted.reply() != "OK admin");
  for (uint8_t role : {uint8_t(PERM_ACL_FILTER_MGR), uint8_t(PERM_ACL_REGION_MGR), uint8_t(7)}) {
    MyMesh revoked; revoked.command("get outpath", 100, 0, 123);
    revoked.acl.clients[0].permissions = role;
    revoked.command("get outpath", 101, 0, 123);
    assert(revoked.reply().find("Err") != std::string::npos);
  }
  puts("cached setters execute once, sender isolation, replay denial, and demotion gates passed");
}

static mesh::Packet* packet(MyMesh& mesh, uint8_t flags = TXT_TYPE_CLI_DATA) {
  mesh::Identity id{}; uint8_t secret[PUB_KEY_SIZE]{};
  const uint8_t data[] = {1, 2, 3, 4, uint8_t(flags << 2), 'r', 'e', 'p', 'l', 'y'};
  return mesh.createDatagram(PAYLOAD_TYPE_TXT_MSG, id, secret, data, sizeof(data));
}
static void alternateCases() {
  MyMesh mesh; auto& own = mesh.acl.clients[0]; own.alt_path_len = 1; own.alt_path[0] = 0xB1;
  auto p = packet(mesh); p->radio_reply = true;
  assert(mesh.sendClientReply(&own, p, 300, 2));
  assert(mesh.sent.size() == 2 && mesh.sent[0].retry_enabled && !mesh.sent[1].retry_enabled);
  assert(mesh.sent[0].path[0] == 0xA1 && mesh.sent[1].path[0] == 0xB1);
  assert(mesh.sent[0].packet.payload_len == mesh.sent[1].packet.payload_len);
  assert(memcmp(mesh.sent[0].packet.payload, mesh.sent[1].packet.payload, p->payload_len) == 0);
  assert(mesh.sent[1].packet.radio_reply && mesh._prefs.direct_retry_enabled == 1);
  for (unsigned mode = 0; mode < 4; ++mode) {
    MyMesh single; auto& c = single.acl.clients[0];
    if (mode == 0) { c.alt_path_len = 1; memcpy(c.alt_path, c.out_path, MAX_PATH_SIZE); c.alt_path[63] ^= 1; }
    if (mode == 1) c.alt_path_len = OUT_PATH_FORCE_FLOOD;
    if (mode == 2) { c.out_path_len = OUT_PATH_UNKNOWN; c.alt_path_len = 1; }
    if (mode == 3) { c.alt_path_len = 1; c.alt_path[0] = 0xB1; single.fail_allocation = 2; }
    assert(single.sendClientReply(&c, packet(single), 300, 2));
    assert(single.sent.size() == 1 && single.allocations <= 2);
    assert(single.sent[0].direct == (mode != 2));
  }
  MyMesh failed; auto& c = failed.acl.clients[0]; c.alt_path_len = 1; c.alt_path[0] = 0xB1;
  failed.send_ok = false;
  assert(!failed.sendClientReply(&c, packet(failed), 300, 2));
  assert(failed.sent.size() == 1 && failed.releases == 1 && failed._prefs.direct_retry_enabled == 1);
  MyMesh exhausted; exhausted.fail_allocation = 1; exhausted.command("set outpath B1");
  assert(exhausted.acl.clients[0].out_path[0] == 0xB1 && exhausted.sent.empty());
  MyMesh duplicate; duplicate.acl.clients[0].alt_path_len = 1; duplicate.acl.clients[0].alt_path[0] = 0xB1;
  duplicate.command("set outpath C1", 100, 0, 123);
  duplicate.command("set outpath C1", 101, 0, 123);
  assert(duplicate.sent.size() == 4 && duplicate.acl.saves == 0);
  assert(duplicate.sent[0].packet.payload_len == duplicate.sent[1].packet.payload_len);
  assert(memcmp(duplicate.sent[0].packet.payload, duplicate.sent[1].packet.payload,
                duplicate.sent[0].packet.payload_len) == 0);
  puts("alternate encrypted copies retain primary retries and tolerate admission or pool failure passed");
}

static void pushCases() {
  MyMesh mesh; auto& c = mesh.acl.clients[0]; c.alt_path_len = 1; c.alt_path[0] = 0xB1;
  mesh::Identity author{}; memset(author.pub_key, 0x55, PUB_KEY_SIZE);
  assert(mesh.pushRoomTextToClient(&c, 700, author, "hello room", 22));
  assert(mesh.sent.size() == 2 && mesh.retry_replacements == 1 && mesh._num_post_pushes == 1);
  assert(mesh.sent[0].retry_enabled && !mesh.sent[1].retry_enabled && mesh.retry_timestamp == 700);
  assert(c.extra.room.push_post_timestamp == 700 && c.extra.room.pending_topic_revision == 22);
  assert(c.extra.room.pending_ack != 0 && c.extra.room.ack_timeout == ticks + PUSH_TIMEOUT_BASE + 2 * PUSH_ACK_TIMEOUT_FACTOR);
  assert(mesh.sent[0].packet.radio_reply && mesh.sent[1].packet.radio_reply);
  assert(memcmp(mesh.sent[0].packet.payload, mesh.sent[1].packet.payload, mesh.sent[0].packet.payload_len) == 0);
  MyMesh failed; failed.send_ok = false;
  assert(!failed.pushRoomTextToClient(&failed.acl.clients[0], 700, author, "hello room", 22));
  assert(failed.retry_replacements == 0 && failed._num_post_pushes == 0);
  assert(failed.acl.clients[0].extra.room.pending_ack == 0);
  puts("actual room post and topic pushes share copies, ACK state, and primary retry ownership passed");
}

static void backlog(MyMesh& mesh) {
  const unsigned indexes[] = {23, 0, 12, 9, 31};
  const uint32_t timestamps[] = {100, 200, 300, 400, 500};
  for (unsigned index = 0; index < 5; ++index) {
    auto& post = mesh.posts[indexes[index]];
    post.post_timestamp = timestamps[index];
    post.author = mesh.acl.clients[index == 1 || index == 3 ? 0 : 1].id;
    strcpy(post.text, "unread post");
  }
  mesh.acl.clients[0].extra.room.sync_since = 50;
}

static void catchupRadio() {
  for (unsigned permissions = 0; permissions < 256; ++permissions) {
    for (bool active : {false, true}) {
      MyMesh mesh; backlog(mesh); auto& own = mesh.acl.clients[0];
      own.permissions = permissions; own.last_activity = active ? 800 : 0;
      own.extra.room.post_quota_used = 9; own.extra.room.poll_quota_used = 11;
      const ClientInfo other = mesh.acl.clients[1];
      const uint8_t role = permissions & PERM_ACL_ROLE_MASK;
      const bool allowed = role == PERM_ACL_READ_ONLY || role == PERM_ACL_READ_WRITE
          || role == PERM_ACL_ADMIN || (role == PERM_ACL_GUEST && active);
      mesh.command("room.catchup keep 1");
      assert(mesh.general_commands == 0 && own.permissions == permissions && !own.permissions_are_explicit);
      assert(memcmp(&mesh.acl.clients[1], &other, sizeof(other)) == 0);
      assert(own.extra.room.post_quota_used == 9 && own.extra.room.poll_quota_used == 11);
      assert(own.extra.room.sync_since == (allowed ? 300U : 50U));
      assert(mesh.acl.saves == (allowed && role == PERM_ACL_ADMIN ? 1U : 0U));
      if (allowed) assert(mesh.reply() == "OK - skipped=2 unread=1 since=300");
      else if (role == PERM_ACL_GUEST) assert(mesh.sent.empty() && own.last_activity == 0);
      else assert(mesh.reply().find("Err") != std::string::npos);
    }
  }
  MyMesh mesh; backlog(mesh);
  auto& own = mesh.acl.clients[0]; own.extra.room.pending_ack = 0x778899;
  own.extra.room.push_post_timestamp = 100; own.extra.room.ack_timeout = 5000;
  mesh.command("  AB|GET room.catchup ", 100);
  assert(mesh.reply() == "AB|> since=50 unread=3");
  mesh.command("AB|room.catchup before 1970-01-01T00:05:00Z", 101);
  assert(mesh.reply() == "AB|OK - skipped=1 unread=2 since=100");
  assert(own.extra.room.pending_ack == 0 && mesh.message_cancellations == 1
         && mesh.cancelled_timestamp == 100);
  mesh.command("room.catchup before 4294967295", 102);
  assert(own.extra.room.sync_since == 500 && mesh.reply() == "OK - skipped=2 unread=0 since=500");
  assert(mesh.message_cancellations == 1); // No repeated cancel after the post was cleared.
  MyMesh evicted; backlog(evicted); evicted.acl.clients[0].extra.room.pending_ack = 55;
  evicted.acl.clients[0].extra.room.push_post_timestamp = 75; // The retained posts no longer include this one.
  evicted.command("room.catchup keep 1");
  assert(evicted.message_cancellations == 1 && evicted.cancelled_timestamp == 75);
  MyMesh topic; backlog(topic); topic.acl.clients[0].extra.room.pending_ack = 55;
  topic.acl.clients[0].extra.room.push_post_timestamp = 100;
  topic.acl.clients[0].extra.room.pending_topic_revision = 22;
  topic.command("room.catchup keep 1");
  assert(topic.message_cancellations == 0 && topic.acl.clients[0].extra.room.pending_ack == 55
         && topic.acl.clients[0].extra.room.pending_topic_revision == 22);
  puts("actual radio catch-up keeps newest, skips before UTC dates, and only changes own client passed");
}

static void catchupRetriesAndDenials() {
  MyMesh mesh; backlog(mesh); auto& own = mesh.acl.clients[0]; own.permissions_are_explicit = true;
  mesh.command("AB|room.catchup keep 1", 100, 0, 777);
  assert(own.extra.room.sync_since == 300 && mesh.acl.saves == 1);
  const std::string first_reply = mesh.reply();
  mesh.command("room.catchup keep 0", 101);
  assert(own.extra.room.sync_since == 500 && mesh.acl.saves == 2);
  mesh.command("AB|room.catchup keep 1", 102, 0, 777);
  assert(own.extra.room.sync_since == 500 && mesh.acl.saves == 2 && mesh.reply() == first_reply);
  const size_t replies = mesh.sent.size();
  mesh.command("room.catchup keep 0", 99);
  mesh.command("room.catchup keep 2", 102);
  assert(mesh.sent.size() == replies && own.extra.room.sync_since == 500);
  own.permissions = PERM_ACL_FILTER_MGR;
  mesh.command("AB|room.catchup keep 1", 103, 0, 777);
  assert(mesh.reply().find("Err") != std::string::npos && own.extra.room.sync_since == 500);
  const char* rejected[] = {"room.catchupx keep 0", "room.catchup", "get room.catchup extra",
    "room.catchup keep -1", "room.catchup keep 4294967296", "room.catchup keep 1;erase",
    "room.catchup before 2026-02-29", "room.catchup before 2106-02-08", "room.catchup set 0",
    "room.catchup skip 1", "AA|BB|room.catchup keep 0", "get room.catchup;erase"};
  for (const char* text : rejected) {
    MyMesh rejected; backlog(rejected); rejected.command(text);
    assert(rejected.acl.clients[0].extra.room.sync_since == 50 && rejected.general_commands == 0);
    assert(rejected.reply().find("Err") != std::string::npos && rejected.acl.saves == 0);
  }
  MyMesh plain; backlog(plain); plain.command("room.catchup keep 0", 100, 0, 0, TXT_TYPE_PLAIN);
  assert(plain.post_count == 1 && plain.acl.clients[0].extra.room.sync_since == 50);
  MyMesh failed; backlog(failed); failed.acl.clients[0].permissions_are_explicit = true;
  failed.acl.clients[0].extra.room.pending_ack = 0x778899;
  failed.acl.clients[0].extra.room.push_post_timestamp = 100;
  failed.acl.clients[0].extra.room.ack_timeout = 5000;
  const auto before = failed.acl.clients[0].extra.room;
  failed.acl.save_ok = false; failed.command("room.catchup keep 1");
  assert(memcmp(&before, &failed.acl.clients[0].extra.room, sizeof(before)) == 0);
  assert(failed.reply().find("save failed") != std::string::npos && failed.message_cancellations == 0);
  puts("catch-up cache executes once, role revocation and invalid families deny, and storage failure rolls back passed");
}

static void adminClients() {
  const std::string target_key(64, '2');
  // The fixture identity is repeated byte0x02, which encodes as repeated "02".
  std::string full_key;
  for (unsigned byte = 0; byte < PUB_KEY_SIZE; ++byte) full_key += "02";
  for (uint8_t target_role : {uint8_t(PERM_ACL_GUEST), uint8_t(PERM_ACL_FILTER_MGR), uint8_t(PERM_ACL_ADMIN)}) {
    MyMesh mesh; mesh.acl.clients[0].permissions = PERM_ACL_ADMIN;
    mesh.acl.clients[1].permissions = target_role;
    const auto permissions = mesh.acl.clients[1].permissions;
    mesh.command(("room.user " + full_key + " outpath C1,C2").c_str());
    assert(mesh.general_commands == 1 && mesh.acl.clients[1].out_path_len == 2);
    assert(mesh.acl.clients[1].out_path[0] == 0xC1 && mesh.acl.clients[0].out_path[0] == 0xA1);
    assert(mesh.acl.clients[1].permissions == permissions && !mesh.acl.clients[1].permissions_are_explicit);
    assert(mesh.acl.saves == (target_role == PERM_ACL_ADMIN ? 1U : 0U));
  }
  for (const std::string& key : {full_key.substr(0, 8), full_key.substr(0, 12), target_key,
                                full_key.substr(0, 62) + "GG", full_key + "00"}) {
    MyMesh mesh; mesh.acl.clients[0].permissions = PERM_ACL_ADMIN;
    mesh.command(("room.user " + key + " outpath C1").c_str());
    assert(mesh.acl.clients[1].out_path[0] == 0xA2 && mesh.reply().find("Err") != std::string::npos);
  }
  MyMesh admin; backlog(admin); admin.acl.clients[0].permissions = PERM_ACL_ADMIN;
  admin.acl.clients[1].extra.room.sync_since = 50;
  // Other authors leave all five retained posts unread to the target.
  for (auto& post : admin.posts) if (post.post_timestamp) memset(post.author.pub_key, 3, PUB_KEY_SIZE);
  admin.command(("room.user " + full_key + " keep 2").c_str());
  assert(admin.acl.clients[1].extra.room.sync_since == 300 && admin.acl.clients[0].extra.room.sync_since == 50);
  MyMesh user; user.command(("room.user " + full_key + " outpath C1").c_str());
  assert(user.general_commands == 0 && user.acl.clients[1].out_path[0] == 0xA2);
  puts("admin targets full identities without role promotion while ordinary users cannot edit another client passed");
}
static void catchupPolls() {
  MyMesh mesh; backlog(mesh); mesh.command("room.catchup keep 0");
  auto& own = mesh.acl.clients[0]; assert(own.extra.room.sync_since == 500);
  const size_t replies = mesh.sent.size();
  mesh.poll(101, 300);
  assert(own.extra.room.sync_since == 500 && own.last_timestamp == 101);
  assert(mesh.sent.size() == replies + 1 && mesh.sent.back().packet.getPayloadType() == PAYLOAD_TYPE_ACK);
  mesh.poll(102, 700);
  assert(own.extra.room.sync_since == 700 && mesh.sent.size() == replies + 2);
  mesh.poll(101, 300); // Exact poll retry returns an ACK without rewinding.
  assert(own.extra.room.sync_since == 700 && own.last_timestamp == 102 && mesh.sent.size() == replies + 3);
  mesh.poll(103, 0);
  assert(own.extra.room.sync_since == 700 && mesh.sent.size() == replies + 4);
  puts("normal keep-alive echoes preserve selected catch-up while forward cursors and exact ACK retries work passed");
}

static std::string mailKey(const MyMesh& mesh, unsigned index) {
  char key[65]; mesh::room_mail_protocol_detail::hex(mesh.acl.clients[index].id.pub_key, key); return key;
}
static mesh::RoomMailStatus mailStatus(MyMesh& mesh, unsigned index, bool provisioned = true) {
  metadata_filesystem = &mesh.policy_fs; mesh::RoomMailStatus status;
  const auto result = mesh::getRoomMailStatus(&mesh.policy_fs, mesh.acl.clients[index].id.pub_key, status);
  assert(result == (provisioned ? mesh::RoomMailResult::Success : mesh::RoomMailResult::NotFound));
  return status;
}
static uint32_t mailId(const std::string& reply) {
  unsigned long id = 0; assert(sscanf(reply.c_str(), "OK id=%lu", &id) == 1 && id <= UINT32_MAX && id);
  return uint32_t(id);
}
static void mailPlain(MyMesh& mesh, const std::string& command, uint32_t timestamp, unsigned sender = 0) {
  const size_t before = mesh.sent.size();
  mesh.command(command.c_str(), timestamp, sender, 0, TXT_TYPE_PLAIN);
  if (mesh.sent.size() > before && mesh.sent.back().packet.getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
    assert(mesh.sent.back().packet.payload[4] >> 2 == TXT_TYPE_PLAIN);
    assert(memcmp(mesh.sent.back().recipient, mesh.acl.clients[sender].id.pub_key, PUB_KEY_SIZE) == 0);
  }
}
static void mailPlainCommands() {
  MyMesh mesh; auto& reader = mesh.acl.clients[0]; reader.permissions = PERM_ACL_READ_ONLY;
  assert(mesh.roomClientChatEnabled(&reader));
  mailPlain(mesh, "!mail mode public", 100); assert(mesh.reply() == "OK");
  assert(mesh.sent.size() == 2 && mesh.sent[0].packet.getPayloadType() == PAYLOAD_TYPE_ACK);
  mailPlain(mesh, "!mail delivery mailbox", 101);
  assert(mailStatus(mesh, 0).settings.mailbox_only && !mesh.roomClientChatEnabled(&reader));
  assert(mesh.roomClientChatEnabled(&mesh.acl.clients[1]));
  mailPlain(mesh, "!mail settings", 102); assert(mesh.reply().find("delivery=mailbox") != std::string::npos);
  mailPlain(mesh, "!mail bogus", 103); assert(mesh.reply().find("Error ") == 0);
  mailPlain(mesh, "!mail", 104); assert(mesh.reply().find("Error ") == 0);
  mailPlain(mesh, "!mail read 0", 105); assert(mesh.reply().find("Error ") == 0);
  mesh.command("AB|mail delivery chat", 106);
  assert(mesh.reply() == "AB|OK" && mesh.sent.back().packet.payload[4] >> 2 == TXT_TYPE_CLI_DATA);
  assert(!mailStatus(mesh, 0).settings.mailbox_only && mesh.roomClientChatEnabled(&reader));
  assert(mesh.post_count == 0 && mesh.general_commands == 0 && mesh.acl.saves == 0);
  assert(reader.permissions == PERM_ACL_READ_ONLY && mesh.acl.clients[1].permissions == PERM_ACL_READ_WRITE);
  MyMesh ordinary;
  ordinary.command("dog lost: please call home", 100, 0, 0, TXT_TYPE_PLAIN);
  ordinary.command("!mailbox is just chat", 101, 0, 0, TXT_TYPE_PLAIN);
  assert(ordinary.post_count == 2 && ordinary.general_commands == 0);
  // A saved mailbox-only preference survives cache replacement and blocks
  // public unread counts without discarding retained chat.
  mailPlain(mesh, "!mail delivery mailbox", 107);
  mesh.remote_cli_reply_cache.clear(); assert(!mesh.roomClientChatEnabled(&reader));
  backlog(mesh); assert(mesh.getUnsyncedCount(&reader) == 0);
  assert(mesh.posts[0].post_timestamp != 0);
  auto corrupt = mesh.policy_fs.get(mesh::ROOM_MAIL_PRIMARY_PATH); corrupt[0] ^= 1;
  mesh.policy_fs.put(mesh::ROOM_MAIL_PRIMARY_PATH, corrupt);
  assert(!mesh.roomClientChatEnabled(&reader)); // Corrupt settings fail closed.
  puts("plain !mail commands privately reply, reserve unknown commands, preserve owner policy and mailbox-only chat admission passed");
}
static void mailOwnershipAndDog() {
  MyMesh mesh; mesh.acl.clients[0].permissions = PERM_ACL_READ_ONLY;
  mailPlain(mesh, "!mail mode public", 100);
  mailPlain(mesh, "!mail delivery mailbox", 101);
  const std::string dog = "dog lost: please call home; set password is ordinary text";
  mailPlain(mesh, "!mail send " + mailKey(mesh, 0) + " " + dog, 100, 1);
  const uint32_t id = mailId(mesh.reply()); assert(mailStatus(mesh, 0).count == 1);
  assert(mesh.post_count == 0 && mesh.general_commands == 0);
  mailPlain(mesh, "!mail inbox", 102); assert(mesh.reply().find(std::to_string(id) + "/") != std::string::npos);
  // A sleeping radio checks later; reading retains the item until explicit ACK.
  mesh.clock.now += 1800;
  mailPlain(mesh, "!mail read " + std::to_string(id), 103);
  assert(mesh.reply().find("text=" + dog) != std::string::npos && mailStatus(mesh, 0).count == 1);
  mailPlain(mesh, "!mail read " + std::to_string(id), 101, 1);
  assert(mesh.reply().find(dog) == std::string::npos && mesh.reply() == "Error not found");
  mailPlain(mesh, "!mail ack " + std::to_string(id), 102, 1);
  assert(mesh.reply() == "Error not found" && mailStatus(mesh, 0).count == 1);
  mailPlain(mesh, "!mail delete " + std::to_string(id), 103, 1);
  assert(mesh.reply() == "Error not found" && mailStatus(mesh, 0).count == 1);
  mailPlain(mesh, "!mail mode closed", 104);
  mailPlain(mesh, "!mail ack " + std::to_string(id), 105);
  assert(mailStatus(mesh, 0).count == 0);
  mailPlain(mesh, "!mail ack " + std::to_string(id), 105); assert(mailStatus(mesh, 0).count == 0);
  mailPlain(mesh, "!mail delete " + std::to_string(id), 106); assert(mesh.reply().find("OK id=") == 0);
  mailPlain(mesh, "!mail read " + std::to_string(id), 107); assert(mesh.reply() == "Error not found");
  assert(mesh.post_count == 0 && mesh.general_commands == 0 && mesh.acl.saves == 0);
  // Even administrators use their authenticated own inbox in these commands.
  mesh.acl.clients[1].permissions = PERM_ACL_ADMIN;
  mailPlain(mesh, "!mail settings", 104, 1); assert(mesh.reply().find("mode=closed") != std::string::npos);
  assert(mailStatus(mesh, 1, false).count == 0);
  puts("later dog-lost check and owner-only inbox read ACK delete keep bodies private and treat command-looking body as text passed");
}
static void mailModesRolesAndBans() {
  MyMesh policy; policy.acl.clients[0].permissions = PERM_ACL_READ_ONLY;
  mailPlain(policy, "!mail mode private", 100);
  mailPlain(policy, "!mail send " + mailKey(policy, 0) + " forbidden", 100, 1);
  assert(policy.reply() == "Error permission denied");
  mailPlain(policy, "!mail allow " + mailKey(policy, 1), 101);
  mailPlain(policy, "!mail send " + mailKey(policy, 0) + " allowed", 101, 1);
  assert(policy.reply().find("OK id=") == 0 && mailStatus(policy, 0).count == 1);
  mailPlain(policy, "!mail deny " + mailKey(policy, 1), 102);
  mailPlain(policy, "!mail send " + mailKey(policy, 0) + " denied", 102, 1);
  assert(policy.reply() == "Error permission denied" && mailStatus(policy, 0).count == 1);
  mailPlain(policy, "!mail mode public", 103);
  mailPlain(policy, "!mail send " + mailKey(policy, 0) + " public", 103, 1);
  assert(policy.reply().find("OK id=") == 0 && mailStatus(policy, 0).count == 2);
  mailPlain(policy, "!mail mode closed", 104);
  mailPlain(policy, "!mail send " + mailKey(policy, 0) + " closed", 104, 1);
  assert(policy.reply() == "Error permission denied" && mailStatus(policy, 0).count == 2);
  for (uint8_t role : {uint8_t(PERM_ACL_GUEST), uint8_t(PERM_ACL_READ_ONLY), uint8_t(PERM_ACL_READ_WRITE),
      uint8_t(PERM_ACL_ADMIN), uint8_t(PERM_ACL_FILTER_MGR), uint8_t(PERM_ACL_REGION_MGR)}) {
    for (bool active : {false, true}) {
      MyMesh mesh; mesh.acl.clients[0].permissions = role; mesh.acl.clients[0].last_activity = active ? 900 : 0;
      mailPlain(mesh, "!mail mode public", 100, 1); mesh.sent.clear();
      mailPlain(mesh, "!mail send " + mailKey(mesh, 1) + " role test", 100);
      const bool writer = role == PERM_ACL_READ_WRITE || role == PERM_ACL_ADMIN;
      assert(mailStatus(mesh, 1).count == (writer ? 1U : 0U));
      assert(mesh.post_count == 0 && mesh.general_commands == 0);
      mailPlain(mesh, "!mail mode private", 101);
      const bool own = writer || role == PERM_ACL_READ_ONLY || (role == PERM_ACL_GUEST && active);
      if (own) assert(mailStatus(mesh, 0).settings.mode == mesh::RoomMailMode::Private);
      else assert(mailStatus(mesh, 0, false).revision == 0);
    }
  }
  MyMesh blocked; mailPlain(blocked, "!mail mode public", 100, 1);
  assert(blocked.room_access.addBan(&blocked.policy_fs, blocked.acl.clients[1].id.pub_key)
      == mesh::RoomAccessPolicy::BanResult::Saved);
  mailPlain(blocked, "!mail send  " + mailKey(blocked, 1) + " banned recipient", 100);
  assert(blocked.reply().find("recipient is blocked") != std::string::npos && mailStatus(blocked, 1).count == 0);
  assert(blocked.room_access.addBan(&blocked.policy_fs, blocked.acl.clients[0].id.pub_key)
      == mesh::RoomAccessPolicy::BanResult::Saved);
  const size_t replies = blocked.sent.size(); mailPlain(blocked, "!mail settings", 101);
  assert(blocked.sent.size() == replies && blocked.post_count == 0 && blocked.general_commands == 0);
  puts("private allow deny public closed policies honor writer reader guest roles and sender recipient bans passed");
}
static void mailTransportRetries() {
  MyMesh mesh; mailPlain(mesh, "!mail mode public", 100);
  const std::string submission = "!mail send " + mailKey(mesh, 0) + " dog lost";
  mailPlain(mesh, submission, 100, 1); const uint32_t id = mailId(mesh.reply()); const std::string original = mesh.reply();
  mailPlain(mesh, "!mail settings", 101, 1);
  mailPlain(mesh, submission, 100, 1);
  assert(mesh.reply() == original && mailStatus(mesh, 0).count == 1 && mesh.acl.clients[1].last_timestamp == 101);
  const size_t replies = mesh.sent.size(); mailPlain(mesh, submission + " changed", 100, 1);
  assert(mesh.sent.size() == replies && mailStatus(mesh, 0).count == 1);
  mesh.acl.clients[1].permissions = PERM_ACL_READ_ONLY;
  mailPlain(mesh, submission, 100, 1); assert(mesh.reply() == original && mailStatus(mesh, 0).count == 1);
  mailPlain(mesh, submission + " new", 102, 1);
  assert(mesh.reply() == "Error permission denied" && mailStatus(mesh, 0).count == 1);
  // Restore the same persisted files with a new transport cache and replay floor.
  MyMesh reboot;
  for (const auto& entry : mesh.policy_fs.files)
    reboot.policy_fs.put(entry.first.c_str(), *entry.second);
  mailPlain(reboot, submission, 100, 1);
  assert(mailId(reboot.reply()) == id && reboot.reply().find("duplicate") != std::string::npos);
  assert(mailStatus(reboot, 0).count == 1);
  mailPlain(reboot, "!mail read " + std::to_string(id), 100);
  assert(reboot.reply().find("text=dog lost") != std::string::npos);
  reboot.acl.clients[0].permissions = PERM_ACL_FILTER_MGR;
  const size_t before_revoke = reboot.sent.size(); mailPlain(reboot, "!mail read " + std::to_string(id), 100);
  assert(reboot.sent.size() == before_revoke || reboot.reply().find("dog lost") == std::string::npos);
  reboot.acl.clients[0].permissions = PERM_ACL_READ_ONLY;
  mailPlain(reboot, "!mail ack " + std::to_string(id), 101); assert(mailStatus(reboot, 0).count == 0);
  reboot.remote_cli_reply_cache.clear(); reboot.acl.clients[1].last_timestamp = 99;
  mailPlain(reboot, submission, 100, 1); assert(mailId(reboot.reply()) == id && mailStatus(reboot, 0).count == 0);
  assert(reboot.room_access.addBan(&reboot.policy_fs, reboot.acl.clients[1].id.pub_key)
      == mesh::RoomAccessPolicy::BanResult::Saved);
  const size_t before_ban = reboot.sent.size(); mailPlain(reboot, submission, 100, 1);
  assert(reboot.sent.size() == before_ban && reboot.post_count == 0 && reboot.general_commands == 0);
  puts("plain transport retries persist once across cache reboot ACK and role or ban changes without exposing revoked mail passed");
}
static void mailInputLengths() {
  MyMesh mesh; mailPlain(mesh, "!mail mode public", 100);
  const std::string prefix = "mail send " + mailKey(mesh, 0) + " ";
  const std::string longest = prefix + std::string(mesh::RemoteCliReplyCache::MAX_REPLY_TEXT - prefix.size(), 'x');
  char reply[180]{};
  assert(mesh.handleRoomMailClientCommand(&mesh.acl.clients[1], longest.c_str(), 800, reply, sizeof(reply)));
  const uint32_t id = mailId(reply); assert(mailStatus(mesh, 0).count == 1);
  mesh::RoomMailMessage message; uint8_t bytes[128]; size_t copied = 0;
  assert(mesh::readRoomMail(&mesh.policy_fs, mesh.acl.clients[0].id.pub_key, id, 0,
      bytes, sizeof(bytes), copied, message) == mesh::RoomMailResult::Success);
  assert(message.length == longest.size() - prefix.size() && copied == message.length);
  assert(std::string(reinterpret_cast<const char*>(bytes), copied) == longest.substr(prefix.size()));
  for (const std::string& input : {longest + "x", prefix + std::string(200, 'y'), prefix + std::string(512, 'z')}) {
    memset(reply, 0, sizeof(reply));
    assert(mesh.handleRoomMailClientCommand(&mesh.acl.clients[1], input.c_str(), 801, reply, sizeof(reply)));
    assert(strstr(reply, "Error command too long") && mailStatus(mesh, 0).count == 1);
  }
  mailPlain(mesh, "!" + longest + "x", 100, 1);
  assert(mesh.reply() == "Error command too long" && mailStatus(mesh, 0).count == 1);
  assert(mesh.post_count == 0 && mesh.general_commands == 0);
  puts("mail transport rejects oversized commands without truncating bodies while exact buffer boundary preserves complete text passed");
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "roles")) roleCases();
  else if (!strcmp(argv[1], "allowlist")) exactAllowlist();
  else if (!strcmp(argv[1], "formats")) pathFormats();
  else if (!strcmp(argv[1], "persistence")) persistenceCases();
  else if (!strcmp(argv[1], "retries")) retryCases();
  else if (!strcmp(argv[1], "alternate")) alternateCases();
  else if (!strcmp(argv[1], "push")) pushCases();
  else if (!strcmp(argv[1], "catchup")) catchupRadio();
  else if (!strcmp(argv[1], "catchup-retry")) catchupRetriesAndDenials();
  else if (!strcmp(argv[1], "admin-clients")) adminClients();
  else if (!strcmp(argv[1], "catchup-poll")) catchupPolls();
  else if (!strcmp(argv[1], "mail-plain")) mailPlainCommands();
  else if (!strcmp(argv[1], "mail-owner")) mailOwnershipAndDog();
  else if (!strcmp(argv[1], "mail-policies")) mailModesRolesAndBans();
  else if (!strcmp(argv[1], "mail-retries")) mailTransportRetries();
  else if (!strcmp(argv[1], "mail-lengths")) mailInputLengths();
  else assert(false);
}
'''


def production_source():
    room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
    header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
    acl = (ROOT / "src/helpers/ClientACL.h").read_text()
    packet = (ROOT / "src/Packet.cpp").read_text()
    constants = "\n".join(line for line in acl.splitlines()
                          if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
    sources = [room, header, (ROOT / "src/Mesh.h").read_text(),
               (ROOT / "src/helpers/TxtDataHelpers.h").read_text()]
    for name in ("ROOM_MESSAGE_CACHE_SIZE", "TXT_TYPE_PLAIN", "TXT_TYPE_CLI_DATA", "TXT_TYPE_CLI_COMMAND",
                 "TXT_TYPE_SIGNED_PLAIN", "TXT_ACK_DELAY", "REPLY_DELAY_MILLIS", "REQ_TYPE_KEEP_ALIVE",
                 "SERVER_RESPONSE_DELAY", "LAZY_CONTACTS_WRITE_DELAY", "PUSH_TIMEOUT_BASE",
                 "PUSH_ACK_TIMEOUT_FACTOR", "PUSH_ACK_TIMEOUT_FLOOD", "MAX_UNSYNCED_POSTS", "MAX_POST_TEXT_LEN"):
        found = None
        for text in sources:
            found = re.search(r"^\s*#define\s+" + name + r"\s+.*$", text, re.MULTILINE)
            if found:
                break
        if not found:
            raise AssertionError("missing production constant " + name)
        constants += "\n" + found[0]
    methods = "\n".join(extract_braced(room, signature) for signature in (
        "bool MyMesh::roomClientChatEnabled(", "bool MyMesh::handleRoomMailClientCommand(",
        "bool MyMesh::handleRoomMailText(",
        "bool MyMesh::handleClientPathCommand(", "bool MyMesh::executeClientPathCommand(",
        "bool MyMesh::sendClientReply(",
        "bool MyMesh::handleRoomCatchUpCommand(", "bool MyMesh::applyRoomCatchUpCommand(",
        "bool MyMesh::setRoomClientPath(", "bool MyMesh::handleRoomManagementCommand(",
        "uint8_t MyMesh::getUnsyncedCount(",
        "void MyMesh::onPeerDataRecv(", "bool MyMesh::pushRoomTextToClient(",
        "void MyMesh::serviceRoomQuotas(", "bool MyMesh::saveFilter("))
    replacements = {
        "@CONSTANTS@": constants,
        "@STR_HELPER@": extract_braced((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(),
                                       "void StrHelper::strncpy("),
        "@CLIENT_INFO@": extract_braced(acl, "struct ClientInfo {"),
        "@POST_INFO@": extract_braced(header, "struct PostInfo {"),
        "@PACKET_METHODS@": "\n".join(extract_braced(packet, signature) for signature in (
            "Packet::Packet()", "bool Packet::isValidPathLen(", "uint8_t Packet::copyPath(",
            "size_t Packet::writePath(")),
        "@PRODUCTION_METHODS@": methods,
        "@TEST_CASES@": CASES,
    }
    generated = FIXTURE.read_text()
    for marker, text in replacements.items():
        generated = generated.replace(marker, text)
    return generated


class RoomClientPathTests(unittest.TestCase):
    @classmethod
    def build(cls, generated, name):
        work = Path(cls.directory.name)
        source = work / (name + ".cpp")
        source.write_text(generated, encoding="ascii")
        binary = work / name
        command = [cls.compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-I" + str(ROOT / "test/fixtures/room_history_store"), "-I" + str(ROOT / "src"),
                   "-I" + str(ROOT / "test/mocks"),
                   str(source), "-o", str(binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        checked = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if checked.returncode:
            raise AssertionError(checked.stdout + checked.stderr)
        return binary

    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise AssertionError("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="room-client-paths-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.generated = production_source()
        cls.binary = cls.build(cls.generated, "room-paths")

    def run_case(self, name, expected):
        checked = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=20)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_all_roles_only_change_the_authenticated_clients_routes(self):
        self.run_case("roles", "256 permission combinations and live reader roles remain self-only passed")

    def test_narrow_allowlist_prefixes_posts_bans_and_command_bypass_attempts(self):
        self.run_case("allowlist", "exact allowlist, correlation normalization, chat isolation, and ban gates passed")

    def test_path_widths_observed_routes_and_bounded_formatting(self):
        self.run_case("formats", "one-to-three byte paths, route sentinels, observed paths, and bounded formatting passed")

    def test_transient_sessions_filtered_storage_noop_and_rollback(self):
        self.run_case("persistence", "transient roles stay transient, filtered durability, no-op writes, and save rollback passed")

    def test_replayed_mutations_cached_results_identity_and_revoked_permissions(self):
        self.run_case("retries", "cached setters execute once, sender isolation, replay denial, and demotion gates passed")

    def test_alternate_packet_clones_primary_retries_and_allocation_failures(self):
        self.run_case("alternate", "alternate encrypted copies retain primary retries and tolerate admission or pool failure passed")

    def test_actual_post_and_topic_pushes_share_ack_and_retry_state(self):
        self.run_case("push", "actual room post and topic pushes share copies, ACK state, and primary retry ownership passed")

    def test_actual_radio_catchup_frames_are_own_only_and_keep_newest(self):
        self.run_case("catchup", "actual radio catch-up keeps newest, skips before UTC dates, and only changes own client passed")

    def test_catchup_correlated_retries_revoked_roles_invalid_commands_and_save_failure(self):
        self.run_case("catchup-retry", "catch-up cache executes once, role revocation and invalid families deny, and storage failure rolls back passed")

    def test_admin_full_identity_routes_and_catchup_without_promoting_target_role(self):
        self.run_case("admin-clients", "admin targets full identities without role promotion while ordinary users cannot edit another client passed")

    def test_actual_general_cli_dispatches_management_after_remote_admin_admission(self):
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        command = extract_braced(source, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        self.assertIn("if (handleRoomManagementCommand(command, reply)) return;", command)
        receive = extract_braced(source, "void MyMesh::onPeerDataRecv(")
        admin = extract_braced(receive, "if (client->isAdmin()) {")
        self.assertIn("handleCommand(sender_timestamp,", admin)

    def test_actual_keepalive_echo_cannot_undo_skip_and_exact_retries_still_ack(self):
        self.run_case("catchup-poll", "normal keep-alive echoes preserve selected catch-up while forward cursors and exact ACK retries work passed")

    def test_actual_plain_mail_commands_are_reserved_private_and_save_delivery_policy(self):
        self.run_case("mail-plain", "plain !mail commands privately reply, reserve unknown commands, preserve owner policy and mailbox-only chat admission passed")

    def test_actual_owner_only_mail_poll_read_and_durable_ack_for_later_dog_check(self):
        self.run_case("mail-owner", "later dog-lost check and owner-only inbox read ACK delete keep bodies private and treat command-looking body as text passed")

    def test_actual_mail_sender_modes_reader_writer_roles_and_bans(self):
        self.run_case("mail-policies", "private allow deny public closed policies honor writer reader guest roles and sender recipient bans passed")

    def test_actual_plain_mail_retries_reboot_receipts_and_current_authorization(self):
        self.run_case("mail-retries", "plain transport retries persist once across cache reboot ACK and role or ban changes without exposing revoked mail passed")

    def test_actual_mail_input_length_rejection_preserves_complete_accepted_body(self):
        self.run_case("mail-lengths", "mail transport rejects oversized commands without truncating bodies while exact buffer boundary preserves complete text passed")

    def test_negative_controls_detect_rollback_cache_authorization_and_retry_regressions(self):
        controls = (
            ("rollback", "memcpy(selected, previous, sizeof(previous));",
             "(void)previous;", "persistence"),
            ("cache-permission", "if (cached_retry && !client->isAdmin() && !own_path_allowed) {",
             "if (false && cached_retry && !client->isAdmin() && !own_path_allowed) {", "retries"),
            ("alternate-retries", "_prefs.direct_retry_enabled = 0;",
             "_prefs.direct_retry_enabled = 1;", "alternate"),
            ("poll-rewind", "if (forceSince > client->extra.room.sync_since) {",
             "if (forceSince > 0) {", "catchup-poll"),
        )
        for name, before, after, case in controls:
            with self.subTest(control=name):
                self.assertEqual(self.generated.count(before), 1,
                                 "update the negative control to the production seam")
                binary = self.build(self.generated.replace(before, after), "negative-" + name)
                checked = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(checked.returncode, 0,
                                    "negative control did not detect " + name)
                self.assertIn("Assertion", checked.stderr, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
