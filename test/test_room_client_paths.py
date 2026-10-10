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
  assert(chat.posts == 1 && chat.acl.clients[0].out_path[0] == 0xA1);
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
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "roles")) roleCases();
  else if (!strcmp(argv[1], "allowlist")) exactAllowlist();
  else if (!strcmp(argv[1], "formats")) pathFormats();
  else if (!strcmp(argv[1], "persistence")) persistenceCases();
  else if (!strcmp(argv[1], "retries")) retryCases();
  else if (!strcmp(argv[1], "alternate")) alternateCases();
  else if (!strcmp(argv[1], "push")) pushCases();
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
                 "PUSH_ACK_TIMEOUT_FACTOR", "PUSH_ACK_TIMEOUT_FLOOD"):
        found = None
        for text in sources:
            found = re.search(r"^\s*#define\s+" + name + r"\s+.*$", text, re.MULTILINE)
            if found:
                break
        if not found:
            raise AssertionError("missing production constant " + name)
        constants += "\n" + found[0]
    methods = "\n".join(extract_braced(room, signature) for signature in (
        "bool MyMesh::handleClientPathCommand(", "bool MyMesh::sendClientReply(",
        "void MyMesh::onPeerDataRecv(", "bool MyMesh::pushRoomTextToClient(",
        "void MyMesh::serviceRoomQuotas(", "bool MyMesh::saveFilter("))
    replacements = {
        "@CONSTANTS@": constants,
        "@STR_HELPER@": extract_braced((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(),
                                       "void StrHelper::strncpy("),
        "@CLIENT_INFO@": extract_braced(acl, "struct ClientInfo {"),
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

    def test_negative_controls_detect_rollback_cache_authorization_and_retry_regressions(self):
        controls = (
            ("rollback", "memcpy(selected, previous, sizeof(previous));",
             "(void)previous;", "persistence"),
            ("cache-permission", "if (cached_retry && !client->isAdmin() && !own_path_allowed) {",
             "if (false && cached_retry && !client->isAdmin() && !own_path_allowed) {", "retries"),
            ("alternate-retries", "_prefs.direct_retry_enabled = 0;",
             "_prefs.direct_retry_enabled = 1;", "alternate"),
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
