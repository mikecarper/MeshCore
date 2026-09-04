#!/usr/bin/env python3
"""Execute production file readers against SPIFFS-style phantom directories."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def section(path, start, end):
    text = (ROOT / path).read_text(encoding="utf-8")
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


HARNESS = r'''
#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <iostream>
#include <helpers/FileRead.h>
#define FILE_O_READ 0
static void check(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}
struct Entry { std::string text; bool directory = false; };
struct FakeFS;
struct File {
  std::shared_ptr<Entry> entry;
  size_t position = 0;
  FakeFS* owner = nullptr;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  // Actual Adafruit LittleFS requires an owner even for a closed File.
  File() = delete;
#else
  File() = default;
#endif
  explicit File(FakeFS& fs) : owner(&fs) {}
  File(FakeFS& fs, std::shared_ptr<Entry> data) : entry(data), owner(&fs) {}
  explicit operator bool() const { return bool(entry); }
  bool isDirectory() const { return entry && entry->directory; }
  size_t size() const { return entry && !isDirectory() ? entry->text.size() : 0; }
  size_t available() const { return size() - position; }
  size_t read(uint8_t* dest, size_t count) {
    if (!entry || isDirectory()) return 0;
    count = std::min(count, available());
    memcpy(dest, entry->text.data() + position, count);
    position += count;
    return count;
  }
  void close() { entry.reset(); }
};
struct FakeFS {
  std::map<std::string, std::shared_ptr<Entry>> entries;
  bool fail_open = false;
  bool report_directory_exists = false;
  unsigned missing_opens = 0;
  unsigned open_calls = 0;
  bool exists(const char* path) {
    auto it = entries.find(path);
    return it != entries.end() &&
        (!it->second->directory || report_directory_exists);
  }
  File fixtureOpen(const char* path) {
    ++open_calls;
    if (fail_open) return File(*this);
    auto it = entries.find(path);
    if (it != entries.end()) return File(*this, it->second);
    ++missing_opens;
    return File(*this, std::make_shared<Entry>(Entry{"", true}));
  }
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File open(const char* path, uint8_t = FILE_O_READ) { return fixtureOpen(path); }
#elif defined(RP2040_PLATFORM)
  // Philhower FS requires the mode argument, unlike Arduino ESP32.
  File open(const char* path, const char*) { return fixtureOpen(path); }
#else
  File open(const char* path, const char* = "r", bool = false) { return fixtureOpen(path); }
#endif
  void put(const char* path, const std::string& content, bool directory = false) {
    entries[path] = std::make_shared<Entry>(Entry{content, directory});
  }
} SPIFFS;
FakeFS InternalFS;
using FILESYSTEM = FakeFS;
class DataStore {
 public:
  FILESYSTEM* _fs;
  explicit DataStore(FILESYSTEM* fs) : _fs(fs) {}
  File openRead(const char*);
  File openRead(FILESYSTEM*, const char*);
  File openDirectory(const char*);
  File openDirectory(FILESYSTEM*, const char*);
};
@DATASTORE@
struct WiFiClient {
  std::string output;
  bool connected() const { return true; }
  size_t write(const uint8_t* data, size_t size) {
    output.append(reinterpret_cast<const char*>(data), size);
    return size;
  }
  void printf(const char* format, ...) {
    char buffer[512];
    va_list args;
    va_start(args, format);
    int size = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    check(size >= 0 && static_cast<size_t>(size) < sizeof(buffer), "HTTP overflow");
    output.append(buffer, size);
  }
};
struct LogServer {
  bool running = true;
  @RESPONSE@
  @SENDLOG@
};
@FLOOD_VERIFY@
int main() {
 try {
  FakeFS fs;
  DataStore store(&fs);
  File unopened = mesh::emptyFile(&fs);
  File unavailable = mesh::emptyFile(static_cast<FILESYSTEM*>(nullptr));
  check(!unopened && !unavailable, "empty helper returned an open file");
  check(fs.open_calls == 0 && InternalFS.open_calls == 0,
      "closed file construction must never access either filesystem");
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  check(unopened.owner == &fs, "closed LittleFS file lost its selected owner");
  check(unavailable.owner == &InternalFS, "null filesystem lacks a valid fallback owner");
#endif
  File phantom = fs.fixtureOpen("/missing");
  check(bool(phantom) && phantom.isDirectory(), "fixture must reproduce SPIFFS");
  unsigned before = fs.missing_opens;
  check(!store.openRead("/missing"), "missing file accepted");
  check(fs.missing_opens == before, "missing regular file should not be opened");
  check(!store.openRead(nullptr, "/file"), "unavailable FS accepted");
  check(!store.openRead(nullptr), "null path accepted");
  check(!store.openDirectory(nullptr, "/"), "unavailable directory FS accepted");
  check(!store.openDirectory(nullptr), "null directory path accepted");
  fs.put("/empty", "");
  fs.put("/real", "data");
  check(bool(store.openRead("/empty")), "real empty file rejected");
  File real = store.openRead("/real");
  uint8_t bytes[4];
  check(real.read(bytes, sizeof(bytes)) == 4 && memcmp(bytes, "data", 4) == 0,
      "real file changed");
  check(!store.openDirectory("/real"), "regular file accepted as directory");
  check(store.openDirectory("/").isDirectory(), "SPIFFS root listing broken");
  fs.put("/directory", "", true);
  fs.report_directory_exists = true;
  check(!store.openRead("/directory"), "directory accepted when exists is true");
  check(store.openDirectory("/directory").isDirectory(), "real listing broken");
  fs.fail_open = true;
  check(!store.openRead("/real"), "failed read-open accepted");
  fs.fail_open = false;
  // Empty expected images must not validate a missing file/directory as data.
  check(!verifyFloodSettingsWrite(&fs, "/missing", 0, 2166136261UL),
      "missing flood file passed empty verification");
  check(!verifyFloodSettingsWrite(&fs, "/directory", 0, 2166136261UL),
      "directory passed empty verification");
  check(verifyFloodSettingsWrite(&fs, "/empty", 0, 2166136261UL),
      "real empty image should verify");
  check(verifyFloodSettingsWrite(&fs, "/real", 4,
      updateFloodSettingsHash(2166136261UL, reinterpret_cast<const uint8_t*>("data"), 4)),
      "valid flood image rejected");
  LogServer server;
  WiFiClient missing;
  server.sendLog(missing);
  check(missing.output.find("HTTP/1.1 404 Not Found\r\n") == 0,
      "missing packet log must return HTTP404");
  SPIFFS.put("/packet_log", "");
  WiFiClient empty;
  server.sendLog(empty);
  check(empty.output.find("HTTP/1.1 200 OK\r\n") == 0 &&
      empty.output.find("Content-Length: 0\r\n") != std::string::npos,
      "existing empty packet log must remain HTTP200");
  SPIFFS.put("/packet_log", "packet\n");
  WiFiClient log;
  server.sendLog(log);
  check(log.output.find("Content-Length: 7\r\n") != std::string::npos &&
      log.output.substr(log.output.size() - 7) == "packet\n", "log download changed");
  SPIFFS.put("/packet_log", "", true);
  SPIFFS.report_directory_exists = true;
  WiFiClient directory;
  server.sendLog(directory);
  check(directory.output.find("HTTP/1.1 404 Not Found\r\n") == 0,
      "directory log must return HTTP404");
  std::cout << "regular-file readers: PASS\n";
  return 0;
 } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
'''


class RegularFileReadTest(unittest.TestCase):
    def test_production_readers(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A host C++ compiler is required")
        program = HARNESS.replace("@DATASTORE@", section(
            "examples/companion_radio/DataStore.cpp", "File DataStore::openRead(",
            "bool DataStore::removeFile("))
        program = program.replace("@RESPONSE@", section(
            "src/helpers/ESP32Board.cpp", "  static void sendResponse(", "  bool readLine("))
        program = program.replace("@SENDLOG@", section(
            "src/helpers/ESP32Board.cpp", "  void sendLog(", "  void sendUpdateError("))
        read_helper = section("examples/simple_repeater/MyMesh.cpp",
                              "static File openFloodSettingsRead(",
                              "static File openFloodSettingsWrite(")
        verification = section("examples/simple_repeater/MyMesh.cpp",
                               "static uint32_t updateFloodSettingsHash(",
                               "static uint8_t batteryPercentFromMilliVolts(")
        program = program.replace("@FLOOD_VERIFY@", read_helper + verification)
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                executable = Path(tmp) / ("readers.exe" if os.name == "nt" else "readers")
                built = subprocess.run(
                    [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0",
                     f"-D{platform}", "-I", str(ROOT / "test/fixtures/regular_file_reads"),
                     "-I", str(ROOT / "src"), "-x", "c++", "-",
                     "-o", str(executable)],
                    input=program, text=True, capture_output=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_directory_listing_uses_explicit_directory_api(self):
        cli = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertIn("File root = _store->openDirectory(path);", cli)
        self.assertIn("File root2 = _store->openDirectory(_store->getSecondaryFS(), path);", cli)
        self.assertIn("File file = _store->openRead(selected_fs, path);", cli)

    def test_audited_read_helpers_remain_regular_file_only(self):
        for path, marker, end in (
            ("src/helpers/ClientACL.cpp", "static File openRead(", "#if !defined(NRF52_PLATFORM)"),
            ("examples/simple_room_server/FloodRuleEngine.cpp", "static File openRead(", "static File openWrite("),
        ):
            with self.subTest(path=path):
                self.assertIn("mesh::openFileRead(", section(path, marker, end))
        for role in ("simple_repeater", "simple_room_server"):
            dump = section(f"examples/{role}/MyMesh.cpp", "void MyMesh::dumpLogFile()",
                           "bool MyMesh::hasPendingSerialOutput()")
            self.assertEqual(dump.count("mesh::openFileRead(_fs, PACKET_LOG_FILE)"), 2)
            self.assertNotIn("_fs->open(", dump)


if __name__ == "__main__":
    unittest.main()
