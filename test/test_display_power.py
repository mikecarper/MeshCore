"""Exercise the shared display policy and real settings/CLI code on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

FS_STUB = r'''
#pragma once
#include <map>
#include <string>
#include <vector>
#include <cstring>
#include <cstdint>
struct FakeFS;
struct File {
  FakeFS* fs = nullptr;
  std::string name;
  explicit operator bool() const { return fs != nullptr; }
  size_t size() const;
  size_t read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void close() {}
};
struct FakeFS {
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_write = false, fail_publish = false;
  bool exists(const char* name) { return files.count(name); }
  bool remove(const char* name) { files.erase(name); return true; }
  bool rename(const char* from, const char* to) {
    if (fail_publish && std::string(from) == "/display_prefs.tmp") return false;
    if (!exists(from) || exists(to)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
  File open(const char* name, const char* mode, bool = false) {
    if (*mode == 'w') files[name].clear();
    if (!exists(name)) return {};
    return File{this, name};
  }
};
inline size_t File::size() const { return fs->files[name].size(); }
inline size_t File::read(uint8_t* out, size_t count) {
  const auto& bytes = fs->files[name];
  count = count < bytes.size() ? count : bytes.size();
  memcpy(out, bytes.data(), count); return count;
}
inline size_t File::write(const uint8_t* in, size_t count) {
  if (fs->fail_write) return 0;
  fs->files[name].assign(in, in + count); return count;
}
#define FILESYSTEM FakeFS
'''

TEST = r'''
#include <cassert>
#include <helpers/ui/DisplayPowerSettings.h>
using namespace mesh::ui;
void policyTests() {
  for (unsigned mode = 0; mode <= 5; ++mode) {
    DisplayPowerPrefs prefs;
    prefs.battery.mode = DisplayMode(mode);
    DisplayPowerPolicy p;
    p.update(prefs, false, false, false, 0);
    assert(p.on() == (mode == 1 || mode == 5));
    p.update(prefs, false, false, false, 4000);
    assert(p.on() == (mode == 1));
    bool button = mode == 1 || mode == 2 || mode == 4 || mode == 5;
    assert(p.wake(DisplayWake::Button, 5000) == button);
    assert(p.on() == button);
    p.update(prefs, false, false, false, 20000);
    assert(p.on() == (mode == 1));
    assert(p.wake(DisplayWake::Message, 21000) == (mode == 1 || mode == 5));
    p.update(prefs, false, true, false, 22000);
    assert(p.on() == (mode == 1));
    assert(p.wake(DisplayWake::Message, 23000) == (mode == 1));
    assert(p.wake(DisplayWake::Button, 24000) == button);
    p.update(prefs, false, true, false, 40000);
    p.update(prefs, false, false, true, 41000);
    assert(p.on() == (mode == 1 || mode == 3 || mode == 4 || mode == 5));
    p.update(prefs, false, false, true, 100000);
    assert(p.on() == (mode == 1 || mode == 3 || mode == 4 || mode == 5));
    p.update(prefs, false, true, false, 100001);
    assert(p.on() == (mode == 1));
  }
  DisplayPowerPrefs prefs;
  prefs.usb.mode = DisplayMode::On;
  DisplayPowerPolicy p;
  p.update(prefs, false, false, false, 100);
  p.wake(DisplayWake::Button, 200);
  p.update(prefs, true, false, false, 300);
  assert(p.on());
  p.update(prefs, false, false, false, 400);
  assert(!p.on()); // USB wake does not leak into the battery profile.
  p.wake(DisplayWake::Button, 500);
  prefs.battery.seconds = 1;
  p.update(prefs, false, false, false, 600);
  assert(p.on());
  p.update(prefs, false, false, false, 1600);
  assert(!p.on());
  p.wake(DisplayWake::Button, UINT32_MAX - 500);
  p.update(prefs, false, false, false, 498);
  assert(p.on());
  p.update(prefs, false, false, false, 499);
  assert(!p.on());
  p.wake(DisplayWake::Button, 1000);
  prefs.battery.mode = DisplayMode::Off;
  p.update(prefs, false, false, true, 1001);
  assert(!p.on()); // Off wins over a pending button wake and pairing.
}
void commandTests() {
  FakeFS fs;
  assert(!loadDisplayPowerSettings(&fs, true));
  assert(displayPowerPrefs().battery.mode == DisplayMode::ButtonPairing);
  char reply[160];
  auto run = [&](const char* text, const char* expected) {
    assert(handleDisplayPowerCommand(text, reply, sizeof(reply)));
    assert(std::string(reply) == expected);
  };
  run("set display.mode automatic", "OK");
  run("set display.timeout 27", "OK");
  run("set display.usb.mode on", "OK");
  run("set display.usb.timeout 123", "OK");
  assert(loadDisplayPowerSettings(&fs, true));
  run("get display.mode", "> automatic");
  run("get display.timeout", "> 27");
  run("get display.usb.mode", "> on");
  run("get display.usb.timeout", "> 123");
  // Earlier 12-byte v1 profiles retain their power settings and gain History.
  auto v1 = fs.files["/display_prefs"];
  v1[2] = 1; v1[9] = 0; v1[11] = 0;
  for (unsigned i = 0; i < 11; ++i) v1[11] ^= v1[i];
  fs.files["/display_prefs"] = v1;
  assert(loadDisplayPowerSettings(&fs, true));
  assert(displayPowerPrefs().inbox == DisplayInboxMode::History);
  run("get display.timeout", "> 27");
  run("get display.inbox", "Error: inbox modes unsupported");
  auto inbox = [&](const char* text, const char* expected) {
    assert(handleDisplayPowerCommand(text, reply, sizeof(reply), true));
    assert(std::string(reply) == expected);
  };
  inbox("get display.inbox", "> history");
  for (const char* mode : {"pending", "unread", "history"}) {
    inbox((std::string("set display.inbox ") + mode).c_str(), "OK");
    assert(loadDisplayPowerSettings(&fs, true));
    inbox("get display.inbox", (std::string("> ") + mode).c_str());
    run("get display.timeout", "> 27");
    run("get display.usb.timeout", "> 123");
  }
  inbox("set display.inbox typo", "Error: use history|pending|unread");
  inbox("set display.inbox", "Error: use history|pending|unread");
  inbox("get display.inbox extra", "Error: unexpected argument");
  assert(!handleDisplayPowerCommand("get display.inboxes", reply, sizeof(reply), true));
  const auto saved = fs.files["/display_prefs"];
  for (const char* value : {"", "0", "-1", "+2", "1.5", "15seconds", "3601", "9999999999999999999999"}) {
    const std::string command = std::string("set display.timeout ") + value;
    run(command.c_str(), "Error: timeout must be 1-3600 seconds");
    assert(fs.files["/display_prefs"] == saved);
  }
  run("set display.mode typo", "Error: use off|on|button|pairing|button-pairing|automatic");
  assert(!handleDisplayPowerCommand("set display.model off", reply, sizeof(reply)));
  fs.fail_write = true;
  inbox("set display.inbox unread", "Error: display settings save failed");
  assert(displayPowerPrefs().inbox == DisplayInboxMode::History);
  run("set display.mode off", "Error: display settings save failed");
  assert(displayPowerPrefs().battery.mode == DisplayMode::Automatic);
  assert(fs.files["/display_prefs"] == saved);
  fs.fail_write = false; fs.fail_publish = true;
  run("set display.mode off", "Error: display settings save failed");
  assert(loadDisplayPowerSettings(&fs, true));
  run("get display.mode", "> automatic");
  fs.fail_publish = false;
  fs.files["/display_prefs.bak"] = saved;
  fs.files["/display_prefs"][4] ^= 0x80;
  assert(loadDisplayPowerSettings(&fs, true));
  run("get display.timeout", "> 27");
  assert(loadDisplayPowerSettings(&fs, false));
  run("set display.mode pairing", "Error: BLE pairing display unsupported");
  run("set display.usb.mode button-pairing", "Error: BLE pairing display unsupported");
  FakeFS legacy;
  assert(!loadDisplayPowerSettings(&legacy, false));
  migrateLegacyDisplayTimeout(0);
  assert(loadDisplayPowerSettings(&legacy, false));
  run("get display.mode", "> on");
  run("get display.usb.mode", "> on");
  migrateLegacyDisplayTimeout(42);
  assert(loadDisplayPowerSettings(&legacy, false));
  run("get display.mode", "> automatic");
  run("get display.timeout", "> 42");
  run("get display.usb.timeout", "> 42");
}
int main() { policyTests(); commandTests(); }
'''


class DisplayPowerTest(unittest.TestCase):
    def test_policy_and_persistent_commands(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-display-test-") as name:
            work = Path(name)
            (work / "helpers").mkdir()
            (work / "helpers/IdentityStore.h").write_text(FS_STUB)
            (work / "test.cpp").write_text(TEST)
            executable = work / "display-test.exe"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-DDISPLAY_CLASS=SSD1306Display", "-I" + str(work),
                "-I" + str(ROOT / "src"), str(work / "test.cpp"),
                str(ROOT / "src/helpers/ui/DisplayPowerSettings.cpp"),
                "-o", str(executable),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
