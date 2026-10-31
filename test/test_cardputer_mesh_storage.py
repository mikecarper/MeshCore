#!/usr/bin/env python3
"""Exercise Cardputer packet ownership, loop dispatch and optional SD history."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"

FILESYSTEM_STUB = r'''
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <limits>
#define ESP32_PLATFORM 1
#define PUB_KEY_SIZE 32
#define FILE_READ "r"
#define FILE_WRITE "w"
#define FILE_APPEND "a"
namespace mesh { struct RTCClock {}; }
struct Entry {
  std::string bytes;
  bool no_progress = false;
  bool corrupt_read = false;
  unsigned reads = 0;
};
struct File {
  std::shared_ptr<Entry> entry;
  size_t position = 0;
  size_t max_write = std::numeric_limits<size_t>::max();
  explicit operator bool() const { return bool(entry); }
  size_t size() const { return entry ? entry->bytes.size() : 0; }
  size_t available() const { return size() - position; }
  bool isDirectory() const { return false; }
  bool setBufferSize(size_t) { return true; }
  void flush() {}
  size_t read(uint8_t* dest, size_t count) {
    if (!entry) return 0;
    if (++entry->reads > 100) throw std::runtime_error("read made no progress");
    if (entry->no_progress) return 0;
    count = std::min(count, available());
    memcpy(dest, entry->bytes.data() + position, count);
    if (entry->corrupt_read && count) dest[0] ^= 1;
    position += count;
    return count;
  }
  size_t write(const uint8_t* src, size_t count) {
    count = std::min(count, max_write);
    entry->bytes.append(reinterpret_cast<const char*>(src), count);
    return count;
  }
  void print(const char* text) { entry->bytes += text; }
  void println(const char* text) { entry->bytes += std::string(text) + "\n"; }
  void close() { entry.reset(); }
};
struct FILESYSTEM {
  std::map<std::string, std::shared_ptr<Entry>> files;
  unsigned opens = 0;
  bool fail_open = false;
  bool fail_stat = false;
  size_t max_write = std::numeric_limits<size_t>::max();
  unsigned rename_calls = 0, fail_rename = 0;
  bool corrupt_public_read = false;
  bool exists(const char* path) const { return files.count(path) != 0; }
  bool mkdir(const char*) { return true; }
  File open(const char* path, const char* mode, bool = false) {
    ++opens;
    if (fail_open) return {};
    if (*mode == 'r' && !files.count(path)) return {};
    auto& entry = files[path];
    if (!entry) entry = std::make_shared<Entry>();
    if (*mode == 'w') entry->bytes.clear();
    return File{entry, 0, max_write};
  }
  bool remove(const char* path) { return files.erase(path) != 0; }
  bool rename(const char* from, const char* to) {
    if (++rename_calls == fail_rename || !files.count(from) || files.count(to)) return false;
    files[to] = files.at(from);
    files.erase(from);
    if (corrupt_public_read && std::string(to) == "/meshcore_custom/prefs") {
      files[to]->corrupt_read = true;
      corrupt_public_read = false;
    }
    return true;
  }
};
static FILESYSTEM* stat_fs = nullptr;
struct DataStore { DataStore(FILESYSTEM&, mesh::RTCClock&) {} };
'''

STORAGE_CHECKS = r'''
#include <cassert>
#include <cstdio>
int main() {
  mesh::RTCClock clock;
  FILESYSTEM primary, sd;
  stat_fs = &primary;
  CardputerDataStore store(primary, clock);
  uint8_t key[PUB_KEY_SIZE] = {};
  ChatHistory history;
  history.push_ref()->out = true;
  store.beginCustom(nullptr);
  assert(!store.hasHistory());
  store.storeMessage(key, "without SD", false, false);
  store.loadMessages(key, false, history);
  assert(history.count() == 0 && primary.opens == 0);

  CustomNodePrefs prefs{1.1f, 127, 2};
  store.beginCustom(&sd);
  assert(store.saveCustomPrefs(prefs));
  assert(primary.files.count(CUSTOM_PREFS_FILE) == 1 && sd.files.empty());
  CustomNodePrefs restored{};
  store.loadCustomPrefs(restored);
  assert(!memcmp(&prefs, &restored, sizeof(prefs)));

  // A bounded source need not have a terminator after its last allowed byte.
  char* bounded = new char[MAX_MESSAGE_LENGTH];
  memset(bounded, 'x', MAX_MESSAGE_LENGTH);
  store.storeMessage(key, bounded, false, true);
  delete[] bounded;
  const std::string channel_path = std::string(CHANNEL_HISTORY_ID_DIR) + "/" + std::string(64, '0');
  assert(sd.files.at(channel_path)->bytes == "<< " + std::string(MAX_MESSAGE_LENGTH, 'x') + "\n");
  store.storeMessage(key, "line1\r\nline2", true, true);
  assert(sd.files.at(channel_path)->bytes.find(">> line1  line2\n") != std::string::npos);

  // Direct and channel namespaces remain separate; loading retains the last 15.
  for (unsigned i = 0; i < CHAT_HISTORY_RINGBUF_SIZE + 5; ++i) {
    char text[32];
    snprintf(text, sizeof(text), "message %u", i);
    store.storeMessage(key, text, i % 2, false);
  }
  store.loadMessages(key, false, history);
  assert(history.count() == CHAT_HISTORY_RINGBUF_SIZE);
  for (unsigned i = 0; i < history.count(); ++i) {
    const HistoryMessage* msg = nullptr;
    assert(history.get(i, msg));
    char expected[32];
    snprintf(expected, sizeof(expected), "message %u", i + 5);
    assert(!strcmp(msg->text, expected) && msg->out == bool((i + 5) % 2));
  }
  const HistoryMessage* absent = nullptr;
  assert(!history.get(history.count(), absent));
  assert(primary.files.size() == 1 && sd.files.size() == 2);

  const std::string direct_path = std::string(HISTORY_DIR_DIRECT) + "/" + std::string(64, '0');
  auto direct = sd.files.at(direct_path);
  direct->no_progress = true;
  direct->reads = 0;
  store.loadMessages(key, false, history);
  assert(direct->reads == 1 && history.count() == 0);
  sd.fail_open = true;
  store.storeMessage(key, "write failure", true, false);
  store.loadMessages(key, false, history);
  assert(history.count() == 0);

  // Received messages persist v2 metadata; own messages persist text only.
  sd.fail_open = false;
  uint8_t key2[PUB_KEY_SIZE] = {1};
  MessageMeta meta;
  meta.has = true;
  meta.sent_ts = 5;
  meta.recv_ts = 6;
  meta.hops = 2;
  meta.snr_q4 = -8;
  meta.rssi = -90;
  meta.path_len = 2;
  meta.path[0] = 0xC5;
  meta.path[1] = 0xD8;
  store.storeMessage(key2, "{brace} rx", false, false, &meta);
  store.storeMessage(key2, "own", true, false, &meta);
  const std::string v2_path = std::string(HISTORY_DIR_DIRECT) + "/01" + std::string(62, '0');
  assert(sd.files.at(v2_path)->bytes == "<< {m1;5;6;2;-8;-90;1;C5D8} {brace} rx\n>> own\n");
  sd.files.at(v2_path)->bytes += "<< {legacy brace}\n";
  store.loadMessages(key2, false, history);
  assert(history.count() == 3);
  const HistoryMessage* rx = nullptr;
  assert(history.get(0, rx) && !rx->out && rx->meta.has && rx->meta.hops == 2 && rx->meta.rssi == -90);
  assert(rx->meta.path_len == 2 && rx->meta.path[1] == 0xD8 && !strcmp(rx->text, "{brace} rx"));
  assert(history.get(1, rx) && rx->out && !rx->meta.has && !strcmp(rx->text, "own"));
  assert(history.get(2, rx) && !rx->out && !rx->meta.has && !strcmp(rx->text, "{legacy brace}"));
}
'''

MESH_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define MAX_HASH_SIZE 8
#define MAX_MESSAGE_LENGTH 150
#define PAYLOAD_TYPE_GRP_TXT 5
namespace mesh {
struct GroupChannel { uint8_t secret[32] = {}; };
struct Packet {
  uint8_t type, value;
  uint8_t getPayloadType() const { return type; }
  void calculatePacketHash(uint8_t* hash) const { memset(hash, value, MAX_HASH_SIZE); }
};
}
struct ChannelDetails { mesh::GroupChannel channel; char name[32] = "channel"; };
struct Store {
  unsigned writes = 0;
  void storeMessage(const uint8_t*, const char*, bool, bool) { ++writes; }
};
struct MyMesh {
  bool admit = true;
  unsigned base_loops = 0;
  uint32_t now = 0;
  bool sendFloodScoped(const mesh::GroupChannel&, mesh::Packet* pkt, uint32_t) {
    if (!admit || !pkt) { delete pkt; return false; }
    return true;
  }
  bool getChannel(int index, ChannelDetails& channel) {
    assert(index == 7);
    channel.channel.secret[31] = 99;
    return true;
  }
  bool sendUiChannelMessage(int index, uint32_t, const char*, size_t) { assert(index == 7); return admit; }
  void loop() { ++base_loops; }
  bool millisHasNowPassed(unsigned long at) const { return int32_t(now - at) > 0; }
};
struct Clock { uint32_t getMillis() const { return 500; } };
struct Tracker {
  uint32_t next = 0;
  unsigned channel_adds = 0;
  uint32_t add(const uint8_t*, bool is_channel, uint32_t) { channel_adds += is_channel; return ++next; }
};
struct CardputerMesh : MyMesh {
  Store store;
  Store* _store = &store;
  Clock clock;
  Clock* _ms = &clock;
  Tracker own_tracker;
  uint32_t pending_channel_own_id = 0;
  uint8_t last_message_hash[MAX_HASH_SIZE] = {};
  uint16_t last_message_heard_repeats = 9;
  unsigned long resend_timeout = 0;
  unsigned resends = 0;
  bool sendFloodScoped(const mesh::GroupChannel&, mesh::Packet*, uint32_t);
  bool sendChannelMessage(int, uint32_t, const char*, size_t);
  void checkResend() { ++resends; }
  void loop();
};
@METHODS@
int main() {
  CardputerMesh node;
  mesh::GroupChannel channel;
  node.admit = false;
  assert(!node.sendFloodScoped(channel, new mesh::Packet{PAYLOAD_TYPE_GRP_TXT, 42}, 0));
  assert(node.last_message_hash[0] == 0 && node.last_message_heard_repeats == 9);
  assert(!node.sendChannelMessage(7, 0, "text", 4) && node.store.writes == 0);
  assert(node.own_tracker.channel_adds == 0 && node.pending_channel_own_id == 0);
  node.admit = true;
  auto* group = new mesh::Packet{PAYLOAD_TYPE_GRP_TXT, 77};
  assert(node.sendFloodScoped(channel, group, 0));
  for (uint8_t byte : node.last_message_hash) assert(byte == 77);
  assert(node.last_message_heard_repeats == 0);
  assert(node.own_tracker.channel_adds == 1 && node.pending_channel_own_id == 1);
  delete group;
  node.last_message_heard_repeats = 3;
  auto* other = new mesh::Packet{1, 66};
  assert(node.sendFloodScoped(channel, other, 0));
  assert(node.last_message_hash[0] == 77 && node.last_message_heard_repeats == 3);
  assert(node.own_tracker.channel_adds == 1);
  delete other;
  assert(node.sendChannelMessage(7, 0, "text", 4) && node.store.writes == 0);
  node.resend_timeout = 10;
  node.now = 9;
  node.loop();
  assert(node.base_loops == 1 && node.resends == 0);
  node.now = 11;
  node.loop();
  assert(node.base_loops == 2 && node.resends == 1 && node.resend_timeout == 0);
  node.loop();
  assert(node.base_loops == 3 && node.resends == 1);
}
'''

ACK_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define PUB_KEY_SIZE 32
enum MessageSendState { MESSAGE_SENDING, MESSAGE_DELIVERED, MESSAGE_FAILED };
struct ContactInfo { struct { uint8_t pub_key[PUB_KEY_SIZE] = {}; } id; };
struct MyMesh {
  unsigned base_calls = 0;
  bool base_owned = false;
  ContactInfo base_peer;
  bool processAck(const uint8_t*, ContactInfo*& peer) {
    ++base_calls;
    peer = base_owned ? &base_peer : nullptr;
    return base_owned;
  }
};
struct UI {
  unsigned notifications = 0;
  void onMessageSendAttempt(uint8_t, uint8_t, MessageSendState state) {
    assert(state == MESSAGE_DELIVERED);
    ++notifications;
  }
};
struct CardputerMesh : MyMesh {
  struct {
    MessageSendState state = MESSAGE_SENDING;
    uint32_t expected_ack = 123;
    uint8_t attempt = 1, total_attempts = 3;
    ContactInfo recipient;
  } last_msg;
  UI ui;
  UI* _ui = &ui;
  struct Clock { uint32_t getMillis() const { return 900; } } clock;
  Clock* _ms = &clock;
  struct Tracker {
    unsigned acks = 0;
    uint32_t last_id = 0;
    void onAck(uint32_t own_id, uint32_t now) { ++acks; last_id = own_id; assert(now == 900); }
  } own_tracker;
  uint32_t last_dm_own_id = 7;
  unsigned long resend_timeout = 100;
  ContactInfo accepted_peer;
  bool contact_present = true;
  unsigned lookups = 0;
  ContactInfo* lookupContactByPubKey(const uint8_t* key, size_t len) {
    ++lookups;
    assert(len == PUB_KEY_SIZE);
    assert(!memcmp(key, last_msg.recipient.id.pub_key, PUB_KEY_SIZE));
    return contact_present ? &accepted_peer : nullptr;
  }
  bool processAck(const uint8_t*, ContactInfo*&);
};
@METHOD@
int main() {
  CardputerMesh node;
  uint32_t ack = 123;
  ContactInfo* peer = nullptr;
  assert(node.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(node.base_calls == 1 && node.lookups == 1 && peer == &node.accepted_peer);
  assert(node.last_msg.state == MESSAGE_DELIVERED && node.last_msg.expected_ack == 0);
  assert(node.resend_timeout == 0 && node.ui.notifications == 1);
  assert(node.own_tracker.acks == 1 && node.own_tracker.last_id == 7);
  assert(!node.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(node.base_calls == 2 && node.ui.notifications == 1 && peer == nullptr);
  assert(node.own_tracker.acks == 1);

  CardputerMesh unrelated;
  ack = 456;
  assert(!unrelated.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(unrelated.resend_timeout == 100 && unrelated.ui.notifications == 0 && unrelated.lookups == 0);
  unrelated.base_owned = true;
  assert(unrelated.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(peer == &unrelated.base_peer && unrelated.base_calls == 2);
  assert(unrelated.last_msg.state == MESSAGE_SENDING && unrelated.resend_timeout == 100);
  assert(unrelated.own_tracker.acks == 0);

  CardputerMesh shared;
  shared.base_owned = true;
  ack = 123;
  assert(shared.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(peer == &shared.base_peer && shared.lookups == 0 && shared.ui.notifications == 1);

  CardputerMesh removed;
  removed.contact_present = false;
  assert(removed.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(peer == nullptr && removed.lookups == 1 && removed.resend_timeout == 0);

  CardputerMesh no_ack;
  no_ack.last_msg.expected_ack = 0;
  ack = 0;
  assert(!no_ack.processAck(reinterpret_cast<const uint8_t*>(&ack), peer));
  assert(no_ack.last_msg.state == MESSAGE_SENDING && no_ack.resend_timeout == 100);
  assert(no_ack.ui.notifications == 0 && no_ack.lookups == 0);
}
'''


class CardputerMeshStorageTests(unittest.TestCase):
    def run_cpp(self, source, *, headers=None, flags=(), fails=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for name, content in (headers or {}).items():
                (work / name).parent.mkdir(parents=True, exist_ok=True)
                (work / name).write_text(content, encoding="ascii")
            program = work / "test.cpp"
            program.write_text(source, encoding="ascii")
            executable = work / "test"
            built = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                "-I", str(work), "-I", str(VARIANT), *flags, str(program), "-o", str(executable),
            ], text=True, capture_output=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1")
            result = subprocess.run([str(executable)], text=True, capture_output=True, env=env, timeout=10)
            if fails:
                self.assertNotEqual(result.returncode, 0, "negative control unexpectedly passed")
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def storage_source(self):
        source = (VARIANT / "CardputerDataStore.cpp").read_text()
        return source + STORAGE_CHECKS

    def mesh_source(self):
        source = (VARIANT / "companion_radio/CardputerMesh.cpp").read_text()
        methods = "\n".join(body(source, signature) for signature in (
            "bool CardputerMesh::sendFloodScoped(", "bool CardputerMesh::sendChannelMessage(",
            "void CardputerMesh::loop()"))
        return MESH_HARNESS.replace("@METHODS@", methods)

    def test_history_storage_and_faults(self):
        self.run_cpp(self.storage_source(), headers=self.storage_headers())

    def test_custom_preferences_verified_rollback_and_recovery(self):
        checks = r'''
#include <cassert>
#include <limits>
int main() {
  mesh::RTCClock clock;
  const CustomNodePrefs previous{1.0f, 128, 1}, candidate{1.2f, 240, 2};
  for (unsigned fault = 0; fault < 6; ++fault) {
    FILESYSTEM fs; stat_fs = &fs;
    CardputerDataStore store(fs, clock);
    assert(store.saveCustomPrefs(previous));
    fs.rename_calls = 0;
    if (fault == 0) fs.max_write = 3;
    if (fault == 1) fs.fail_open = true;
    if (fault == 2) fs.fail_rename = 1;
    if (fault == 3) fs.fail_rename = 2;
    if (fault == 4) fs.corrupt_public_read = true;
    if (fault == 5) fs.fail_stat = true;
    assert(!store.saveCustomPrefs(candidate));
    fs.max_write = 100; fs.fail_open = false; fs.fail_rename = 0; fs.fail_stat = false;
    CustomNodePrefs restored{};
    CardputerDataStore rebooted(fs, clock);
    assert(rebooted.loadCustomPrefs(restored));
    assert(!memcmp(&restored, &previous, sizeof(previous)));
    assert(!fs.exists(CUSTOM_PREFS_FILE ".tmp"));
  }
  FILESYSTEM fs; stat_fs = &fs;
  CardputerDataStore store(fs, clock);
  assert(store.saveCustomPrefs(previous));
  assert(fs.rename(CUSTOM_PREFS_FILE, CUSTOM_PREFS_FILE ".bak"));
  auto temp = fs.open(CUSTOM_PREFS_FILE ".tmp", FILE_WRITE);
  temp.write(reinterpret_cast<const uint8_t*>(&candidate), sizeof(candidate)); temp.close();
  CustomNodePrefs restored{};
  assert(store.loadCustomPrefs(restored));
  assert(!memcmp(&restored, &previous, sizeof(previous)));
  assert(fs.exists(CUSTOM_PREFS_FILE) && !fs.exists(CUSTOM_PREFS_FILE ".bak"));
  for (CustomNodePrefs invalid : {CustomNodePrefs{0.49f, 1, 1}, CustomNodePrefs{2.01f, 1, 1},
       CustomNodePrefs{1.0f, 1, 3}, CustomNodePrefs{std::numeric_limits<float>::quiet_NaN(), 1, 1}}) {
    assert(!store.saveCustomPrefs(invalid));
  }
  fs.files.at(CUSTOM_PREFS_FILE)->bytes.resize(3);
  restored = candidate;
  assert(!store.loadCustomPrefs(restored));
  assert(!memcmp(&restored, &candidate, sizeof(candidate)));
  assert(!store.saveCustomPrefs(previous));
}
'''
        self.run_cpp((VARIANT / "CardputerDataStore.cpp").read_text() + checks,
                     headers=self.storage_headers())

    def test_channel_identity_storage_preserves_ambiguous_legacy_logs(self):
        checks = r'''
#include <cassert>
int main() {
  mesh::RTCClock clock;
  FILESYSTEM primary, sd; stat_fs = &primary;
  CardputerDataStore store(primary, clock); store.beginCustom(&sd);
  const std::string legacy_path = std::string(HISTORY_DIR_CHANNEL) + "/" + std::string(32,'0');
  auto legacy = sd.open(legacy_path.c_str(), FILE_WRITE);
  legacy.println("<< ambiguous old conversation"); legacy.close();
  const std::string previous = sd.files.at(legacy_path)->bytes;
  uint8_t first[32] = {}, second[32] = {}; first[31] = 1; second[31] = 2;
  ChatHistory history;
  store.loadMessages(first, true, history);
  assert(history.count() == 0);
  store.storeMessage(first, "first identity", false, true);
  store.storeMessage(second, "second identity", true, true);
  store.loadMessages(first, true, history);
  const HistoryMessage* message = nullptr;
  assert(history.count() == 1 && history.get(0,message));
  assert(!strcmp(message->text,"first identity") && !message->out);
  store.loadMessages(second, true, history);
  assert(history.count() == 1 && history.get(0,message));
  assert(!strcmp(message->text,"second identity") && message->out);
  assert(sd.files.size() == 3 && sd.files.at(legacy_path)->bytes == previous);
  for (const auto& entry : sd.files) {
    if (entry.first == legacy_path) continue;
    assert(entry.first.find(std::string(HISTORY_DIR) + "/chan2/") == 0);
    assert(entry.first.substr(entry.first.find_last_of('/')+1).size() == 64);
  }
}
'''
        self.run_cpp((VARIANT / "CardputerDataStore.cpp").read_text() + checks,
                     headers=self.storage_headers())

    def storage_headers(self):
        # Production includes SD.h for PlatformIO's variant-local dependency
        # discovery. The filesystem stub supplies every API this source uses;
        # no concrete SD driver is needed by the native ownership/fault tests.
        return {"DataStore.h": FILESYSTEM_STUB, "SD.h": "#pragma once\n",
                "helpers/ContactFileTransaction.h": (ROOT / "src/helpers/ContactFileTransaction.h").read_text(),
                "helpers/IdentityStore.h": "#pragma once\n",
                "helpers/PersistentStoreFormat.h": (ROOT / "src/helpers/PersistentStoreFormat.h").read_text(),
                "sys/stat.h": r'''
#pragma once
#include <cerrno>
struct stat {};
inline int stat(const char* path, struct stat*) {
  if (!stat_fs || stat_fs->fail_stat) { errno = EIO; return -1; }
  if (stat_fs->exists(path + 7)) return 0;
  errno = ENOENT;
  return -1;
}
'''}

    def test_history_source_bound_negative_control(self):
        # Widen the source bound handed to the line codec past the caller's buffer.
        source = self.storage_source().replace(
            "text, MAX_MESSAGE_LENGTH, line, sizeof(line)",
            "text, sizeof(line), line, sizeof(line)")
        self.assertNotEqual(source, self.storage_source())
        self.run_cpp(source, headers=self.storage_headers(), fails=True)

    def test_sd_no_progress_negative_control(self):
        source = self.storage_source().replace("    if (bytesRead <= 0) {\n      break;\n    }\n", "")
        self.assertNotEqual(source, self.storage_source())
        self.run_cpp(source, headers=self.storage_headers(), fails=True)

    def test_group_admission_and_packet_ownership(self):
        self.run_cpp(self.mesh_source())

    def test_cardputer_ack_ownership_preserves_base_processing(self):
        source = (VARIANT / "companion_radio/CardputerMesh.cpp").read_text()
        self.run_cpp(ACK_HARNESS.replace("@METHOD@", body(source, "bool CardputerMesh::processAck(")))

    def test_packet_ownership_negative_control(self):
        source = self.mesh_source().replace(
            "  if (sent && is_group_text) {",
            "  if (pkt->getPayloadType() == PAYLOAD_TYPE_GRP_TXT) {")
        self.assertNotEqual(source, self.mesh_source())
        self.run_cpp(source, fails=True)

    def test_main_loop_dispatch(self):
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        dispatch = re.search(
            r"#if CARDPUTER_ADV_UI\n  the_mesh_cp\.loop\(\);\n#else\n  the_mesh\.loop\(\);\n#endif",
            source)
        self.assertIsNotNone(dispatch)
        harness = r'''
#include <cassert>
struct MyMesh { unsigned base_calls = 0; void loop() { ++base_calls; } };
struct CardputerMesh : MyMesh { unsigned custom_calls = 0; void loop() { MyMesh::loop(); ++custom_calls; } };
CardputerMesh the_mesh_cp;
MyMesh& the_mesh = the_mesh_cp;
int main() {
@DISPATCH@
  assert(the_mesh.base_calls == 1);
  assert(the_mesh_cp.custom_calls == CARDPUTER_ADV_UI);
}
'''.replace("@DISPATCH@", dispatch.group())
        for enabled in (0, 1):
            with self.subTest(cardputer=enabled):
                self.run_cpp(harness, flags=(f"-DCARDPUTER_ADV_UI={enabled}",))


if __name__ == "__main__":
    unittest.main()
