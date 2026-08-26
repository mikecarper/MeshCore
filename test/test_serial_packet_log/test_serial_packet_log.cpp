#include <gtest/gtest.h>

#include <cstring>
#include <string>

#include "helpers/SerialPacketLog.h"

namespace {

class FakePort : public Stream {
 public:
  explicit FakePort(size_t capacity = 64, size_t bytes_per_ms = 64)
      : _capacity(capacity),
        _free(capacity),
        _rate(bytes_per_ms),
        _last_ms(millis()) {}

  int availableForWrite() override {
    uint32_t now = millis();
    _free += (size_t)(now - _last_ms) * _rate;
    _last_ms = now;
    if (_free > _capacity) _free = _capacity;
    return (int)_free;
  }

  size_t write(const uint8_t* buffer, size_t size) override {
    if (size > _free) size = _free;
    _written.append(reinterpret_cast<const char*>(buffer), size);
    _free -= size;
    if (_written.size() >= _wedge_after) wedge();
    return size;
  }

  void wedge() {
    availableForWrite();
    _rate = 0;
    _free = 0;
  }
  void wedgeAfter(size_t bytes) { _wedge_after = bytes; }
  const std::string& written() const { return _written; }

 private:
  size_t _capacity;
  size_t _free;
  size_t _rate;
  size_t _wedge_after = (size_t)-1;
  uint32_t _last_ms;
  std::string _written;
};

class SerialPacketLogTest : public ::testing::Test {
 protected:
  void SetUp() override {
    g_mock_millis = 1000;
    mesh::serialLogDroppedCount() = 0;
    mesh::serialLogPortSeen() = false;
  }

  static void primePort() {
    FakePort port(4096);
    mesh::SerialLogLine<> line;
    line.printf("primed");
    ASSERT_TRUE(line.flush(port));
  }
};

TEST_F(SerialPacketLogTest, WritesWholeLineThroughSmallFifo) {
  FakePort port;
  uint8_t raw[40];
  for (size_t i = 0; i < sizeof(raw); i++) raw[i] = (uint8_t)i;

  mesh::SerialLogLine<> line;
  line.printf("RAW: ");
  line.hex(raw, sizeof(raw));
  EXPECT_TRUE(line.flush(port));
  EXPECT_NE(std::string::npos, port.written().find("000102"));
  EXPECT_EQ(0U, mesh::serialLogDroppedCount());
}

TEST_F(SerialPacketLogTest, DropsImmediatelyWhenHostStopsDraining) {
  primePort();
  FakePort port;
  port.wedge();

  uint32_t before = millis();
  mesh::SerialLogLine<> line;
  line.printf("RAW: 0102");
  EXPECT_FALSE(line.flush(port));
  EXPECT_EQ(before, millis());
  EXPECT_TRUE(port.written().empty());
  EXPECT_EQ(1U, mesh::serialLogDroppedCount());
}

TEST_F(SerialPacketLogTest, GivesUpWithinBudgetWhenHostWedgesMidLine) {
  primePort();
  FakePort port(64, 64);
  port.wedgeAfter(64);
  uint8_t raw[200];
  memset(raw, 0xA5, sizeof(raw));

  mesh::SerialLogLine<> line;
  line.hex(raw, sizeof(raw));
  uint32_t before = millis();
  EXPECT_FALSE(line.flush(port));
  uint32_t waited = millis() - before;
  EXPECT_GE(waited, (uint32_t)SERIAL_LOG_WRITE_BUDGET_MS);
  EXPECT_LE(waited, (uint32_t)SERIAL_LOG_WRITE_BUDGET_MS + 2);
  EXPECT_EQ(1U, mesh::serialLogDroppedCount());
}

TEST_F(SerialPacketLogTest, ReportsDropsAfterHostRecovers) {
  primePort();
  FakePort wedged;
  wedged.wedge();
  for (int i = 0; i < 3; i++) {
    mesh::SerialLogLine<> line;
    line.printf("RAW: 00");
    EXPECT_FALSE(line.flush(wedged));
  }

  FakePort recovered(4096);
  mesh::SerialLogLine<> line;
  line.printf("RAW: 01");
  EXPECT_TRUE(line.flush(recovered));
  EXPECT_EQ("DROP:3\r\nRAW: 01\r\n", recovered.written());
}

TEST_F(SerialPacketLogTest, IgnoresDropsBeforeAnyHostConnects) {
  FakePort unattached;
  unattached.wedge();
  mesh::SerialLogLine<> line;
  line.printf("RAW: 00");
  EXPECT_FALSE(line.flush(unattached));
  EXPECT_EQ(0U, mesh::serialLogDroppedCount());
}

TEST_F(SerialPacketLogTest, MarksTruncatedLines) {
  primePort();
  FakePort port(4096);
  uint8_t raw[64];
  memset(raw, 0x5A, sizeof(raw));

  mesh::SerialLogLine<32> line;
  line.printf("RAW: ");
  line.hex(raw, sizeof(raw));
  EXPECT_FALSE(line.flush(port));
  EXPECT_LE(port.written().size(), (size_t)32);
  EXPECT_EQ(1U, mesh::serialLogDroppedCount());
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
