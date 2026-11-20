#!/usr/bin/env python3
"""Exercise the real qualified RTC post bank, post/history methods and lifetime."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_room_history_store import STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]


class RoomRtcPostsTests(unittest.TestCase):
    def sources(self):
        source = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
        start = source.index("#if ROOM_POSTS_RTC_SLOW_STORAGE\n#include <esp_attr.h>")
        bank = source[start:source.index("MyMesh::MyMesh(", start)]
        constructor = extract_braced(source, "MyMesh::MyMesh(")
        initializer = re.search(r"posts\(acquireRoomRtcPosts\(this\)\)", constructor)[0]
        clear = re.search(r"memset\(posts, 0, sizeof\(posts\)\);", constructor)[0]
        destructor = extract_braced(source, "MyMesh::~MyMesh()")
        post = extract_braced(header, "struct PostInfo") + ";\n"
        return source, header, bank, initializer, clear, destructor, post

    def compile(self, directory, source, *, ndebug=False, name="posts"):
        directory = Path(directory)
        (directory / "esp_attr.h").write_text('#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow.0")))\n')
        (directory / "sdkconfig.h").write_text("#define CONFIG_IDF_TARGET_ESP32 1\n")
        path = directory / (name + ".cpp")
        path.write_text(source)
        binary = directory / name
        flags = ["-DNDEBUG"] if ndebug else []
        sanitized = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        built = subprocess.run([shutil.which("g++") or "clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-class-memaccess", *sanitized, *flags,
                                "-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DESP32_PLATFORM=1",
                                "-DMESH_ENABLE_FLOOD_PROFILE_POLICY=0",
                                "-I" + str(directory), "-I" + str(ROOT / "src"),
                                "-I" + str(ROOT / "test/mocks"), str(path), "-o", str(binary)],
                               text=True, capture_output=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return binary

    def minimal_fixture(self):
        _, _, bank, initializer, clear, destructor, post = self.sources()
        _, header, _, _, _, _, _ = self.sources()
        deleted = "\n".join(line for line in header.splitlines() if "MyMesh" in line and "= delete;" in line)
        return r'''
#include <type_traits>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#define MAX_UNSYNCED_POSTS 32
#define MAX_POST_TEXT_LEN 151
#define CHECK(value) do { if (!(value)) abort(); } while (0)
namespace mesh { struct Identity { uint8_t pub_key[32]; Identity() { memset(pub_key,0,32); } }; }
''' + post + bank + r'''
struct MyMesh {
  PostInfo (&posts)[MAX_UNSYNCED_POSTS];
  MyMesh() : ''' + initializer + " { " + clear + r''' }
  ~MyMesh();
''' + deleted + r'''
};
static_assert(!std::is_copy_constructible<MyMesh>::value && !std::is_move_constructible<MyMesh>::value, "RTC owner cannot be copied or moved");
''' + destructor + r'''
int main(int argc,char** argv) {
  int mode=argc>1 ? atoi(argv[1]) : 0;
  if(mode==1) { MyMesh first; MyMesh second; return 1; }
  if(mode==2) { MyMesh first; releaseRoomRtcPosts(&mode); return 1; }
  if(mode==3) { acquireRoomRtcPosts(nullptr); return 1; }
  PostInfo* address=nullptr;
  {
    MyMesh first; address=first.posts;
    CHECK(sizeof(first.posts)==6016);
    for(size_t i=0;i<sizeof(first.posts);++i) CHECK(reinterpret_cast<uint8_t*>(first.posts)[i]==0);
    for(unsigned i=0;i<32;++i) {
      memset(first.posts[i].author.pub_key,int(i+1),32);
      first.posts[i].post_timestamp=0xF0000000+i;
      memset(first.posts[i].text,int('A'+i%26),151);first.posts[i].text[151]=0;
    }
    for(unsigned i=0;i<32;++i) {
      CHECK(first.posts[i].author.pub_key[31]==i+1);
      CHECK(first.posts[i].post_timestamp==0xF0000000+i);
      CHECK(strlen(first.posts[i].text)==151);
    }
  }
  { MyMesh second; CHECK(second.posts==address);
    for(size_t i=0;i<sizeof(second.posts);++i) CHECK(reinterpret_cast<uint8_t*>(second.posts)[i]==0); }
  CHECK(room_rtc_posts_owner==nullptr);
  puts("RTC post ownership, complete capacity and boot reset passed");
}
'''

    def test_actual_bank_ownership_capacity_and_reset_with_ndebug(self):
        with tempfile.TemporaryDirectory(prefix="room-rtc-owner-") as directory:
            binary = self.compile(directory, self.minimal_fixture(), ndebug=True)
            okay = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(okay.returncode, 0, okay.stdout + okay.stderr)
            for mode in (1, 2, 3):
                with self.subTest(mode=mode):
                    failed = subprocess.run([str(binary), str(mode)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(failed.returncode, -6, failed.stdout + failed.stderr)

    def test_physical_bank_size_and_ordinary_disabled_source(self):
        with tempfile.TemporaryDirectory(prefix="room-rtc-section-") as directory:
            binary = self.compile(directory, self.minimal_fixture())
            sections = subprocess.check_output(["readelf", "-SW", str(binary)], text=True)
            section = next(line for line in sections.splitlines() if ".rtc.force_slow.0" in line)
            self.assertRegex(section, r"\b001780\b")  # All 32 complete 188-byte records.
            _, _, bank, _, _, _, _ = self.sources()
            # The default path must not include any SDK headers, owner or bank.
            source = Path(directory) / "disabled.cpp"
            source.write_text(bank + "\nint main() {}\n")
            output = subprocess.run([shutil.which("g++") or "clang++", "-E", "-P", "-x", "c++", str(source)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(output.stdout.strip(), "int main() {}")

    def test_storage_flag_rejects_unqualified_boards_and_invalid_values(self):
        _, header, _, _, _, _, _ = self.sources()
        start = header.index("#ifndef ROOM_POSTS_RTC_SLOW_STORAGE")
        policy = header[start:header.index("struct PostInfo", start)]
        cases = (([], True), (["-DROOM_POSTS_RTC_SLOW_STORAGE=0"], True),
                 (["-DROOM_POSTS_RTC_SLOW_STORAGE=2"], False),
                 (["-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DNRF52_PLATFORM=1"], False),
                 (["-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DESP32_PLATFORM=1"], False),
                 (["-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DESP32_PLATFORM=1", "-DLILYGO_TLORA=1",
                   "-DMESHCORE_EXPANDED_PARTITION_PROFILE=1"], True),
                 *((["-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DESP32_PLATFORM=1", "-D" + board + "=1",
                     "-DMESHCORE_EXPANDED_PARTITION_PROFILE=1"], True)
                   for board in ("TBEAM_SX1262", "TBEAM_SX1276")),
                 *((["-DROOM_POSTS_RTC_SLOW_STORAGE=1", "-DESP32_PLATFORM=1", "-D" + board + "=1"], False)
                   for board in ("LILYGO_TLORA", "TBEAM_SX1262", "TBEAM_SX1276")))
        for flags, valid in cases:
            with self.subTest(flags=flags):
                result = subprocess.run([shutil.which("g++") or "clang++", "-std=c++17", "-x", "c++",
                                         "-fsyntax-only", *flags, "-"], input=policy + "\nint main() {}\n",
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_only_qualified_full_room_recipes_select_complete_storage_policy(self):
        text = (ROOT / "build_legacy.sh").read_text()
        functions = []
        for name in ("apply_esp32_full_size_profile", "apply_esp32_iram_advert_storage_profile"):
            start = text.index(name + "() {")
            functions.append(text[start:text.index("\n}\n", start) + 2])
        targets = {
            "Tbeam_SX1262_room_server_observer_mqtt": True,
            "Tbeam_SX1276_room_server_observer_mqtt": True,
            "LilyGo_TLora_V2_1_1_6_room_server_observer_mqtt_": True,
            "Tbeam_SX1262_room_server": False,
            "Tbeam_SX1276_room_server": False,
            "Tbeam_SX1262_repeater_observer_mqtt": False,
            "Tbeam_SX1276_companion_radio_ble": False,
            "T_Beam_S3_Supreme_SX1262_room_server_observer_mqtt": False,
            "RAK_4631_room_server": False,
        }
        for full in ("0", "1"):
            for supported in ("0", "1"):
                for target, selected in targets.items():
                    with self.subTest(full=full, supported=supported, target=target):
                        code = r'''
ESP32_FULL_BUILD="$1"
QUALIFIED="$2"
ESP32_FULL_MAX_NEIGHBOURS=254
DRAM_LIMITED_MAX_NEIGHBOURS=50
PLATFORMIO_BUILD_FLAGS="-DEXISTING=1"
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
supports_esp32_full_build() { [ "$QUALIFIED" = 1 ]; }
is_room_server_role_target() { [[ "$1" == *room_server* ]]; }
requires_dram_limited_neighbors() { [[ "$1" == Tbeam_SX12* ]]; }
requires_esp32_companion_full_ota_fallback() { return 1; }
append_platformio_build_unflags() { :; }
record_build_reduction() { :; }
''' + "\n".join(functions) + r'''
apply_esp32_full_size_profile "$3"
apply_esp32_iram_advert_storage_profile "$3"
printf "%s" "$PLATFORMIO_BUILD_FLAGS"
'''
                        result = subprocess.run(["bash", "-c", code, "bash", full, supported, target],
                                                capture_output=True, text=True, check=True, timeout=10)
                        enabled = full == "1" and supported == "1" and selected
                        flags = result.stdout.split()
                        for name in ("ROOM_POSTS_RTC_SLOW_STORAGE", "OTA_BOUNDED_SOURCE_PROOFS"):
                            self.assertEqual(flags.count("-D" + name + "=1"), int(enabled), result.stdout)
                        if enabled:
                            self.assertIn("-DFLOOD_ADVERT_IRAM_WORD_STORAGE=1", flags)
                            self.assertNotIn("-DFLOOD_PACKET_FILTER_SLOTS=31", flags)
                            self.assertIn("-DMESH_ENABLE_ROOM_FLOOD_RULE_ENGINE=1", flags)
                            self.assertIn("-DMAX_NEIGHBOURS=" + ("50" if target.startswith("Tbeam_") else "254"), flags)
                        self.assertNotIn("-DMAX_UNSYNCED_POSTS", result.stdout)
                        self.assertNotIn("-DFLOOD_ADVERT_SOURCE_SLOTS", result.stdout)

    def test_real_post_fifo_history_reload_maximum_text_and_catchup(self):
        source, header, bank, initializer, clear, destructor, post = self.sources()
        fixture = (ROOT / "test/fixtures/room_history_integration/test.cpp").read_text().split("int main()", 1)[0]
        # Keep the actual persistence/admission methods and existing FS fault
        # boundary, but simulate boots sequentially because this bank has one owner.
        fixture = fixture[:fixture.index("static void compareBoot(")]
        fixture = fixture.replace("struct Identity { uint8_t pub_key[32] = {}; };", "struct Identity { uint8_t pub_key[32] = {}; bool matches(const Identity& other) const { return !memcmp(pub_key,other.pub_key,32); } };")
        fixture = fixture.replace("struct MyMesh {", bank + "\nstruct MyMesh {")
        fixture = fixture.replace("  PostInfo posts[MAX_UNSYNCED_POSTS] = {};", "  PostInfo (&posts)[MAX_UNSYNCED_POSTS];")
        fixture = fixture.replace("explicit MyMesh(MemoryFS* fs) : _fs(fs) {", "explicit MyMesh(MemoryFS* fs) : _fs(fs), " + initializer + " { " + clear)
        fixture = fixture.replace("  Clock* getRTCClock()", "  ~MyMesh();\n  Clock* getRTCClock()")
        fixture = fixture.replace('#include "../room_history_store/filesystem.h"', '#include "' + str(ROOT / 'test/fixtures/room_history_store/filesystem.h') + '"')
        fixture += destructor + r'''
#include <helpers/RoomCatchUp.h>
struct SyncClient {
  mesh::Identity id;
  struct { struct { uint32_t sync_since=0,pending_ack=0,pending_topic_revision=0,push_post_timestamp=0; } room; } extra;
};
int main() {
  MemoryFS fs; std::vector<std::string> expected;
  for(int utf8=0;utf8<2;++utf8) {
    std::string full(151,'A');
    if(utf8) { full="x";for(int i=0;i<50;++i)full+="\xe2\x98\x83"; }
    {
      MyMesh m(&fs);m.loadRoomHistory();
      assert(m.command("room.history.clear").find("cleared")!=std::string::npos);
      assert(m.command("set room.history on").find("enabled")!=std::string::npos);
      assert(full.size()==151 && m.addSystemPost(full.c_str()));
      assert(m.retained().back()==full && m.posts[0].text[151]==0);
      assert(!m.addSystemPost((full+"!").c_str()));
      for(int i=0;i<70;++i) assert(m.addSystemPost(("post-"+std::to_string(i)).c_str()));
      assert(m.retained().size()==32 && m.retained().front()=="post-38");
      assert(m.post_ready_mask==0 && m.snapshotRoomHistory());
      expected=m.retained();
      PostInfo ordinary[32];memcpy(ordinary,m.posts,sizeof(ordinary));
      SyncClient client;
      for(auto mode : {mesh::RoomCatchUpMode::Before,mesh::RoomCatchUpMode::SkipNext,mesh::RoomCatchUpMode::KeepNewest})
        for(uint32_t value : {0u,1u,16u,31u,32u,100u,UINT32_MAX}) {
          auto rtc=mesh::planRoomCatchUp(client,m.posts,32,mode,value);
          auto old=mesh::planRoomCatchUp(client,ordinary,32,mode,value);
          assert(rtc.candidate_cursor==old.candidate_cursor && rtc.skipped==old.skipped);
          assert(rtc.backlog_remaining==old.backlog_remaining && rtc.pending_post_cancelled==old.pending_post_cancelled);
        }
    }
    {
      MyMesh reboot(&fs);
      assert(reboot.retained().empty()); // Always clear retained RTC bytes first.
      reboot.loadRoomHistory();assert(reboot.retained()==expected);
      uint32_t floor=reboot.last_room_timestamp;
      assert(reboot.addSystemPost(full.c_str()) && reboot.last_room_timestamp>floor);
      assert(reboot.retained().back()==full && reboot.snapshotRoomHistory());
    }
  }
  puts("RTC Room FIFO, maximum text, durable reload and catch-up passed");
}
'''
        acl = (ROOT / "src/helpers/ClientACL.h").read_text()
        state = "\n".join(line for line in acl.splitlines() if line.startswith("#define PERM_ACL_"))
        state += "\n" + post
        definitions = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::addPost(", "bool MyMesh::addSystemPost(", "bool MyMesh::storePost(",
            "bool MyMesh::snapshotRoomHistory(", "void MyMesh::loadRoomHistory(",
            "bool MyMesh::handleRoomHistoryCommand("))
        definitions += "\n" + extract_braced((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(), "void StrHelper::strncpy(")
        gate = extract_braced(extract_braced(source, "void MyMesh::onPeerDataRecv("), "if (flags == TXT_TYPE_PLAIN)")
        with tempfile.TemporaryDirectory(prefix="room-rtc-history-") as directory:
            work = Path(directory);(work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            (work / "state.inc").write_text(state)
            (work / "production.inc").write_text(definitions)
            (work / "post_gate.inc").write_text(gate)
            binary = self.compile(work, fixture)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("durable reload and catch-up passed", ran.stdout)


if __name__ == "__main__":
    unittest.main()
