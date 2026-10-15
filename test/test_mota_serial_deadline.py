#!/usr/bin/env python3
"""Run real native mOTA serial tests with actual production code and sanitizers."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method


ROOT = Path(__file__).resolve().parents[1]


class MotaSerialDeadlineTests(unittest.TestCase):
    def test_actual_nrf_ble_capacity_uses_current_ready_connection(self):
        serial = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        getter = method(serial, "uint16_t SerialBLEInterface::motaResponseCapacity()")
        harness = r'''
#include <cassert>
#include <cstdint>
#include "helpers/BleMotaProtocol.h"
struct BLEConnection {
  bool online = true;
  uint16_t mtu = 247;
  bool connected() const { return online; }
  uint16_t getMtu() const { return mtu; }
};
struct {
  BLEConnection connection;
  bool available = true;
  uint16_t last_handle = 0;
  BLEConnection* Connection(uint16_t handle) {
    last_handle = handle;
    return available ? &connection : nullptr;
  }
} Bluefruit;
struct SerialBLEInterface {
  bool ready = true;
  uint16_t _conn_handle = 77;
  struct { bool active = true; bool isActive() const { return active; } } _mota_stream;
  bool isMotaChannelReady() const { return ready; }
  uint16_t motaResponseCapacity();
};
@GETTER@
int main() {
  SerialBLEInterface port;
  for (uint16_t mtu : {uint16_t(247), uint16_t(185), uint16_t(179), uint16_t(23)}) {
    Bluefruit.connection.mtu = mtu;
    const uint16_t expected = mtu == 247 ? 197 : mtu - 3;
    assert(port.motaResponseCapacity() == expected);
    assert(Bluefruit.last_handle == 77);
  }
  Bluefruit.connection.mtu = 22;
  assert(port.motaResponseCapacity() == 0);
  Bluefruit.connection.mtu = 247;
  Bluefruit.connection.online = false;
  assert(port.motaResponseCapacity() == 0);
  Bluefruit.connection.online = true;
  Bluefruit.available = false;
  assert(port.motaResponseCapacity() == 0);
  Bluefruit.available = true;
  port.ready = false;
  assert(port.motaResponseCapacity() == 0);
  port.ready = true;
  port._mota_stream.active = false;
  assert(port.motaResponseCapacity() == 0);
}
'''.replace("@GETTER@", getter).replace("#include <cstdint>",
                                          "#include <cstdint>\n#include <initializer_list>")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "capacity.cpp"
            binary = Path(directory) / "capacity"
            cpp.write_text(harness)
            built = subprocess.run([
                "g++", "-std=c++17", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_native_serial_cases(self):
        native = (ROOT / "test/test_ota/test_ota_core.cpp").read_text()
        start = native.index("class FakeMotaSeederStream")
        end = native.index("class FakeFolderMotaSeederStream", start)
        source = r'''
#include <gtest/gtest.h>
#include <algorithm>
#include <array>
#include <vector>
#include <cstring>
#include <type_traits>
#include <utility>
#include "helpers/ota/MotaSourceSerial.h"
#include "helpers/BleMotaStream.h"
#include "helpers/ota/MotaSeederProto.h"
#include "helpers/ota/OtaByteIO.h"
#include "helpers/ota/FolderMotaStore.h"
using namespace mesh::ota;
'''
        source += native[start:end]
        source += r'''
class MtuMotaSource : public SerialMotaSource {
public:
  using SerialMotaSource::SerialMotaSource;
  uint16_t frame_capacity = 197;
  mutable unsigned checks = 0;
  unsigned disconnect_after = 0;
protected:
  uint16_t responseFrameCapacity() const override {
    if (disconnect_after && ++checks >= disconnect_after) return 0;
    return frame_capacity;
  }
};

TEST(MotaSourceMtu, RawRequestsFitOneActualAttPayload) {
  for (uint16_t mtu : {247, 185, 179, 23}) {
    resetArduinoMock();
    FakeMotaSeederStream stream;
    MtuMotaSource source(stream, MotaStreamWritePolicy::NoFlush);
    source.frame_capacity = std::min<uint16_t>(197, mtu - 3);
    std::array<uint8_t, 2048> bytes{};
    ASSERT_TRUE(source.read(0, 99, bytes.data(), bytes.size()));
    const uint16_t limit = source.frame_capacity - 5;
    EXPECT_EQ(stream.lengths.size(), (bytes.size() + limit - 1) / limit);
    uint32_t offset = 99;
    for (size_t i = 0; i < stream.lengths.size(); ++i) {
      EXPECT_EQ(stream.offsets[i], offset);
      EXPECT_LE(stream.lengths[i] + 5u, source.frame_capacity);
      offset += stream.lengths[i];
    }
    for (size_t i = 0; i < bytes.size(); ++i) EXPECT_EQ(bytes[i], uint8_t(99 + i));
    if (mtu == 247) EXPECT_EQ(stream.lengths.size(), 11u);
    if (mtu == 185) EXPECT_EQ(stream.lengths.size(), 12u);
    if (mtu == 179) EXPECT_EQ(stream.lengths.size(), 12u);
  }
}

TEST(MotaSourceMtu, DeflateRequestsIncludeSizeAndFrameOverheads) {
  for (uint16_t mtu : {247, 185, 179, 23}) {
    resetArduinoMock();
    FakeDeflateMotaSeederStream stream;
    stream.encoded.resize(2048);
    for (size_t i = 0; i < stream.encoded.size(); ++i) stream.encoded[i] = uint8_t(i * 17);
    MtuMotaSource source(stream, MotaStreamWritePolicy::NoFlush);
    source.frame_capacity = std::min<uint16_t>(197, mtu - 3);
    std::array<uint8_t, 2048> bytes{};
    uint16_t length = 0;
    ASSERT_TRUE(source.read_deflated_block(0, 7, bytes.data(), bytes.size(), &length));
    const uint16_t limit = source.frame_capacity - 7;
    EXPECT_EQ(stream.lengths.size(), 1u + (bytes.size() + limit - 1) / limit);
    EXPECT_EQ(stream.lengths[0], 0u); // bounded size query
    uint16_t offset = 0;
    for (size_t i = 1; i < stream.lengths.size(); ++i) {
      EXPECT_EQ(stream.offsets[i], offset);
      EXPECT_LE(stream.lengths[i] + 7u, source.frame_capacity);
      offset += stream.lengths[i];
    }
    EXPECT_EQ(length, bytes.size());
    EXPECT_EQ(0, memcmp(bytes.data(), stream.encoded.data(), bytes.size()));
  }
}

TEST(MotaSourceMtu, InvalidOrDisconnectedCapacityFailsBeforeNextRequest) {
  for (uint16_t invalid : {0, 1, 5, 7}) {
    FakeMotaSeederStream stream;
    MtuMotaSource source(stream, MotaStreamWritePolicy::NoFlush);
    source.frame_capacity = invalid;
    uint8_t byte = 0;
    EXPECT_FALSE(source.read(0, 0, &byte, 1));
    EXPECT_TRUE(stream.lengths.empty());
    uint16_t length = 99;
    EXPECT_FALSE(source.read_deflated_block(0, 0, &byte, 1, &length));
    EXPECT_EQ(length, 0u);
    EXPECT_TRUE(stream.lengths.empty());
  }
  FakeMotaSeederStream stream;
  MtuMotaSource source(stream, MotaStreamWritePolicy::NoFlush);
  source.disconnect_after = 2;
  std::array<uint8_t, 1024> bytes{};
  EXPECT_FALSE(source.read(0, 0, bytes.data(), bytes.size()));
  EXPECT_EQ(stream.lengths, (std::vector<uint16_t>{192}));
}

TEST(MotaSourceMtu, OversizedCapacityCannotIncreaseLegacyTransportMaximum) {
  FakeMotaSeederStream stream;
  MtuMotaSource source(stream, MotaStreamWritePolicy::NoFlush);
  source.frame_capacity = UINT16_MAX;
  std::array<uint8_t, 1024> bytes{};
  ASSERT_TRUE(source.read(0, 0, bytes.data(), bytes.size()));
  EXPECT_EQ(stream.lengths, (std::vector<uint16_t>{192, 192, 192, 192, 192, 64}));
}
'''
        source += r'''
int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
'''
        googletest = ROOT / ".pio/libdeps/native/googletest/googletest"
        self.assertTrue(googletest.is_dir(), "Native googletest dependency must be present")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "serial-deadline.cpp"
            cpp.write_text(source)
            for sanitizer in (False, True):
                with self.subTest(sanitizer=sanitizer):
                    binary = Path(directory) / ("serial-deadline-" + str(sanitizer))
                    flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                              "-fno-pie", "-no-pie"] if sanitizer else [])
                    build = subprocess.run([
                        "g++", "-std=c++17", "-pthread", *flags,
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        "-I", str(googletest / "include"), "-I", str(googletest),
                        str(cpp), str(ROOT / "src/helpers/ota/MotaSourceSerial.cpp"),
                        str(ROOT / "src/helpers/ota/FolderMotaStore.cpp"),
                        str(googletest / "src/gtest-all.cc"), "-o", str(binary)],
                        capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    run = subprocess.run([str(binary), "--gtest_brief=1"],
                                         capture_output=True, text=True, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
