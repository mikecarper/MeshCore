"""Compile actual ESP-NOW diagnostic dispatch/forwarders with native fakes."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class EspNowDiagnosticCliTests(unittest.TestCase):
    def compile_run(self, source, flags=()):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="espnow-cli-") as directory:
            source_path = Path(directory) / "main.cpp"
            binary = Path(directory) / "check"
            source_path.write_text('#include <helpers/CLICommandUtils.h>\n' + source, encoding="ascii")
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                            "-I", str(ROOT / "src"), *flags, str(source_path),
                            "-o", str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    def test_actual_dispatch_strict_local_actions_and_bounded_replies(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        body = extract_braced(source, "bool CommonCLI::handleEspNowDiagnosticCommand(")
        defaults = "\n".join(extract_braced(header, signature) for signature in (
            "virtual void formatEspNowStatus(", "virtual void formatEspNowStats(",
            "virtual bool sendEspNowTest(", "virtual bool resetEspNowStats("))
        self.assertIn("if (handleEspNowDiagnosticCommand(sender_timestamp, command, reply)) return;", source)
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <helpers/bridges/ESPNowBridgeDiagnostics.h>
static uint32_t now_ms = 0x1234ABCD;
uint32_t millis() { return now_ms; }
struct Callbacks {
  virtual ~Callbacks() = default;
''' + defaults + r'''
};
struct Fake : Callbacks {
  int statuses = 0, stats = 0, sends = 0, resets = 0;
  uint32_t nonce = 0;
  size_t capacity = 0;
  std::string page;
  bool send_result = true, reset_result = true, long_reply = false;
  void fill(char* reply, size_t cap, const char* value) {
    capacity = cap;
    const std::string text = long_reply ? std::string(300, 'x') : value;
    snprintf(reply, cap, "%s", text.c_str());
  }
  void formatEspNowStatus(char* r, size_t cap) override {
    ++statuses; fill(r, cap, "status");
  }
  void formatEspNowStats(const char* p, char* r, size_t cap) override {
    ++stats; page = p; fill(r, cap, "stats");
  }
  bool sendEspNowTest(uint32_t n) override {
    ++sends; nonce = n; return send_result;
  }
  bool resetEspNowStats() override { ++resets; return reset_result; }
  int count() const { return statuses + stats + sends + resets; }
};
struct CommonCLI {
  Callbacks* _callbacks;
  bool handleEspNowDiagnosticCommand(uint32_t, const char*, char*);
};
''' + body + r'''
int main() {
  Fake callbacks;
  CommonCLI cli{&callbacks};
  char reply[160];
  auto command = [&](const char* text, uint32_t sender = 0) {
    memset(reply, 'Z', sizeof(reply));
    const bool handled = cli.handleEspNowDiagnosticCommand(sender, text, reply);
    assert(reply[157] == 'Z' && reply[158] == 'Z' && reply[159] == 'Z');
    if (handled) assert(memchr(reply, 0, 157) != nullptr);
    return handled;
  };
  assert(command("get espnow.status", 77));
  assert(callbacks.statuses == 1 && callbacks.capacity == 157);
  for (const char* page : {"", "mac", "rx", "tx", "drops", "test"}) {
    const std::string text = std::string("get espnow.stats") + (*page ? " " : "") + page;
    assert(command(text.c_str(), 77));
    assert(callbacks.page == page && callbacks.capacity == 157);
  }
  callbacks.long_reply = true;
  assert(command("get espnow.status"));
  assert(strlen(reply) == 156);
  assert(command("get espnow.stats rx"));
  assert(strlen(reply) == 156);
  callbacks.long_reply = false;
  for (const char* text : {
      "get espnow.status extra", "get espnow.statusx", "get espnow.status ",
      "get espnow.statsrx", "get espnow.stats x", "get espnow.stats rx junk",
      "get espnow.statsmac", "get espnow.stats macx", "get espnow.stats mac junk",
      "get espnow.stats mac ", "get espnow.stats\tmac", "get espnow.stats  mac",
      "get espnow.stats ", "get espnow.stats\trx", "get espnow.stats  rx",
      "espnow", "espnow testx", "espnow test ", "espnow test 1234567",
      "espnow test 123456789", "espnow test 0x123456", "espnow test +1234567",
      "espnow test 1234567Z", "espnow test 12345678 ", "espnow test 12345678 x",
      "espnow resetstatsx", "espnow resetstats ", "espnow resetstats now"}) {
    const int before = callbacks.count();
    assert(command(text));
    assert(strncmp(reply, "Error:", 6) == 0 && callbacks.count() == before);
  }
  assert(command("get espnow.stats unknown"));
  assert(strstr(reply, "[mac|rx|tx|drops|test]"));
  for (const char* text : {"espnowtest", "espnowx test", "get espnow.enabled", "get repeat"}) {
    const int before = callbacks.count();
    assert(!command(text));
    assert(callbacks.count() == before);
  }
  for (uint32_t remote : {1U, UINT32_MAX}) {
    for (const char* text : {"espnow test", "espnow test 12345678", "espnow resetstats"}) {
      const int before = callbacks.count();
      assert(command(text, remote));
      assert(strstr(reply, "local connection") && callbacks.count() == before);
    }
  }
  assert(command("espnow test"));
  assert(callbacks.sends == 1 && callbacks.nonce == now_ms);
  assert(strcmp(reply, "OK - test queued id=1234ABCD; confirm on peer") == 0);
  assert(command("espnow test 89abcDEF"));
  assert(callbacks.sends == 2 && callbacks.nonce == 0x89ABCDEF);
  assert(command("espnow test 00000000"));
  assert(callbacks.sends == 3 && callbacks.nonce == 0);
  callbacks.send_result = false;
  assert(command("espnow test DEADBEEF"));
  assert(callbacks.sends == 4 && strstr(reply, "not queued"));
  assert(command("espnow resetstats"));
  assert(callbacks.resets == 1 && strcmp(reply, "OK - ESP-NOW statistics reset") == 0);
  callbacks.reset_result = false;
  assert(command("espnow resetstats"));
  assert(callbacks.resets == 2 && strstr(reply, "unavailable"));
  Callbacks unsupported;
  cli._callbacks = &unsupported;
  assert(command("get espnow.status") && strstr(reply, "unavailable"));
  assert(command("get espnow.stats") && strstr(reply, "unavailable"));
  assert(command("espnow test") && strstr(reply, "not queued"));
  assert(command("espnow resetstats") && strstr(reply, "unavailable"));
}
''')

    def test_actual_role_forwarders_select_only_the_espnow_transport(self):
        repeater = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        start = repeater.index("#if defined(WITH_ESPNOW_BRIDGE)\n  ESPNowBridge& diagnosticEspNowBridge()")
        end = repeater.index("  // Retry ESP-NOW", start)
        repeater = repeater[start:end] + "\n#endif\n"
        room = (ROOT / "examples/simple_room_server/MyMesh.h").read_text(encoding="ascii")
        start = room.index("#if defined(WITH_BRIDGE)\n#if defined(WITH_MQTT_BRIDGE) && defined(WITH_ESPNOW_BRIDGE)\n  void formatEspNowStatus(")
        end = room.index("\n#endif", start) + len("\n#endif")
        room = room[start:end] + "\n#endif\n"
        prefix = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
struct Base {
  virtual void formatEspNowStatus(char*, size_t) {}
  virtual void formatEspNowStats(const char*, char*, size_t) {}
  virtual bool sendEspNowTest(uint32_t) { return false; }
  virtual bool resetEspNowStats() { return false; }
};
struct ESPNowBridge {
  int calls = 0; uint32_t nonce = 0;
  void formatStatus(char* r, size_t cap) { ++calls; snprintf(r, cap, "status"); }
  void formatStats(const char* page, char* r, size_t cap) { ++calls; assert(strcmp(page,"rx")==0); snprintf(r, cap, "rx"); }
  bool sendTest(uint32_t n) { ++calls; nonce = n; return true; }
  void resetStats() { ++calls; }
};
struct Role : Base {
  ESPNowBridge bridge, espnow_bridge;
'''
        suffix = r'''
};
int main() {
  Role role; char reply[157];
  role.formatEspNowStatus(reply, sizeof(reply));
  role.formatEspNowStats("rx", reply, sizeof(reply));
  assert(role.sendEspNowTest(0xABCD));
  assert(role.resetEspNowStats());
  assert(role.EXPECTED.calls == 4 && role.EXPECTED.nonce == 0xABCD);
  assert(role.OTHER.calls == 0);
}
'''
        for macros in ((), ("WITH_RS232_BRIDGE",), ("WITH_MQTT_BRIDGE",),
                       ("WITH_MQTT_BRIDGE", "WITH_RS232_BRIDGE")):
            with self.subTest(role="repeater", macros=macros):
                selected = "espnow_bridge" if macros else "bridge"
                code = prefix + repeater + suffix.replace("EXPECTED", selected).replace(
                    "OTHER", "bridge" if macros else "espnow_bridge")
                self.compile_run(code, ["-DWITH_ESPNOW_BRIDGE=1", *["-D" + m for m in macros]])
        with self.subTest(role="room"):
            code = prefix + room + suffix.replace("EXPECTED", "espnow_bridge").replace("OTHER", "bridge")
            self.compile_run(code, ["-DWITH_BRIDGE=1", "-DWITH_MQTT_BRIDGE=1", "-DWITH_ESPNOW_BRIDGE=1"])

    def test_diagnostic_virtuals_are_absent_without_espnow(self):
        header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        start = header.index("#if defined(WITH_ESPNOW_BRIDGE)\n  virtual void formatEspNowStatus(")
        end = header.index("\n#endif", start) + len("\n#endif")
        block = header[start:end]
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <type_traits>
#include <utility>
struct Callbacks {
''' + block + r'''
};
template <typename T, typename = void> struct HasDiagnostics : std::false_type {};
template <typename T> struct HasDiagnostics<T, std::void_t<
  decltype(std::declval<T>().formatEspNowStatus(nullptr, 0)),
  decltype(std::declval<T>().formatEspNowStats(nullptr, nullptr, 0)),
  decltype(std::declval<T>().sendEspNowTest(0)),
  decltype(std::declval<T>().resetEspNowStats())>> : std::true_type {};
int main() {
#if defined(WITH_ESPNOW_BRIDGE)
  static_assert(HasDiagnostics<Callbacks>::value);
  Callbacks callbacks; char reply[157];
  callbacks.formatEspNowStatus(reply, sizeof(reply));
  assert(reply[0] == 'E');
  assert(!callbacks.sendEspNowTest(0) && !callbacks.resetEspNowStats());
#else
  static_assert(!HasDiagnostics<Callbacks>::value);
  static_assert(sizeof(Callbacks) == 1);
#endif
}
'''
        self.compile_run(source)
        self.compile_run(source, ["-DWITH_ESPNOW_BRIDGE=1"])


if __name__ == "__main__":
    unittest.main()
