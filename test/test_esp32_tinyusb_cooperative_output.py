#!/usr/bin/env python3
"""Execute the real native-TinyUSB role output pumps with small host stubs."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstdint>
#include <helpers/FileRead.h>
#define MESH_ESP32_TINYUSB_NONBLOCKING 1
#define MESH_ESP32_USB_CONSOLE_COOPERATIVE 1
#define MAX_ROUTE_HASH_BYTES 3
#define PACKET_LOG_FILE "/packet_log"
static void require(bool ok, const char* what) {
  if (!ok) throw std::runtime_error(what);
}
struct FileData { std::string text; size_t reads = 0; bool exists = true; };
struct File {
  std::shared_ptr<FileData> data;
  size_t pos = 0;
  bool closed = false;
  bool directory = false;
  explicit operator bool() const { return data && (data->exists || directory) && !closed; }
  bool isDirectory() const { return directory; }
  size_t size() const { return data ? data->text.size() : 0; }
  int read() {
    if (!static_cast<bool>(*this) || pos >= data->text.size()) return -1;
    ++data->reads;
    return static_cast<unsigned char>(data->text[pos++]);
  }
  void close() { closed = true; }
};
struct FakeFS {
  std::shared_ptr<FileData> data = std::make_shared<FileData>();
  bool exists(const char*) const { return data->exists; }
  // ESP32 SPIFFS can return a truthy directory for a missing regular file.
  File open(const char*) { return File{data, 0, false, !data->exists}; }
} fs;
struct Stream {
  std::string output;
  std::vector<std::string> records;
  size_t capacity = 4096;
  size_t short_limit = 0;
  bool fail_once = false;
  int availableForWrite() const { return static_cast<int>(capacity); }
  size_t write(const uint8_t* data, size_t len) {
    if (fail_once) { fail_once = false; return 0; }
    if (len > capacity) return 0;
    if (short_limit != 0) len = std::min(len, short_limit);
    output.append(reinterpret_cast<const char*>(data), len);
    records.emplace_back(reinterpret_cast<const char*>(data), len);
    capacity -= len;
    return len;
  }
  size_t printf(const char* format, ...) {
    char record[1024];
    va_list args;
    va_start(args, format);
    int len = vsnprintf(record, sizeof(record), format, args);
    va_end(args);
    return len > 0 ? write(reinterpret_cast<const uint8_t*>(record), len) : 0;
  }
} console;
namespace mesh {
Stream& usbConsolePort() { return console; }
namespace Utils {
void toHex(char* dest, const uint8_t* src, size_t len) {
  static const char digits[] = "0123456789ABCDEF";
  for (size_t i = 0; i < len; ++i) {
    *dest++ = digits[src[i] >> 4];
    *dest++ = digits[src[i] & 15];
  }
  *dest = 0;
}
}
}
struct SimpleMeshTables {
  struct RecentRepeaterInfo {
    uint8_t prefix[3];
    uint8_t prefix_len;
    int8_t snr_x4;
  };
  std::vector<RecentRepeaterInfo> rows;
  int getRecentRepeaterCount() const { return static_cast<int>(rows.size()); }
  const RecentRepeaterInfo* getNextRecentRepeaterBySortKey(
      const RecentRepeaterInfo*, int previous, int& result) const {
    result = previous + 1;
    return result < static_cast<int>(rows.size()) ? &rows[result] : nullptr;
  }
};
class MyMesh {
 public:
  FakeFS* _fs = &fs;
  File serial_log_dump;
  size_t serial_log_remaining = 0;
  size_t serial_log_pending_size = 0;
  char serial_log_pending[640];
  bool serial_log_active = false;
  bool serial_log_eof_pending = false;
  bool serial_log_skip_line = false;
  int serial_recent_next = -1;
  int serial_recent_count = 0;
  bool serial_recent_header = false;
  bool serial_recent_has_cursor = false;
  SimpleMeshTables::RecentRepeaterInfo serial_recent_cursor{};
  int serial_recent_cursor_index = -1;
  SimpleMeshTables tables;
  const SimpleMeshTables* getTables() const { return &tables; }
  void dumpLogFile();
  bool hasPendingSerialOutput() const;
  void servicePendingSerialOutput();
  void cancelPendingSerialOutput();
  void printRecentRepeatersSerial();
};
@METHODS@
static void setupFile(const std::string& text, bool exists = true) {
  fs.data = std::make_shared<FileData>();
  fs.data->text = text;
  fs.data->exists = exists;
  console = Stream();
}
static void drain(MyMesh& radio, size_t capacity = 4096) {
  int passes = 0;
  while (radio.hasPendingSerialOutput() && passes++ < 10000) {
    console.capacity = capacity;
    const size_t reads = fs.data->reads;
    radio.servicePendingSerialOutput();
    require(fs.data->reads - reads <= 640, "unbounded file read pass");
  }
  require(!radio.hasPendingSerialOutput(), "output pump failed to finish");
}
int main() {
  try {
    const std::string eof = "  ->    EOF\r\n";
    for (bool exists : {false, true}) {
      setupFile("", exists);
      MyMesh radio;
      radio.dumpLogFile();
      require(exists || !radio.serial_log_active, "missing log accepted as a directory");
      require(console.output.empty(), "synchronous premature EOF");
      drain(radio);
      require(console.output == eof, "missing or duplicate empty-file EOF");
    }
    setupFile("hello\nworld\n");
    {
      MyMesh radio;
      radio.dumpLogFile();
      console.capacity = 0;
      radio.servicePendingSerialOutput();
      const size_t reads = fs.data->reads;
      radio.servicePendingSerialOutput();
      require(reads == fs.data->reads, "backpressure lost pending line");
      require(console.output.empty(), "wrote without capacity");
      console.short_limit = 3;
      console.capacity = 4096;
      radio.servicePendingSerialOutput();
      console.short_limit = 0;
      drain(radio);
      require(console.output == "hello\nworld\n" + eof, "short-write suffix lost");
    }
    std::string large;
    for (int i = 0; i < 1000; ++i) large += "stored packet\n";
    setupFile(large);
    {
      MyMesh radio;
      radio.dumpLogFile();
      fs.data->text += "arrived later\n";
      drain(radio);
      require(console.output == large + eof, "large dump lost bytes or ignored snapshot");
      for (const auto& record : console.records)
        require(!record.empty() && record.back() == '\n', "split stored record");
    }
    const std::string boundary(639, 'a');
    const std::string too_long(640, 'b');
    setupFile(boundary + "\n" + too_long + "\nend\ntail");
    {
      MyMesh radio;
      radio.dumpLogFile();
      drain(radio);
      require(console.output == boundary + "\n"
          "[USB log line omitted: exceeds 640 bytes]\r\nend\ntail\n" + eof,
          "long line boundary or final partial line is wrong");
    }
    setupFile(std::string(2000, 'x') + "\n");
    {
      MyMesh radio;
      radio.dumpLogFile();
      radio.servicePendingSerialOutput();
      require(radio.hasPendingSerialOutput(), "overlong line not pending");
      radio.cancelPendingSerialOutput();
      drain(radio);
      require(console.output.empty(), "canceled old-session output leaked");
    }
    @RECENT_TEST@
    std::cout << "cooperative output checks passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
}
'''

RECENT_TEST = r'''
    setupFile("");
    {
      MyMesh radio;
      for (int i = 0; i < 2048; ++i) {
        radio.tables.rows.push_back({{0, static_cast<uint8_t>(i >> 8),
            static_cast<uint8_t>(i)}, 3, -127});
      }
      console.fail_once = true;
      radio.printRecentRepeatersSerial();
      require(radio.serial_recent_header, "failed header write advanced cursor");
      drain(radio, 64);
      require(console.output.find("Recent repeaters (2048):\n") == 0, "missing header");
      require(std::count(console.output.begin(), console.output.end(), '\n') == 2049,
          "2048-row listing truncated");
      require(console.output.substr(console.output.size() - 14) == "0007FF,-31.75\n",
          "last recent repeater missing");
      for (const auto& record : console.records)
        require(!record.empty() && record.back() == '\n', "split recent row");
    }
'''


class CooperativeOutputTest(unittest.TestCase):
    def test_real_role_pumps(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A host C++ compiler is required for pump execution")
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                text = (ROOT / f"examples/{role}/MyMesh.cpp").read_text()
                dump_start = text.index("void MyMesh::dumpLogFile()")
                dump_end = text.index("\n#if MESH_ESP32_USB_CONSOLE_COOPERATIVE\nbool MyMesh::hasPendingSerialOutput", dump_start)
                pump_start = text.index("bool MyMesh::hasPendingSerialOutput()", dump_end)
                pump_end = text.index("\n#endif", pump_start)
                methods = text[dump_start:dump_end] + "\n" + text[pump_start:pump_end]
                if role == "simple_repeater":
                    format_start = text.index("static void formatLocalSnrX4(")
                    format_end = text.index("\nvoid MyMesh::formatRecentRepeatersReply", format_start)
                    recent_start = text.index("void MyMesh::printRecentRepeatersSerial()")
                    recent_end = text.index("\nbool MyMesh::setRecentRepeater(", recent_start)
                    methods = text[format_start:format_end] + "\n" + text[recent_start:recent_end] + "\n" + methods
                program = HARNESS.replace("@METHODS@", methods).replace(
                    "@RECENT_TEST@", RECENT_TEST if role == "simple_repeater" else ""
                )
                executable = Path(temporary) / ("pump.exe" if os.name == "nt" else "pump")
                build = subprocess.run(
                    [compiler, "-std=c++17", "-O0", "-I", str(ROOT / "src"),
                     "-x", "c++", "-", "-o", str(executable)],
                    input=program, text=True, capture_output=True, timeout=60,
                )
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                run = subprocess.run(
                    [str(executable)], text=True, capture_output=True, timeout=30,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
