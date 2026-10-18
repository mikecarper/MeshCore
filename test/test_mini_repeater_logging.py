#!/usr/bin/env python3
"""Keep constrained Mini Repeater builds compatible with the MQTT packet log.

Compile the production repeater RX method, Dispatcher TX method, packet wire
codec and bounded serial writer under all eight feature-gate combinations.
Hardware transport/radio are stand-ins; this does not qualify physical USB/RF.
"""

import configparser
import itertools
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <Arduino.h>
inline uint32_t micros() { return millis() * 1000U; }
#include <Packet.h>
#include <helpers/CarrierTools.h>
#include <helpers/ManagementFeatureConfig.h>
#include <helpers/PacketFileLoggingConfig.h>
#include <helpers/SerialPacketLog.h>
#include <algorithm>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

static_assert(MESH_PACKET_LOGGING == 1, "packet logging must remain compiled");
static_assert(MESH_PACKET_LOGGING_COMPACT == 1, "retain compact serial protocol");
static_assert(MESH_ENABLE_CARRIER_TOOLS == EXPECT_CARRIER, "exercise actual flag");
static_assert(MESH_ENABLE_MANAGEMENT_REPORTING == EXPECT_MANAGEMENT, "exercise actual flag");
static_assert(MESH_ENABLE_PACKET_FILE_LOGGING == EXPECT_FILE_LOGGING, "exercise actual flag");

struct Port : Stream {
  std::string output;
  bool stalled = false;
  size_t chunk = 7, wedge_after = std::numeric_limits<size_t>::max();
  int availableForWrite() override {
    return stalled || output.size() >= wedge_after ? 0 : int(chunk);
  }
  size_t write(const uint8_t* data, size_t size) override {
    if (!availableForWrite()) return 0;
    size = std::min(size, chunk);
    output.append(reinterpret_cast<const char*>(data), size);
    return size;
  }
} log_port;

namespace mesh {
bool logging_enabled = true;
bool isUsbLoggingEnabled() { return logging_enabled; }
Stream& usbLoggingPort() { return log_port; }

@PACKET_METHODS@

enum class RadioParamApplyResult { APPLIED, BUSY };
struct Profile { uint8_t cr = 5; };
struct Profiles {
  Profile value;
  const Profile& params(uint8_t) const { return value; }
};
struct Radio {
  bool send_ok = true;
  unsigned sends = 0;
  std::vector<uint8_t> raw;
  Profiles settings;
  RadioParamApplyResult prepareTransmitProfile(uint8_t, bool) {
    return RadioParamApplyResult::APPLIED;
  }
  Profiles* profiles() { return &settings; }
  uint32_t getEstAirtimeFor(int) { return 10; }
  bool setCodingRate(uint8_t) { return true; }
  bool startSendRaw(const uint8_t* bytes, int length) {
    ++sends;
    if (send_ok) raw.assign(bytes, bytes + length);
    return send_ok;
  }
  float getLastSNR() const { return 2.5f; }
  float getLastRSSI() const { return -92; }
};
struct Clock { uint32_t getMillis() const { return millis(); } };
struct Dispatcher {
  Packet* outbound = nullptr;
  Radio radio;
  Radio* _radio = &radio;
  Clock clock;
  Clock* _ms = &clock;
  uint8_t outbound_restore_cr = 0;
  uint32_t outbound_start = 0, outbound_expiry = 0;
  bool restoreOutboundTxOverrides() { return true; }
  bool isPacketRadioCurrent(const Packet*) const { return true; }
  uint8_t getDefaultTxCodingRate() const { return 5; }
  uint32_t futureMillis(uint32_t wait) const { return millis() + wait; }
  const char* getLogDateTime() const { return "unused compact timestamp"; }
  bool startOutboundTransmit();
};

@TX_METHOD@
} // namespace mesh

#define PACKET_LOG_FILE "/packet_log"
struct FakeFS {
  unsigned opens = 0, writes = 0, removes = 0;
  std::string output;
  void remove(const char* name) {
    assert(!std::strcmp(name, PACKET_LOG_FILE));
    ++removes;
  }
};
struct File {
  FakeFS* fs;
  explicit operator bool() const { return true; }
  void print(const char* text) { ++fs->writes; fs->output += text; }
  void printf(const char* format, ...) {
    char text[1024];
    va_list args;
    va_start(args, format);
    const int length = std::vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    assert(length >= 0 && size_t(length) < sizeof(text));
    ++fs->writes;
    fs->output += text;
  }
  void close() {}
};
struct Callbacks {
  virtual void setLoggingOn(bool enable) = 0;
  virtual void eraseLogFile() = 0;
};
inline uint8_t getRetryLogCodingRate(const mesh::Packet*, uint8_t fallback) { return fallback; }
inline uint16_t rxPowerSavingPreambleForParams(uint8_t, float) { return 16; }
struct MyMesh : Callbacks {
  bool _logging = false;
  FakeFS fs;
  FakeFS* _fs = &fs;
  mesh::Radio radio;
  mesh::Radio* _radio = &radio;
  unsigned refreshed = 0;
  uint8_t active_sf = 7;
  float active_bw = 125;
  File openAppend(const char* name) {
    assert(!std::strcmp(name, PACKET_LOG_FILE));
    ++fs.opens;
    return File{&fs};
  }
  const char* getLogDateTime() const { return "timestamp"; }
  uint8_t getDefaultTxCodingRate() const { return 5; }
  void formatFloodRetryPath(char* output, size_t size, const mesh::Packet*) {
    std::snprintf(output, size, "01");
  }
  bool formatFloodRetryHeard(char* output, size_t size, const mesh::Packet*) {
    std::snprintf(output, size, "02");
    return true;
  }
  void refreshFloodRetryReachability(const mesh::Packet*) { ++refreshed; }
  @INLINE_CALLBACKS@
  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len);
  void logRx(mesh::Packet* pkt, int len, float score);
  void logTx(mesh::Packet* pkt, int len);
  void logTxFail(mesh::Packet* pkt, int len);
  void onFloodRetryEvent(const char* event, const mesh::Packet* packet,
                        uint32_t delay_millis, uint8_t retry_attempt);
};
@RX_METHOD@
@FILE_METHODS@

static void reset() {
  log_port = Port{};
  mesh::logging_enabled = true;
  mesh::serialLogDroppedCount() = 0;
  mesh::serialLogPortSeen() = false;
  g_mock_millis = 100;
}
static std::string hex(const uint8_t* bytes, size_t size) {
  const char* digits = "0123456789ABCDEF";
  std::string output;
  for (size_t i = 0; i < size; ++i) {
    output += digits[bytes[i] >> 4];
    output += digits[bytes[i] & 15];
  }
  return output;
}
static mesh::Packet makePacket(bool maximum = false) {
  mesh::Packet packet;
  packet.header = (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT) | ROUTE_TYPE_TRANSPORT_DIRECT;
  packet.transport_codes[0] = 0x1234;
  packet.transport_codes[1] = 0xABCD;
  packet.setPathHashSizeAndCount(2, maximum ? 32 : 2);
  for (unsigned i = 0; i < packet.getPathByteLen(); ++i) packet.path[i] = uint8_t(i);
  packet.payload_len = maximum ? MAX_PACKET_PAYLOAD : 3;
  for (unsigned i = 0; i < packet.payload_len; ++i) packet.payload[i] = uint8_t(i ^ 0xAB);
  return packet;
}

int main() {
  MyMesh repeater;
  // Actual inline callbacks and persistent packet emitters remain independent
  // of live USB logging. The file-off control cannot be re-enabled at runtime.
  assert(!repeater._logging);
  repeater.setLoggingOn(true);
  assert(repeater._logging == bool(EXPECT_FILE_LOGGING));
  mesh::Packet stored = makePacket();
  repeater.logRx(&stored, 42, 0.25f);
  repeater.logTx(&stored, 42);
  repeater.logTxFail(&stored, 42);
  repeater.onFloodRetryEvent("good", &stored, 12, 1);
  assert(repeater.refreshed == 1); // Reachability is not a logging side effect.
  repeater.onFloodRetryEvent("good", nullptr, 12, 1);
  repeater.onFloodRetryEvent("failure", &stored, 12, 1);
  assert(repeater.refreshed == 1);
  repeater.eraseLogFile();
  if (EXPECT_FILE_LOGGING) {
    assert(repeater.fs.opens == 5 && repeater.fs.writes > 0 && repeater.fs.removes == 1);
    for (const char* marker : {": RX,", ": TX,", ": TX FAIL!", ": FLOOD RETRY good"})
      assert(repeater.fs.output.find(marker) != std::string::npos);
  } else {
    assert(repeater.fs.opens == 0 && repeater.fs.writes == 0 && repeater.fs.removes == 0);
    assert(repeater.fs.output.empty());
  }
  repeater.setLoggingOn(false);
  assert(!repeater._logging);
  const unsigned old_opens = repeater.fs.opens;
  repeater.logRx(&stored, 42, 0.25f);
  repeater.logTx(&stored, 42);
  repeater.logTxFail(&stored, 42);
  repeater.onFloodRetryEvent("good", &stored, 12, 1);
  assert(repeater.refreshed == 2 && repeater.fs.opens == old_opens);
  const uint8_t raw[] = {0x00, 0xAB, 0xFF};
  reset();
  repeater.logRxRaw(2.5f, -92, raw, sizeof(raw));
  assert(log_port.output == "R00ABFF\n");
  assert(g_mock_millis == 100 && mesh::serialLogDroppedCount() == 0);

  uint8_t maximum_rx[MAX_TRANS_UNIT];
  for (unsigned i = 0; i < sizeof(maximum_rx); ++i) maximum_rx[i] = uint8_t(i);
  reset();
  repeater.logRxRaw(0, -100, maximum_rx, sizeof(maximum_rx));
  assert(log_port.output == "R" + hex(maximum_rx, sizeof(maximum_rx)) + "\n");
  assert(log_port.output.size() == 2 + MAX_TRANS_UNIT * 2);

  for (const bool maximum : {false, true}) {
    mesh::Packet packet = makePacket(maximum);
    uint8_t wire[MAX_TRANS_UNIT];
    const size_t length = packet.writeTo(wire);
    assert(length == size_t(packet.getRawLength()));
    if (maximum) assert(length == 254);
    // Exercise real decoding/bounds too: feature gates must not alter packet
    // framing, transport codes, path bytes, payload or accepted wire lengths.
    mesh::Packet decoded;
    assert(decoded.readFrom(wire, uint8_t(length)));
    assert(decoded.header == packet.header && decoded.path_len == packet.path_len);
    assert(decoded.payload_len == packet.payload_len);
    assert(!std::memcmp(decoded.payload, packet.payload, packet.payload_len));
    assert(!decoded.readFrom(wire, 1));
    auto invalid = std::vector<uint8_t>(wire, wire + length);
    invalid[5] = 0xC1; // reserved four-byte path mode
    assert(!decoded.readFrom(invalid.data(), uint8_t(invalid.size())));

    reset();
    mesh::Dispatcher dispatcher;
    dispatcher.outbound = &packet;
    assert(dispatcher.startOutboundTransmit());
    assert(dispatcher.radio.raw == std::vector<uint8_t>(wire, wire + length));
    assert(log_port.output == "T" + hex(wire, length) + "\n");
    assert(mesh::serialLogDroppedCount() == 0);

    reset();
    mesh::logging_enabled = false;
    repeater.logRxRaw(0, -100, maximum_rx, sizeof(maximum_rx));
    assert(dispatcher.startOutboundTransmit());
    assert(log_port.output.empty());
    assert(dispatcher.radio.raw == std::vector<uint8_t>(wire, wire + length));

    reset();
    dispatcher.radio.send_ok = false;
    assert(!dispatcher.startOutboundTransmit());
    assert(log_port.output.empty()); // Never claim a TX which did not start.
  }

  // An initially undrained host must cost neither time nor a false drop.
  reset();
  log_port.stalled = true;
  repeater.logRxRaw(0, -100, raw, sizeof(raw));
  assert(log_port.output.empty() && g_mock_millis == 100);
  assert(mesh::serialLogDroppedCount() == 0);

  // Once a host was observed, refused RX and TX records count as lost. RF
  // transmission still proceeds; recovery exposes the existing DROP marker.
  reset();
  repeater.logRxRaw(0, -100, raw, sizeof(raw));
  log_port.output.clear();
  log_port.stalled = true;
  repeater.logRxRaw(0, -100, raw, sizeof(raw));
  mesh::Packet packet = makePacket();
  mesh::Dispatcher dispatcher;
  dispatcher.outbound = &packet;
  assert(dispatcher.startOutboundTransmit() && dispatcher.radio.sends == 1);
  assert(log_port.output.empty() && g_mock_millis == 100);
  assert(mesh::serialLogDroppedCount() == 2);
  log_port.stalled = false;
  repeater.logRxRaw(0, -100, raw, sizeof(raw));
  assert(log_port.output == "DROP:2\r\nR00ABFF\n");

  // Keep the bounded writer's existing mid-record stall behavior explicit.
  reset();
  mesh::serialLogPortSeen() = true;
  log_port.wedge_after = 14;
  repeater.logRxRaw(0, -100, maximum_rx, sizeof(maximum_rx));
  assert(log_port.output.size() == 14);
  assert(g_mock_millis - 100 <= SERIAL_LOG_WRITE_BUDGET_MS + 1);
  assert(mesh::serialLogDroppedCount() == 1);
  std::puts("PASS: production compact repeater RX/TX logging survives all feature gates");
}
'''


class MiniRepeaterLoggingTest(unittest.TestCase):
    def test_actual_raw_rx_tx_packet_codec_and_backpressure_with_all_gate_combinations(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        packet = (ROOT / "src/Packet.cpp").read_text()
        packet_methods = "\n".join(body(packet, signature) for signature in (
            "Packet::Packet()", "bool Packet::isValidPathLen(",
            "size_t Packet::writePath(", "int Packet::getRawLength() const",
            "uint8_t Packet::writeTo(", "bool Packet::readFrom("))
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        program = HARNESS.replace("@PACKET_METHODS@", packet_methods).replace(
            "@TX_METHOD@", body(dispatcher, "bool Dispatcher::startOutboundTransmit()"))
        program = program.replace("@RX_METHOD@", body(repeater, "void MyMesh::logRxRaw("))
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        program = program.replace("@INLINE_CALLBACKS@", "\n".join(body(header, signature) for signature in (
            "void setLoggingOn(", "void eraseLogFile()")))
        program = program.replace("@FILE_METHODS@", "\n".join(body(repeater, signature) for signature in (
            "void MyMesh::logRx(", "void MyMesh::logTx(", "void MyMesh::logTxFail(",
            "void MyMesh::onFloodRetryEvent(")))
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="mini-experiment-logging-") as directory:
            source, executable = Path(directory) / "test.cpp", Path(directory) / "test"
            source.write_text(program, encoding="ascii")
            for carrier, management, file_logging in itertools.product((0, 1), repeat=3):
                with self.subTest(carrier_tools=carrier, management_reporting=management,
                                  packet_file_logging=file_logging):
                    command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                               "-Wno-unused-parameter", "-Wno-unused-variable",
                               "-Wno-unused-but-set-variable", "-Wno-sign-compare", *sanitizers,
                               "-DARDUINO=1", "-DSTM32_PLATFORM=1", "-DMESH_DEBUG=0",
                               "-DMESH_PACKET_LOGGING=1", "-DMESH_PACKET_LOGGING_COMPACT=1",
                               f"-DMESH_ENABLE_CARRIER_TOOLS={carrier}",
                               f"-DMESH_ENABLE_MANAGEMENT_REPORTING={management}",
                               f"-DMESH_ENABLE_PACKET_FILE_LOGGING={file_logging}",
                               f"-DEXPECT_CARRIER={carrier}", f"-DEXPECT_MANAGEMENT={management}",
                               f"-DEXPECT_FILE_LOGGING={file_logging}",
                               "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                               str(source), "-o", str(executable)]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                    self.assertIn("PASS: production compact repeater RX/TX", tested.stdout)

    def test_baseline_storage_logging_host_cli_and_authentication_contracts_remain(self):
        config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        config.read(ROOT / "variants/wio-e5-mini/platformio.ini")
        variant = config["env:wio-e5-mini_repeater"]
        self.assertEqual(variant.getint("board_upload.maximum_size"), 245760)
        flags = variant["build_flags"]
        self.assertRegex(flags, r"-D\s+LFS_FLASH_TOTAL_SIZE=16384\b")
        self.assertRegex(flags, r"-D\s+MESH_PACKET_LOGGING_COMPACT=1\b")
        self.assertNotRegex(flags, r"-D\s+MESH_ENABLE_HOST_CLI=0\b")
        self.assertNotRegex(flags, r"-D\s+MESH_PACKET_LOGGING=0\b")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        self.assertIn("#ifndef MESH_ENABLE_HOST_CLI\n#define MESH_ENABLE_HOST_CLI 1\n#endif", header)
        file_config = (ROOT / "src/helpers/PacketFileLoggingConfig.h").read_text()
        self.assertRegex(file_config, r"#ifndef MESH_ENABLE_PACKET_FILE_LOGGING\s+"
                         r"#define MESH_ENABLE_PACKET_FILE_LOGGING 1\s+#endif")
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        dispatch = body(source, "void MyMesh::handleCommand(uint32_t sender_timestamp, ClientInfo* sender,")
        self.assertLess(dispatch.index("handleReplayResetCommand(sender, command, reply, usb_origin)"),
                        dispatch.index("_cli.handleCommand("))
        self.assertIn("if (sender != nullptr && sender_timestamp == 0) sender_timestamp = 1;", dispatch)
        receive = source[source.index("else if (type == PAYLOAD_TYPE_TXT_MSG && len > 5"):]
        self.assertIn("client->isAdmin() || client->isRegionMgr() || client->isFilterMgr()", receive)
        self.assertIn("sender_timestamp < client->last_timestamp && !cached_retry", receive)
        host_complete = body(source, "bool MyMesh::completeHostCliRequest(")
        self.assertIn("client_index < 0 || !acl.getClientByIdx(client_index)->isAdmin()", host_complete)
        host_reply = body(source, "bool MyMesh::handleHostCliSerialReply(")
        self.assertIn("parsed.request_id != deferred_cli_command.request_id", host_reply)
        self.assertIn("parsed.request_nonce != host_cli_nonce", host_reply)
        self.assertIn("else if (!host_cli_claimed)", host_reply)
        deferred = body(source, "void __attribute__((noinline)) MyMesh::processDeferredCliCommand()")
        self.assertIn("if (!client->isAdmin())", deferred)
        self.assertIn("self_id.sign(signature, (const uint8_t*)signed_content,", deferred)
        common = body((ROOT / "src/helpers/CommonCLI.cpp").read_text(), "void CommonCLI::handleCommand(")
        self.assertIn("mesh::handleUsbLoggingWatchdogCommand(", common)
        self.assertIn("_radio_profiles.handle(", common)
        self.assertIn('#endif\n    } else if (sender_timestamp == 0 && memcmp(command, "stats-packets",', common)


if __name__ == "__main__":
    unittest.main()
