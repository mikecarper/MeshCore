"""Exercise the shared display policy and real settings/CLI code on the host."""
from pathlib import Path
import re
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
#define FILE_O_READ 1
#define FILE_O_WRITE 2
struct FakeFS;
struct File {
  FakeFS* fs = nullptr;
  std::string name;
  File() = default;
  explicit File(FakeFS& backing) : fs(&backing) {}
  File(FakeFS* backing, const char* path) : fs(backing), name(path) {}
  explicit operator bool() const { return fs != nullptr; }
  bool open(const char*, int);
  size_t size() const;
  size_t read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void flush() {}
  bool isDirectory() const { return false; }
  void close() {}
};
struct FakeFS {
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_write = false, fail_publish = false;
  bool corrupt_write = false, fail_temp_read = false, fail_open_write = false;
  bool false_publish = false, late_publish_error = false, fail_public_read = false;
  bool corrupt_public = false, replace_after_validation = false, published = false;
  unsigned calls = 0, public_reads = 0;
  bool exists(const char* name) { ++calls; return files.count(name); }
  bool remove(const char* name) { ++calls; files.erase(name); return true; }
  bool rename(const char* from, const char* to) {
    ++calls;
    if (fail_publish && std::string(from) == "/display_prefs.tmp") return false;
    if (!exists(from)) return false;
#if !defined(NRF52_PLATFORM)
    if (exists(to)) return false;
#endif
    if (false_publish && std::string(from) == "/display_prefs.tmp") return true;
    files[to] = files[from]; files.erase(from);
    if (std::string(from) == "/display_prefs.tmp") {
      published = true; public_reads = 0;
      if (corrupt_public) files[to][0] ^= 1;
      if (late_publish_error) return false;
    }
    return true;
  }
  File open(const char* name, const char* mode, bool = false) {
    ++calls;
    if (*mode == 'w') {
      if (fail_open_write) return {};
      files[name].clear();
    }
    if (!exists(name)) return {};
    return File(this, name);
  }
};
inline bool File::open(const char* path, int mode) {
  *this = fs->open(path, mode == FILE_O_WRITE ? "w" : "r");
  return bool(*this);
}
inline size_t File::size() const { return fs->files[name].size(); }
inline size_t File::read(uint8_t* out, size_t count) {
  if (fs->fail_temp_read && name == "/display_prefs.tmp") return 0;
  if (fs->published && name == "/display_prefs") {
    if (fs->fail_public_read) return 0;
    // Change to another valid image only after AtomicFileWriter's public
    // verification, exercising the caller's independent exact-format readback.
    if (++fs->public_reads == 2 && fs->replace_after_validation) {
      auto& altered = fs->files[name];
      altered[4] ^= 1; altered[11] ^= 1;
    }
  }
  const auto& bytes = fs->files[name];
  count = count < bytes.size() ? count : bytes.size();
  memcpy(out, bytes.data(), count); return count;
}
inline size_t File::write(const uint8_t* in, size_t count) {
  const size_t written = fs->fail_write ? count / 2 : count;
  fs->files[name].assign(in, in + written);
  if (fs->corrupt_write && written) fs->files[name][0] ^= 1;
  return written;
}
#define FILESYSTEM FakeFS
'''

TEST = r'''
#include <cassert>
#include <helpers/ui/DisplayPowerSettings.h>
#if defined(NRF52_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
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
void companionBootTests() {
  // The startup exception is role-wide, independent of transport, power
  // source, or whether an app has already connected by the first UI frame.
  for (bool usb : {false, true}) for (bool connected : {false, true}) {
    for (unsigned mode = 0; mode <= 5; ++mode) {
      DisplayPowerPrefs prefs;
      prefs.wake_on_boot = true;
      prefs.battery.mode = prefs.usb.mode = DisplayMode::Off;
      auto& profile = usb ? prefs.usb : prefs.battery;
      profile.mode = DisplayMode(mode);
      DisplayPowerPolicy p;
      p.update(prefs, usb, connected, false, 100);
      const bool button_mode = mode == 2 || mode == 4;
      assert(p.on() == (mode == 1 || button_mode || (mode == 5 && !connected)));
      p.update(prefs, usb, connected, false, 4100);
      assert(p.on() == (mode == 1 || button_mode)); // Normal 15s, not 4s.
      p.update(prefs, usb, connected, false, 15099);
      assert(p.on() == (mode == 1 || button_mode));
      p.update(prefs, usb, connected, false, 15100);
      assert(p.on() == (mode == 1));
      p.update(prefs, usb, connected, false, 30000);
      assert(p.on() == (mode == 1)); // Refresh cannot retrigger startup.
    }
  }
  for (DisplayMode mode : {DisplayMode::Button, DisplayMode::ButtonPairing}) {
    DisplayPowerPrefs prefs;
    prefs.wake_on_boot = true;
    prefs.battery = prefs.usb = {mode, 27};
    DisplayPowerPolicy p;
    p.update(prefs, false, false, false, 100);
    p.update(prefs, false, true, false, 101);
    assert(p.on()); // Connecting does not dismiss a button-mode boot screen.
    assert(!p.wake(DisplayWake::Message, 200));
    p.update(prefs, false, true, false, 27099);
    assert(p.on());
    assert(p.wake(DisplayWake::Button, 27099));
    p.update(prefs, false, true, false, 27100);
    assert(p.on()); // Real input extends the boot timeout.
    p.update(prefs, false, true, false, 54099);
    assert(!p.on());
    p.update(prefs, false, true, true, 54100);
    assert(p.on() == (mode == DisplayMode::ButtonPairing));
    p.update(prefs, false, true, true, 100000);
    assert(p.on() == (mode == DisplayMode::ButtonPairing));
    p.update(prefs, false, true, false, 100001);
    assert(!p.on());

    DisplayPowerPolicy switched;
    switched.update(prefs, false, false, false, 0);
    assert(switched.on());
    switched.update(prefs, true, false, false, 1);
    assert(!switched.on()); // No synthetic boot when the power source changes.
    switched.update(prefs, false, false, false, 2);
    assert(!switched.on());
    prefs.battery.mode = DisplayMode::Off;
    switched.update(prefs, false, false, false, 3);
    prefs.battery.mode = mode;
    switched.update(prefs, false, false, false, 4);
    assert(!switched.on()); // No synthetic boot when the mode changes.

    DisplayPowerPolicy dismissed;
    dismissed.update(prefs, false, false, false, 0);
    dismissed.dismiss();
    dismissed.update(prefs, false, false, false, 1);
    assert(!dismissed.on());

    DisplayPowerPolicy rollover;
    rollover.update(prefs, false, false, false, UINT32_MAX - 500);
    rollover.update(prefs, false, false, false, 26498);
    assert(rollover.on());
    rollover.update(prefs, false, false, false, 26499);
    assert(!rollover.on());
  }
}
void firstInstallBootTests() {
  // The first-install window begins when the UI takes ownership, even after
  // long radio/key-generation startup, and has exact wrap-safe boundaries.
  for (bool companion : {false, true}) for (bool usb : {false, true}) {
    for (bool connected : {false, true}) for (unsigned mode = 0; mode <= 5; ++mode) {
      for (uint32_t start : {0U, 800000U, UINT32_MAX - 500U}) {
        DisplayPowerPrefs prefs;
        prefs.wake_on_boot = companion;
        prefs.first_install_boot = true;
        prefs.battery = prefs.usb = {DisplayMode(mode), 900};
        DisplayPowerPolicy policy;
        policy.update(prefs, usb, connected, false, start);
        assert(policy.on() == (mode != 0));
        policy.update(prefs, usb, connected, false, start + 119999U);
        assert(policy.on() == (mode != 0));
        policy.update(prefs, usb, connected, false, start + 120000U);
        assert(policy.on() == (mode == 1));
        policy.update(prefs, usb, connected, false, start + 900000U);
        assert(policy.on() == (mode == 1)); // Refreshes cannot restart it.
        assert(prefs.battery.seconds == 900 && prefs.usb.seconds == 900);
      }
    }
  }

  DisplayPowerPrefs prefs;
  prefs.wake_on_boot = true;
  prefs.first_install_boot = true;
  prefs.battery = {DisplayMode::Automatic, 27};
  prefs.usb = {DisplayMode::ButtonPairing, 123};
  const uint32_t start = 700000U;
  DisplayPowerPolicy connected;
  connected.update(prefs, false, false, false, start);
  connected.update(prefs, false, true, false, start + 1000U);
  assert(connected.on()); // An app cannot dismiss the first-install window.
  connected.update(prefs, true, true, false, start + 30000U);
  assert(connected.on());
  connected.update(prefs, false, true, false, start + 119500U);
  connected.update(prefs, false, true, false, start + 119999U);
  assert(connected.on());
  connected.update(prefs, false, true, false, start + 120000U);
  assert(!connected.on()); // USB changes do not restart the window.
  connected.update(prefs, false, false, false, start + 120001U);
  assert(!connected.on());

  // Ordinary input still uses the selected timeout. It cannot shorten the
  // first window, and input near its end can keep the screen awake longer.
  for (DisplayWake reason : {DisplayWake::Button, DisplayWake::Message}) {
    DisplayPowerPolicy input;
    input.update(prefs, false, false, false, start);
    assert(input.wake(reason, start + 10000U));
    input.update(prefs, false, false, false, start + 37000U);
    assert(input.on());
    assert(input.wake(reason, start + 110000U));
    input.update(prefs, false, false, false, start + 120000U);
    assert(input.on());
    input.update(prefs, false, false, false, start + 136999U);
    assert(input.on());
    input.update(prefs, false, false, false, start + 137000U);
    assert(!input.on());
  }

  DisplayPowerPolicy dismissed;
  dismissed.update(prefs, false, false, false, start);
  dismissed.dismiss();
  dismissed.update(prefs, false, false, false, start + 1U);
  assert(!dismissed.on());
  dismissed.update(prefs, true, false, false, start + 2U);
  assert(!dismissed.on());

  DisplayPowerPolicy turned_off;
  turned_off.update(prefs, false, false, false, start);
  prefs.usb.mode = DisplayMode::Off;
  turned_off.update(prefs, true, false, true, start + 1U);
  assert(!turned_off.on());
  prefs.battery.mode = DisplayMode::Button;
  turned_off.update(prefs, false, false, false, start + 2U);
  assert(!turned_off.on()); // Off cancels the first window permanently.
  assert(turned_off.wake(DisplayWake::Button, start + 3U));
  turned_off.update(prefs, false, false, false, start + 27003U);
  assert(!turned_off.on());

  DisplayPowerPolicy initialized;
  prefs.first_install_boot = false;
  initialized.update(prefs, false, false, false, start);
  prefs.first_install_boot = true;
  initialized.update(prefs, false, false, false, start + 27000U);
  assert(!initialized.on()); // Only the first policy update can arm startup.
}
void firstInstallSettingsTests() {
  for (bool ble : {false, true}) for (bool companion : {false, true}) {
    FakeFS fs;
    assert(!loadDisplayPowerSettings(&fs, ble, companion));
    assert(!displayPowerPrefs().first_install_boot);
    displayPowerPrefs().first_install_boot = true;
    assert(displayPowerPrefs().battery.seconds == 15 && displayPowerPrefs().usb.seconds == 15);
    DisplayPowerPolicy first;
    first.update(displayPowerPrefs(), false, true, false, 500000U);
    first.update(displayPowerPrefs(), false, true, false, 619999U);
    assert(first.on());
    first.update(displayPowerPrefs(), false, true, false, 620000U);
    assert(!first.on());
    assert(fs.files.empty()); // First-install display time writes no settings.

    // A later boot is ordinary even while /display_prefs is still absent.
    assert(!loadDisplayPowerSettings(&fs, ble, companion));
    assert(!displayPowerPrefs().first_install_boot);
    DisplayPowerPolicy next;
    next.update(displayPowerPrefs(), false, false, false, 0);
    assert(next.on() == companion);
    next.update(displayPowerPrefs(), false, false, false, 15000U);
    assert(!next.on());

    displayPowerPrefs().first_install_boot = true;
    char reply[160];
    assert(handleDisplayPowerCommand("set display.timeout 27", reply, sizeof(reply)));
    assert(std::string(reply) == "OK");
    assert(displayPowerPrefs().first_install_boot);
    const auto saved = fs.files.at("/display_prefs");
    assert(saved.size() == 12 && saved[2] == 2 && saved[10] == 0);
    assert(saved[4] == 27 && saved[5] == 0 && saved[7] == 15 && saved[8] == 0);
    assert(loadDisplayPowerSettings(&fs, ble, companion));
    assert(!displayPowerPrefs().first_install_boot); // Never serialized by CLI saves.
    assert(displayPowerPrefs().battery.seconds == 27 && displayPowerPrefs().usb.seconds == 15);
    assert(fs.files.at("/display_prefs") == saved);
    DisplayPowerPolicy saved_boot;
    saved_boot.update(displayPowerPrefs(), false, false, false, 0);
    assert(saved_boot.on() == companion);
    saved_boot.update(displayPowerPrefs(), false, false, false, 27000U);
    assert(!saved_boot.on());
  }
}
void companionSettingsTests() {
  for (bool ble : {false, true}) {
    FakeFS fs;
    assert(!loadDisplayPowerSettings(&fs, ble, true));
    assert(displayPowerPrefs().wake_on_boot);
    const DisplayMode mode = ble ? DisplayMode::ButtonPairing : DisplayMode::Button;
    assert(displayPowerPrefs().battery.mode == mode);
    assert(displayPowerPrefs().usb.mode == mode);
    char reply[160];
    for (const char* command : {"set display.timeout 27", "set display.usb.timeout 123"}) {
      assert(handleDisplayPowerCommand(command, reply, sizeof(reply)));
      assert(std::string(reply) == "OK");
    }
    assert(fs.files["/display_prefs"].size() == 12); // No on-device format change.
    assert(fs.files["/display_prefs"][10] == 0);
    assert(loadDisplayPowerSettings(&fs, ble, true));
    assert(displayPowerPrefs().wake_on_boot);
    for (bool usb : {false, true}) {
      DisplayPowerPolicy p;
      p.update(displayPowerPrefs(), usb, false, false, 0);
      assert(p.on()); // Previously saved button modes also show startup.
      const uint32_t timeout = usb ? 123000 : 27000;
      p.update(displayPowerPrefs(), usb, false, false, timeout - 1);
      assert(p.on());
      p.update(displayPowerPrefs(), usb, false, false, timeout);
      assert(!p.on());
    }
    for (const char* command : {"set display.mode off", "set display.usb.mode off"}) {
      assert(handleDisplayPowerCommand(command, reply, sizeof(reply)));
      assert(std::string(reply) == "OK");
    }
    for (unsigned version : {1, 2}) {
      auto& saved = fs.files["/display_prefs"];
      saved[2] = version; saved[11] = 0;
      for (unsigned i = 0; i < 11; ++i) saved[11] ^= saved[i];
      assert(loadDisplayPowerSettings(&fs, ble, true));
      assert(displayPowerPrefs().wake_on_boot);
      for (bool usb : {false, true}) {
        DisplayPowerPolicy p;
        p.update(displayPowerPrefs(), usb, false, true, 0);
        assert(!p.on()); // Saved off wins over startup and pairing.
        assert(!p.wake(DisplayWake::Button, 1));
      }
    }
    assert(loadDisplayPowerSettings(&fs, ble));
    assert(!displayPowerPrefs().wake_on_boot); // Infrastructure does not opt in.
    assert(!loadDisplayPowerSettings(nullptr, ble, true));
    assert(displayPowerPrefs().wake_on_boot); // Defaults still work without flash.
    DisplayPowerPolicy p;
    p.update(displayPowerPrefs(), false, false, false, 0);
    assert(p.on());
    assert(!loadDisplayPowerSettings(nullptr, ble));
    assert(!displayPowerPrefs().wake_on_boot);
  }
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
int main() {
  policyTests(); companionBootTests(); firstInstallBootTests();
  firstInstallSettingsTests(); companionSettingsTests(); commandTests();
}
'''


NRF_ATOMIC_TESTS = r'''
// Independent persisted bytes preserve the existing v2 disk layout.
std::vector<uint8_t> powerRecord(uint16_t timeout) {
  std::vector<uint8_t> data = {'D', 'P', 2, 4, uint8_t(timeout),
      uint8_t(timeout >> 8), 4, 15, 0, 0, 0, 0};
  for (unsigned i = 0; i < 11; ++i) data[11] ^= data[i];
  return data;
}
void atomicPersistenceTests() {
  const auto previous = powerRecord(15), candidate = powerRecord(27);
  for (unsigned failure = 0; failure < 9; ++failure) {
    FakeFS fs;
    fs.files["/display_prefs"] = previous;
    fs.files["/display_prefs.bak"] = previous;
    assert(loadDisplayPowerSettings(&fs, true));
    switch (failure) {
      case 0: fs.fail_write = true; break;
      case 1: fs.corrupt_write = true; break;
      case 2: fs.fail_temp_read = true; break;
      case 3: fs.fail_open_write = true; break;
      case 4: fs.fail_publish = true; break;
      case 5: fs.false_publish = true; break;
      case 6: fs.fail_public_read = true; break;
      case 7: fs.corrupt_public = true; break;
      case 8: fs.replace_after_validation = true; break;
    }
    char reply[160];
    assert(handleDisplayPowerCommand("set display.timeout 27", reply, sizeof(reply)));
    assert(std::string(reply) == "Error: display settings save failed");
    assert(displayPowerPrefs().battery.seconds == 15);
    assert(fs.files.at("/display_prefs.bak") == previous);
    assert(!fs.files.count("/display_prefs.tmp"));
    if (failure < 6) assert(fs.files.at("/display_prefs") == previous);
    if (failure == 6) assert(fs.files.at("/display_prefs") == candidate);
    // A post-publication failure is not proof that the old record survived.
    if (failure >= 7) assert(fs.files.at("/display_prefs") != previous);
  }
  for (bool late_error : {false, true}) {
    FakeFS fs;
    fs.files["/display_prefs"] = previous;
    fs.files["/display_prefs.bak"] = previous;
    fs.files["/display_prefs.tmp"] = {'s', 't', 'a', 'l', 'e'};
    assert(loadDisplayPowerSettings(&fs, true));
    fs.late_publish_error = late_error;
    char reply[160];
    assert(handleDisplayPowerCommand("set display.timeout 27", reply, sizeof(reply)));
    assert(std::string(reply) == "OK");
    assert(fs.files.at("/display_prefs") == candidate);
    assert(!fs.files.count("/display_prefs.bak"));
    assert(!fs.files.count("/display_prefs.tmp"));
    assert(displayPowerPrefs().battery.seconds == 27);
    assert(loadDisplayPowerSettings(&fs, true));
    assert(displayPowerPrefs().battery.seconds == 27);
  }
}
'''


HEADLESS_TEST = r'''
#include <cassert>
#include <helpers/ui/DisplayPowerSettings.h>
using namespace mesh::ui;
int main() {
  FakeFS fs;
  fs.files["/display_prefs"] = {'D', 'P', 2, 5, 42, 0, 1, 57, 0, 2, 0, 0};
  fs.files["/display_prefs.bak"] = {'b', 'a', 'c', 'k', 'u', 'p'};
  fs.files["/display_prefs.tmp"] = {'t', 'e', 'm', 'p'};
  const auto original = fs.files;
  for (bool pairing : {false, true}) for (bool wake : {false, true}) {
    displayPowerPrefs().battery = {DisplayMode::Off, 123};
    displayPowerPrefs().usb = {DisplayMode::On, 456};
    displayPowerPrefs().inbox = DisplayInboxMode::Unread;
    displayPowerPrefs().first_install_boot = true;
    assert(!loadDisplayPowerSettings(&fs, pairing, wake));
    const auto& prefs = displayPowerPrefs();
    const auto mode = pairing ? DisplayMode::ButtonPairing : DisplayMode::Button;
    assert(prefs.battery.mode == mode && prefs.usb.mode == mode);
    assert(prefs.battery.seconds == 15 && prefs.usb.seconds == 15);
    assert(prefs.inbox == DisplayInboxMode::History && prefs.wake_on_boot == wake);
    assert(!prefs.first_install_boot);
    assert(displayPairingSupported() == pairing);
    for (const char* key : {"display.mode", "display.timeout", "display.usb.mode",
                            "display.usb.timeout", "display.inbox"}) {
      for (const char* prefix : {"get ", "set "}) for (const char* suffix : {"", " off", " typo"}) {
        char reply[160] = {};
        const std::string text = std::string(prefix) + key + suffix;
        assert(handleDisplayPowerCommand(text.c_str(), reply, sizeof(reply), true));
        assert(std::string(reply) == "Error: display unsupported");
      }
    }
    char reply[32] = "untouched";
    assert(!handleDisplayPowerCommand("get display.model", reply, sizeof(reply)));
    assert(std::string(reply) == "untouched");
    char bounded[3] = {'x', 'y', 'z'};
    assert(handleDisplayPowerCommand("get display.mode", bounded, 1));
    assert(bounded[0] == 0 && bounded[1] == 'y' && bounded[2] == 'z');
    migrateLegacyDisplayTimeout(0);
    assert(displayPowerPrefs().battery.mode == DisplayMode::On);
    assert(displayPowerPrefs().usb.mode == DisplayMode::On);
    migrateLegacyDisplayTimeout(42);
    assert(displayPowerPrefs().battery.mode == DisplayMode::Automatic);
    assert(displayPowerPrefs().usb.mode == DisplayMode::Automatic);
    assert(displayPowerPrefs().battery.seconds == 42 && displayPowerPrefs().usb.seconds == 42);
    assert(displayPowerPrefs().wake_on_boot == wake && displayPairingSupported() == pairing);
    migrateLegacyDisplayTimeout(3601);
    assert(displayPowerPrefs().battery.seconds == 42 && displayPowerPrefs().usb.seconds == 42);
    assert(fs.calls == 0 && fs.files == original);
    assert(!loadDisplayPowerSettings(nullptr, pairing, wake));
    assert(displayPowerPrefs().battery.mode == mode && displayPowerPrefs().wake_on_boot == wake);
    assert(fs.calls == 0 && fs.files == original);
  }
}
'''


EINK_TEST = r'''
#include <cassert>
#include <helpers/ui/DisplayPowerSettings.h>
using namespace mesh::ui;
int main() {
  for (bool ble : {false, true}) for (bool companion : {false, true}) {
    FakeFS fs;
    assert(!loadDisplayPowerSettings(&fs, ble, companion));
    const auto& prefs = displayPowerPrefs();
    assert(prefs.battery.seconds == 900 && prefs.usb.seconds == 900);
    displayPowerPrefs().first_install_boot = true;
    DisplayPowerPolicy first;
    first.update(prefs, false, true, false, 500000U);
    first.update(prefs, false, true, false, 619999U);
    assert(first.on());
    first.update(prefs, false, true, false, 620000U);
    assert(!first.on()); // E-paper gets the same two-minute first startup.
    assert(prefs.battery.seconds == 900 && prefs.usb.seconds == 900);
    assert(first.wake(DisplayWake::Button, 620001U));
    first.update(prefs, false, true, false, 1520000U);
    assert(first.on());
    first.update(prefs, false, true, false, 1520001U);
    assert(!first.on()); // Later button wakes retain the 15-minute default.
    displayPowerPrefs().first_install_boot = false;
    for (bool usb : {false, true}) {
      DisplayPowerPolicy policy;
      const uint32_t start = UINT32_MAX - 1000;
      policy.update(prefs, usb, false, false, start);
      assert(policy.on() == companion);
      assert(policy.wake(DisplayWake::Button, start));
      // Redraw/service calls and an app connection do not count as input.
      policy.update(prefs, usb, true, false, start + 899999);
      assert(policy.on());
      policy.update(prefs, usb, true, false, start + 900000);
      assert(!policy.on());
      assert(policy.wake(DisplayWake::Button, start + 900001));
      assert(policy.on());
    }
    char reply[160];
    for (const char* command : {"set display.timeout 27", "set display.usb.timeout 123"}) {
      assert(handleDisplayPowerCommand(command, reply, sizeof(reply)));
      assert(std::string(reply) == "OK");
    }
    const auto saved = fs.files.at("/display_prefs");
    assert(saved.size() == 12 && saved[2] == 2);
    assert(loadDisplayPowerSettings(&fs, ble, companion));
    assert(prefs.battery.seconds == 27 && prefs.usb.seconds == 123);
    assert(fs.files.at("/display_prefs") == saved);
    for (bool usb : {false, true}) {
      DisplayPowerPolicy policy;
      policy.update(prefs, usb, false, false, 0);
      policy.wake(DisplayWake::Button, 0);
      const uint32_t timeout = usb ? 123000 : 27000;
      policy.update(prefs, usb, false, false, timeout - 1);
      assert(policy.on());
      policy.update(prefs, usb, false, false, timeout);
      assert(!policy.on());
    }
    // A legacy explicit always-on setting must not become a timed profile.
    migrateLegacyDisplayTimeout(0);
    assert(loadDisplayPowerSettings(&fs, ble, companion));
    for (bool usb : {false, true}) {
      DisplayPowerPolicy policy;
      policy.update(prefs, usb, false, false, 0);
      policy.update(prefs, usb, false, false, 3600000);
      assert(policy.on());
    }
  }
}
'''


class DisplayPowerTest(unittest.TestCase):
    def test_all_eink_drivers_default_to_fifteen_minute_idle_without_overriding_saved_profiles(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-eink-timeout-test-") as name:
            work = Path(name)
            (work / "helpers").mkdir()
            (work / "helpers/IdentityStore.h").write_text(FS_STUB)
            (work / "test.cpp").write_text(EINK_TEST)
            for driver in ("GxEPDDisplay", "E213Display", "E290Display"):
                with self.subTest(driver=driver):
                    executable = work / (driver + ".exe")
                    result = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DDISPLAY_CLASS=" + driver, "-I" + str(work),
                        "-I" + str(ROOT / "src"), str(work / "test.cpp"),
                        str(ROOT / "src/helpers/ui/DisplayPowerSettings.cpp"),
                        "-o", str(executable),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_companion_boot_wake_is_transport_and_layout_independent(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertRegex(mesh, re.compile(
            r"loadDisplayPowerSettings\(_store->getPrimaryFS\(\),\s*"
            r"#ifdef BLE_PIN_CODE\s*true,\s*#else\s*false,\s*#endif\s*true\b"))
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        setup = main[main.index("void setup()"):main.index("\nvoid loop()")]
        self.assertLess(setup.index("the_mesh.begin("), setup.index("ui_task.begin("))
        for layout in ("ui-new", "ui-orig", "ui-tiny"):
            with self.subTest(layout=layout):
                ui = (ROOT / "examples/companion_radio" / layout / "UITask.cpp").read_text()
                start = ui.index("void UITask::begin(")
                end = ui.index("\n}", start)
                self.assertIn("_display->servicePower(", ui[start:end])
        infrastructure = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        self.assertIn("loadDisplayPowerSettings(fs, false);", infrastructure)

    def test_headless_defaults_commands_and_legacy_migration_do_not_touch_files(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-headless-display-test-") as name:
            work = Path(name)
            (work / "helpers").mkdir()
            (work / "helpers/IdentityStore.h").write_text(FS_STUB)
            (work / "test.cpp").write_text(HEADLESS_TEST)
            for label, definitions in (("absent", ()), ("null", ("-DDISPLAY_CLASS=NullDisplayDriver",))):
                with self.subTest(display=label):
                    executable = work / (label + ".exe")
                    result = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        *definitions, "-I" + str(work), "-I" + str(ROOT / "src"),
                        str(work / "test.cpp"), str(ROOT / "src/helpers/ui/DisplayPowerSettings.cpp"),
                        "-o", str(executable),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    def test_nrf52_atomic_publication_failures_and_legacy_format(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-nrf-display-test-") as name:
            work = Path(name)
            (work / "helpers").mkdir()
            (work / "helpers/IdentityStore.h").write_text(FS_STUB, encoding="ascii")
            code = TEST.replace("int main() {", NRF_ATOMIC_TESTS + "\nint main() {\n  atomicPersistenceTests();")
            (work / "test.cpp").write_text(code, encoding="ascii")
            executable = work / "nrf-display-test.exe"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-DNRF52_PLATFORM", "-DDISPLAY_CLASS=SSD1306Display",
                "-I" + str(work), "-I" + str(ROOT / "src"),
                str(work / "test.cpp"), str(ROOT / "src/helpers/ui/DisplayPowerSettings.cpp"),
                "-o", str(executable),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
