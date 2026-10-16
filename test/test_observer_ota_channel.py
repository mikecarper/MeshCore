"""Exercise the production observer OTA stream, channel commit, and deferred call."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def compile_run(code):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise unittest.SkipTest("a host C++17 compiler is required")
    with tempfile.TemporaryDirectory(prefix="observer-ota-channel-") as directory:
        cpp = Path(directory) / "test.cpp"
        binary = Path(directory) / ("test.exe" if os.name == "nt" else "test")
        cpp.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "src"),
             str(cpp), "-o", str(binary)], text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise AssertionError(f"exit {result.returncode}: {result.stdout}\n{result.stderr}")


class ObserverOtaChannelTest(unittest.TestCase):
    def test_stream_refuses_unsafe_images_before_boot_commit(self):
        board = (ROOT / "src/helpers/ESP32Board.cpp").read_text(encoding="utf-8")
        stream = extract_braced(board, "static bool ota_streamFirmware(")
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/OtaChannel.h>
static uint32_t clock_ms=0, yields=0;
uint32_t millis() { return clock_ms; }
void delay(uint32_t ms) { clock_ms+=ms; ++yields; }
namespace mesh {
struct Logging { void printf(const char*, ...) {} };
Logging& usbLoggingPort() { static Logging value; return value; }
}
static constexpr int U_FLASH=0;
struct UpdateMock {
  bool begin_ok=true, write_ok=true, end_ok=true;
  bool aborted=false, boot_committed=false;
  int end_calls=0;
  std::vector<uint8_t> written;
  bool begin(size_t, int) { return begin_ok; }
  size_t write(const uint8_t* data, size_t size) {
    if (!write_ok) return 0;
    written.insert(written.end(), data, data+size); return size;
  }
  bool end() { ++end_calls; boot_committed=end_ok; return end_ok; }
  void abort() { aborted=true; }
  const char* errorString() { return "injected failure"; }
} Update;
struct Client {
  std::string image;
  size_t pos=0, chunk=1;
  bool stalled=false, disconnect_on_read=false;
  int available() { return stalled ? 1 : int(image.size()-pos); }
  bool connected() { return stalled || pos<image.size(); }
  int read(uint8_t* out, size_t wanted) {
    if (disconnect_on_read) { stalled=false; pos=image.size(); return -1; }
    if (stalled) { clock_ms+=5000; return 0; }
    if (wanted>chunk) wanted=chunk;
    if (wanted>image.size()-pos) wanted=image.size()-pos;
    memcpy(out, image.data()+pos, wanted); pos+=wanted; return int(wanted);
  }
};
@STREAM@
int main() {
  char reply[160]={};
  auto run = [&](std::string image, size_t chunk, bool expect_ok) {
    Update=UpdateMock(); clock_ms=0; yields=0;
    Client client; client.image=std::move(image); client.chunk=chunk;
    const bool ok=ota_streamFirmware(client, client.image.size(), reply);
    assert(ok==expect_ok && Update.boot_committed==expect_ok);
    assert(Update.end_calls==(expect_ok ? 1 : 0));
    assert(expect_ok ? !Update.aborted : Update.aborted);
    return ok;
  };
  const std::string tag(ota_compat_tag, sizeof(ota_compat_tag));
  const std::string literal("ota-compat:\0", 12);
  const std::string compatible=literal+std::string(2111, 'x')+tag+"tail";
  for (size_t chunk : {size_t(1), size_t(7), size_t(63), size_t(64), size_t(2048)})
    run(compatible, chunk, true);
  run(std::string("ota-compat:2\0", 13), 2048, false); // upstream JSON generation
  run("untagged-image", 7, false);
  run(literal+"only-search-literal", 1, false);
  run(tag.substr(0, tag.size()-1), 1, false); // no complete NUL-terminated tag
  run(tag+std::string("ota-compat:2\0", 13), 7, false);
  run(std::string("ota-compat:2\0", 13)+tag, 2048, false);
  run(tag+tag, 3, true); // duplicate identical constants are harmless
  const char future[]="ota-compat:1+keymind1+future";
  run(tag+std::string(future, sizeof(future)), 5, false);
  run(tag+"ota-compat:2+future", 1, false); // pending tag after a good one
  std::string oversized=tag+"ota-compat:1+keymind1+"+std::string(128, 'a');
  oversized.push_back(0);
  run(oversized, 1, false);
  run(std::string("ota-compat:2147483648\0", 22)+tag, 2048, true);

  Update=UpdateMock(); Client incomplete; incomplete.image=tag;
  assert(!ota_streamFirmware(incomplete, tag.size()+1, reply));
  assert(Update.aborted && !Update.boot_committed && Update.end_calls==0);
  Update=UpdateMock(); Client stalled; stalled.stalled=true; clock_ms=0; yields=0;
  assert(!ota_streamFirmware(stalled, tag.size(), reply));
  assert(Update.aborted && Update.end_calls==0 && yields>0);
  assert(strstr(reply, "timeout"));
  Update=UpdateMock(); stalled.stalled=true; stalled.disconnect_on_read=true;
  assert(!ota_streamFirmware(stalled, tag.size(), reply));
  assert(Update.aborted && Update.end_calls==0 && strstr(reply, "incomplete"));
  Update=UpdateMock(); Update.write_ok=false; Client bad_write; bad_write.image=tag;
  assert(!ota_streamFirmware(bad_write, tag.size(), reply));
  assert(Update.aborted && Update.end_calls==0);
  Update=UpdateMock(); Update.end_ok=false; Client bad_end; bad_end.image=tag;
  assert(!ota_streamFirmware(bad_end, tag.size(), reply));
  assert(!Update.boot_committed && Update.end_calls==1);
}
'''.replace("@STREAM@", stream)
        compile_run(code)

    def test_channel_setting_rolls_back_and_deferred_update_keeps_snapshot(self):
        cli = (ROOT / "src/helpers/CommonCLI_Observer.cpp").read_text(encoding="utf-8")
        branch = extract_braced(cli, 'if (memcmp(command, "ota branch", 10) == 0')
        roles = []
        for role in ("simple_repeater", "simple_room_server"):
            header = (ROOT / "examples" / role / "MyMesh.h").read_text(encoding="utf-8")
            method = extract_braced(header, "bool beginDeferredOtaUpdate() override")
            roles.append("struct " + role + " : Deferred { " + method + " };\n")
        code = r'''
#define WITH_MQTT_BRIDGE 1
#define OTA_MANIFEST_BASE "https://native.example/v"
#define OTA_MANIFEST_BASE_STABLE "https://prod.example/v"
#define OTA_MANIFEST_BASE_DEV "https://beta.example/v"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/OtaChannel.h>
static uint32_t now_ms=10;
uint32_t millis() { return now_ms; }
struct Prefs { uint8_t ota_channel=0; };
struct Common {
  Prefs prefs; Prefs* _prefs=&prefs;
  bool save_ok=true; int saves=0; uint8_t persisted=0;
  bool saveCommonPrefs() {
    ++saves; if (!save_ok) return false;
    persisted=_prefs->ota_channel; return true;
  }
  bool run(const char* command, char* reply) { @BRANCH@ return false; }
};
struct Deferred {
  Prefs _prefs;
  unsigned long _ota_update_at=0;
  uint8_t _ota_update_channel=0;
  virtual bool beginDeferredOtaUpdate()=0;
  void otaAlert(const char*) {}
};
@ROLES@
template<typename Role> void check_deferred() {
  Role role; role._prefs.ota_channel=OTA_CH_DEV;
  assert(role.beginDeferredOtaUpdate());
  role._prefs.ota_channel=OTA_CH_STABLE;
  assert(role._ota_update_channel==OTA_CH_DEV);
  assert(role._ota_update_at==2510);
  assert(!strcmp(ota_resolve_base(role._ota_update_channel), "https://beta.example/v"));
}
int main() {
  Common cli; char reply[160]={};
  assert(cli.run("ota branch", reply) && cli.saves==0);
  assert(strstr(reply, "build: custom"));
  assert(cli.run("ota branch beta", reply));
  assert(cli.prefs.ota_channel==OTA_CH_DEV && cli.persisted==OTA_CH_DEV && cli.saves==1);
  cli.save_ok=false;
  assert(cli.run("ota branch prod", reply));
  assert(cli.prefs.ota_channel==OTA_CH_DEV && cli.persisted==OTA_CH_DEV);
  assert(strstr(reply, "ERR: save unconfirmed") && strstr(reply, "reboot may differ"));
  const int saves=cli.saves;
  assert(cli.run("ota branch Beta", reply) && cli.saves==saves);
  assert(!cli.run("ota branches", reply));
  cli.save_ok=true;
  for (const char* choice : {"ota branch prod", "ota branch stable", "ota branch default", "ota branch dev"})
    assert(cli.run(choice, reply) && !strncmp(reply, "channel set", 11));
  check_deferred<simple_repeater>(); check_deferred<simple_room_server>();
}
'''.replace("@BRANCH@", branch).replace("@ROLES@", "\n".join(roles))
        compile_run(code)

    def test_worker_releases_network_pin_after_task_failure_and_completion(self):
        board = (ROOT / "src/helpers/ESP32Board.cpp").read_text(encoding="utf-8")
        args = extract_braced(board, "struct OtaTaskArgs {") + ";"
        entry = extract_braced(board, "static void ota_task_entry(")
        worker = extract_braced(board, "bool ESP32Board::otaFromManifest(")
        code = r'''
#include <cassert>
#include <cstring>
#include <cstdint>
using TaskHandle_t=void*; using BaseType_t=int;
static constexpr BaseType_t pdPASS=1;
static bool spawn_ok=false;
void delay(uint32_t) {}
void vTaskDelete(void*) {}
BaseType_t xTaskCreatePinnedToCore(void (*entry)(void*), const char*, int, void* arg, int,
                                   TaskHandle_t*, int) {
  if (!spawn_ok) return 0;
  entry(arg); return pdPASS;
}
struct NetworkLink {
  int locks=0, releases=0;
  void lockSwitching() { ++locks; }
  void unlockSwitching() { ++releases; }
} network;
NetworkLink& activeNetworkLink() { return network; }
struct ESP32Board {
  bool otaFromManifest(const char*, const char*, bool, char[]);
  bool otaFromManifestImpl(const char* base, const char* version, bool dry, char[]) {
    assert(network.locks==network.releases+1);
    assert(!strcmp(base, "base") && !strcmp(version, "version") && dry);
    return false;
  }
};
@ARGS@
@ENTRY@
@WORKER@
int main() {
  ESP32Board board; char reply[160]={};
  assert(!board.otaFromManifest("base", "version", true, reply));
  assert(network.locks==1 && network.releases==1 && strstr(reply, "spawn failed"));
  spawn_ok=true;
  assert(!board.otaFromManifest("base", "version", true, reply));
  assert(network.locks==2 && network.releases==2);
}
'''.replace("@ARGS@", args).replace("@ENTRY@", entry).replace("@WORKER@", worker)
        compile_run(code)


if __name__ == "__main__":
    unittest.main()
