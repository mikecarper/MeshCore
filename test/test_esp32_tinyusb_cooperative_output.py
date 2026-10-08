#!/usr/bin/env python3
"""Execute the real native-USB role output pumps with small host stubs."""

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
@USB_CONFIG@
#define MESH_ESP32_USB_CONSOLE_COOPERATIVE 1
#define MESH_USB_CONSOLE_COOPERATIVE 1
#define PUB_KEY_SIZE 32
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
  static void copyRecentRepeaterInfo(RecentRepeaterInfo& out,
                                     const RecentRepeaterInfo& in) { out = in; }
  static void copyRecentRepeaterPrefix(uint8_t* out,
                                       const RecentRepeaterInfo& in) {
    memcpy(out, in.prefix, sizeof(in.prefix));
  }
  int getRecentRepeaterCount() const { return static_cast<int>(rows.size()); }
  const RecentRepeaterInfo* getNextRecentRepeaterBySortKey(
      const RecentRepeaterInfo*, int previous, int& result) const {
    result = previous + 1;
    return result < static_cast<int>(rows.size()) ? &rows[result] : nullptr;
  }
};
struct FakeAcl {
  struct Client {
    struct { uint8_t pub_key[PUB_KEY_SIZE]; } id{};
    uint8_t permissions = 3;
  };
  std::vector<Client> clients;
  int getNumClients() const { return static_cast<int>(clients.size()); }
  Client* getClientByIdx(int index) { return &clients.at(index); }
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
  int serial_acl_next = -1;
  int serial_acl_count = 0;
  bool serial_acl_header = false;
  int serial_recent_next = -1;
  int serial_recent_count = 0;
  bool serial_recent_header = false;
  bool serial_recent_has_cursor = false;
  SimpleMeshTables::RecentRepeaterInfo serial_recent_cursor{};
  int serial_recent_cursor_index = -1;
  SimpleMeshTables tables;
  FakeAcl acl;
  const SimpleMeshTables* getTables() const { return &tables; }
  void dumpLogFile();
  void printAclSerial();
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
    // The real HWCDC setup falls back to these ring sizes after allocation
    // failure. A stored row larger than the ring must finish without omission.
    for (size_t capacity : {256u, 512u}) {
      const std::string stored = std::string(639, 's') + "\nnext\n";
      setupFile(stored);
      MyMesh radio;
      radio.dumpLogFile();
      console.capacity = 0;
      radio.servicePendingSerialOutput();
      require(console.output.empty(), "fallback dump wrote to a stalled host");
      drain(radio, capacity);
      require(console.output == stored + eof, "small TX ring wedged or truncated a stored line");
    }
    setupFile("short\n");
    {
      MyMesh radio;
      radio.dumpLogFile();
      console.short_limit = 3;
      drain(radio, 7);
      require(console.output == "short\n" + eof, "short-write EOF was duplicated or truncated");
    }
    setupFile("");
    {
      MyMesh radio;
      std::string expected = "ACL:\r\n";
      for (int i = 0; i < 32; ++i) {
        FakeAcl::Client client;
        std::fill(std::begin(client.id.pub_key), std::end(client.id.pub_key), i);
        radio.acl.clients.push_back(client);
        char key[PUB_KEY_SIZE * 2 + 1], line[80];
        mesh::Utils::toHex(key, client.id.pub_key, PUB_KEY_SIZE);
        snprintf(line, sizeof(line), "%02X %s\n", client.permissions, key);
        expected += line;
      }
      require(expected.size() > 2048, "ACL test does not exceed admission reserve");
      console.capacity = 256;  // Accepted with a nearly-full HWCDC/logging FIFO.
      radio.printAclSerial();
      require(console.output.empty(), "ACL is still emitted synchronously");
      radio.acl.clients.emplace_back();  // New arrivals must not extend the snapshot.
      console.capacity = 0;
      for (int i = 0; i < 1000; ++i) radio.servicePendingSerialOutput();
      require(console.output.empty(), "ACL wrote into a stalled host");
      require(radio.serial_acl_next == 0, "stalled header skipped an ACL row");
      console.fail_once = true;
      console.short_limit = 17;
      drain(radio, 73);
      require(console.output == expected, "full ACL lost or duplicated short-write bytes");
      require(!radio.hasPendingSerialOutput(), "ACL blocks the next command after completion");
    }
    setupFile("");
    {
      MyMesh radio;
      radio.acl.clients.resize(32);
      for (auto& client : radio.acl.clients) client.permissions = 0;
      radio.printAclSerial();
      drain(radio, 73);
      require(console.output == "ACL:\r\n", "deleted ACL clients were printed");
    }
    setupFile("");
    {
      MyMesh radio;
      radio.acl.clients.resize(1);
      std::fill(std::begin(radio.acl.clients[0].id.pub_key),
                std::end(radio.acl.clients[0].id.pub_key), 0xAA);
      radio.printAclSerial();
      radio.servicePendingSerialOutput();
      console.short_limit = 7;
      radio.servicePendingSerialOutput();
      require(radio.serial_log_pending_size > 0, "ACL short-write suffix was not retained");
      radio.cancelPendingSerialOutput();
      require(!radio.hasPendingSerialOutput(), "ACL cancellation leaves a pending job");
      console.output.clear();
      console.records.clear();
      console.short_limit = 0;
      radio.acl.clients.clear();
      radio.printAclSerial();
      drain(radio, 73);
      require(console.output == "ACL:\r\n", "old ACL row leaked after reconnect cancellation");
    }
    setupFile("existing file dump\n");
    {
      MyMesh radio;
      radio.dumpLogFile();
      console.capacity = 0;
      radio.servicePendingSerialOutput();
      const size_t pending = radio.serial_log_pending_size;
      radio.printAclSerial();
      require(radio.serial_log_pending_size == pending && radio.serial_acl_next == -1,
          "overlapping ACL command overwrote a pending file record");
      drain(radio);
      require(console.output == "existing file dump\n" + eof,
          "overlapping ACL command corrupted an existing output job");
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
      radio.tables.rows.push_back({{0, 0, 1}, 3, -127});
      radio.tables.rows.push_back({{0, 0, 2}, 3, -127});
      console.short_limit = 3;
      radio.printRecentRepeatersSerial();
      require(radio.serial_recent_header, "partial header advanced cursor");
      drain(radio, 7);
      require(console.output == "Recent repeaters (2):\n000001,-31.75\n000002,-31.75\n",
          "short-write recent listing duplicated or lost a prefix");
    }
    setupFile("");
    {
      MyMesh radio;
      radio.tables.rows.push_back({{0, 0, 1}, 3, -127});
      radio.printRecentRepeatersSerial();
      console.short_limit = 3;
      radio.servicePendingSerialOutput();
      require(radio.serial_log_pending_size > 0, "partial recent row was not retained");
      radio.tables.rows[0] = {{0, 0, 9}, 3, 0};
      console.short_limit = 0;
      drain(radio, 64);
      require(console.output == "Recent repeaters (1):\n000001,-31.75\n",
          "live table update changed a partially emitted recent row");
    }
    setupFile("");
    {
      MyMesh radio;
      radio.tables.rows.push_back({{0, 0, 1}, 3, -127});
      console.short_limit = 3;
      radio.printRecentRepeatersSerial();
      radio.cancelPendingSerialOutput();
      console.output.clear();
      console.short_limit = 0;
      radio.tables.rows.clear();
      radio.printRecentRepeatersSerial();
      drain(radio, 64);
      require(console.output == "Recent repeaters (0):\n-none-\r\n",
          "canceled recent suffix leaked into the next session");
    }
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
    def test_usb_acl_commands_route_to_the_cooperative_pump(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                text = (ROOT / f"examples/{role}/MyMesh.cpp").read_text()
                start = text.index('strcmp(command, "get acl") == 0) {')
                end = text.index("reply[0] = 0;", text.index("#endif", start))
                self.assertIn("printAclSerial();", text[start:end])
                self.assertNotIn("mesh::usbConsolePort().printf", text[start:end])

    def test_real_role_pumps(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A host C++ compiler is required for pump execution")
        for role, config in ((role, config)
                for role in ("simple_repeater", "simple_room_server")
                for config in ("MESH_ESP32_TINYUSB_NONBLOCKING", "MESH_ESP32_HWCDC_SESSION_GUARD")):
            with self.subTest(role=role, transport=config), tempfile.TemporaryDirectory() as temporary:
                text = (ROOT / f"examples/{role}/MyMesh.cpp").read_text()
                dump_start = text.index("void MyMesh::dumpLogFile()")
                dump_end = text.index("\n#if MESH_USB_CONSOLE_COOPERATIVE\nbool MyMesh::hasPendingSerialOutput", dump_start)
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
                ).replace("@USB_CONFIG@", f"#define {config} 1")
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
