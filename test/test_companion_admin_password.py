#!/usr/bin/env python3
"""Fault-inject the real Companion NVS credential store, including reboot reads."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PREFERENCES = r'''
#pragma once
#include <string>
#include <cstring>
#include <algorithm>
enum PreferenceType { PT_INVALID, PT_U8, PT_STR };
struct Disk {
  bool begin_ok = true, has_password = false, has_pending = false;
  PreferenceType password_type = PT_STR, pending_type = PT_U8;
  std::string password;
  unsigned char pending = 0;
  int writes = 0, fail_write = 0, reads = 0, fail_read = 0;
  bool corrupt_read = false;
} disk;
class Preferences {
 public:
  bool begin(const char* name, bool read_only) {
    return !strcmp(name, "mesh-admin") && !read_only && disk.begin_ok;
  }
  void end() {}
  bool isKey(const char* name) { return !strcmp(name, "password") ? disk.has_password : disk.has_pending; }
  PreferenceType getType(const char* name) { return !strcmp(name, "password") ? disk.password_type : disk.pending_type; }
  unsigned char getUChar(const char*, unsigned char fallback) {
    if (++disk.reads == disk.fail_read || !disk.has_pending || disk.pending_type != PT_U8) return fallback;
    return disk.pending;
  }
  size_t putUChar(const char*, unsigned char value) {
    if (++disk.writes == disk.fail_write) return 0;
    disk.has_pending = true; disk.pending_type = PT_U8; disk.pending = value; return 1;
  }
  size_t putString(const char*, const char* value) {
    if (++disk.writes == disk.fail_write) return 0;
    disk.has_password = true; disk.password_type = PT_STR; disk.password = value; return strlen(value);
  }
  size_t getString(const char*, char* output, size_t size) {
    if (++disk.reads == disk.fail_read || disk.password.size() + 1 > size) return 0;
    memcpy(output, disk.password.c_str(), disk.password.size() + 1);
    if (disk.corrupt_read) output[0] ^= 1;
    return disk.password.size() + 1;
  }
};
'''


class CompanionAdminPasswordTest(unittest.TestCase):
    def test_migration_rotation_power_loss_and_explicit_repair(self):
        source = r'''
#include <helpers/esp32/CompanionAdminPassword.h>
#include <cassert>
using namespace mesh::esp32::companionAdminPassword;
void fresh() { disk = Disk{}; }
void reboot() { disk.writes = disk.reads = disk.fail_write = disk.fail_read = 0; disk.corrupt_read = false; }
void unavailable() {
  char value[CAPACITY]; memset(value, 'X', sizeof(value));
  assert(!load(value, "factory"));
  for (char c : value) assert(c == 0);
}
int main() {
  char value[CAPACITY];
  fresh(); assert(load(value, "factory") && !strcmp(value, "factory"));
  assert(!disk.has_password); // Loading never overwrites saved state.
  for (const char* invalid : {"", "1234567890123456", "abc\rdef", "abc\ndef"}) {
    assert(!save(invalid)); assert(disk.writes == 0);
    assert(!load(value, invalid));
  }
  assert(!save(nullptr));
  for (const char* valid_password : {"a", "123456789012345", "a:b ", "\303\251"}) {
    assert(save(valid_password)); reboot();
    assert(load(value, "different-default") && !strcmp(value, valid_password));
  }
  fresh(); disk.begin_ok = false; unavailable(); assert(!save("replacement"));
  fresh(); disk.has_password = true;
  for (const char* malformed : {"", "1234567890123456", "bad\rpassword", "bad\npassword"}) {
    disk.password = malformed; unavailable();
  }
  disk.password = std::string("valid\0hidden", 12); unavailable();
  disk.password = "saved-password"; disk.password_type = PT_U8; unavailable();
  disk.password_type = PT_STR; disk.has_pending = true;
  disk.pending_type = PT_STR; unavailable();
  disk.pending_type = PT_U8; disk.pending = 1; unavailable();
  disk.pending = 0; assert(load(value, "factory") && !strcmp(value, "saved-password"));
  disk.has_password = false; unavailable(); // A partial save cannot become factory login.
  for (int failing_write = 1; failing_write <= 3; ++failing_write) {
    fresh(); assert(save("old-password")); reboot();
    disk.fail_write = failing_write;
    assert(!save("new-password")); reboot();
    if (failing_write == 1) assert(load(value, "factory") && !strcmp(value, "old-password"));
    else unavailable(); // Pending survives power loss / failed data or commit writes.
    assert(save("repaired")); reboot();
    assert(load(value, "factory") && !strcmp(value, "repaired"));
  }
  for (int failing_read = 1; failing_read <= 3; ++failing_read) {
    fresh(); assert(save("old-password")); reboot();
    disk.fail_read = failing_read; assert(!save("new-password")); reboot();
    if (failing_read < 3) unavailable();
    else assert(load(value, "factory") && !strcmp(value, "new-password"));
    // A failed final read is honestly unconfirmed: reboot may see the durable new value.
  }
  fresh(); disk.corrupt_read = true; assert(!save("new-password")); reboot(); unavailable();
  fresh(); assert(save("saved-password")); reboot();
  disk.fail_read = 1; unavailable(); reboot();
  disk.fail_read = 2; unavailable();
}
'''
        with tempfile.TemporaryDirectory(prefix="companion-admin-") as temporary:
            work = Path(temporary)
            (work / "Preferences.h").write_text(PREFERENCES)
            (work / "test.cpp").write_text(source)
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP32_PLATFORM=1", '-DWIFI_SSID=""', "-I", str(work), "-I", str(ROOT / "src"),
                str(work / "test.cpp"), "-o", str(work / "test")], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(work / "test")], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
