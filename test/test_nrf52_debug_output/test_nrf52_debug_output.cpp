#include <gtest/gtest.h>

#include <string>

#define ARDUINO 1
#define NRF52_PLATFORM 1
#define MESH_DEBUG 1
#define BRIDGE_DEBUG 1
#define POWERSAVING_DEBUG 1
#include <MeshCore.h>

namespace {

class CapacityStream : public Stream {
 public:
  void reset(int capacity) {
    capacity_ = capacity;
    available_calls_ = 0;
    write_calls_ = 0;
    oversized_write_ = false;
    reenter_on_write_ = false;
    reentrant_stream_ = nullptr;
    reentrant_result_ = 0;
    output_.clear();
  }

  int availableForWrite() override {
    available_calls_++;
    return capacity_;
  }

  size_t write(const uint8_t* buffer, size_t size) override {
    write_calls_++;
    if (size > static_cast<size_t>(capacity_)) {
      oversized_write_ = true;
      return 0;
    }
    if (reenter_on_write_) {
      reenter_on_write_ = false;
      if (reentrant_stream_ != nullptr) {
        static const uint8_t nested[] = {'n', 'e', 's', 't', 'e', 'd'};
        reentrant_result_ = reentrant_stream_->write(nested, sizeof(nested));
      } else {
        MESH_DEBUG_PRINTLN("nested record must be dropped");
      }
    }
    output_.append(reinterpret_cast<const char*>(buffer), size);
    capacity_ -= static_cast<int>(size);
    return size;
  }

  int capacity_ = 0;
  int available_calls_ = 0;
  int write_calls_ = 0;
  bool oversized_write_ = false;
  bool reenter_on_write_ = false;
  Stream* reentrant_stream_ = nullptr;
  size_t reentrant_result_ = 0;
  std::string output_;
};

CapacityStream logging_port;
mesh::WholeRecordNonBlockingStream<> bounded_logging_port(logging_port);
bool logging_enabled = true;

}  // namespace

const char* getLogDateTime() {
  return "12:34";
}

namespace mesh {

bool isUsbLoggingEnabled() {
  return logging_enabled;
}

Stream& usbLoggingPort() {
  return bounded_logging_port;
}

}  // namespace mesh

class Nrf52DebugOutputTest : public testing::Test {
 protected:
  void SetUp() override {
    logging_enabled = true;
    logging_port.reset(256);
  }
};

TEST_F(Nrf52DebugOutputTest, WritesACompleteFormattedRecordWhenItFits) {
  MESH_DEBUG_PRINTLN("value=%d", 42);

  EXPECT_EQ(logging_port.output_, "DEBUG: value=42\n");
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 1);
  EXPECT_FALSE(logging_port.oversized_write_);
}

TEST_F(Nrf52DebugOutputTest, DropsTheWholeRecordWhenFifoCannotFitIt) {
  logging_port.reset(10);

  MESH_DEBUG_PRINTLN("this record is longer than ten bytes");

  EXPECT_TRUE(logging_port.output_.empty());
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 0);
  EXPECT_FALSE(logging_port.oversized_write_);
}

TEST_F(Nrf52DebugOutputTest, AnUndrainedFullFifoNeverCallsTheWriter) {
  logging_port.reset(0);

  MESH_DEBUG_PRINTLN("must not wait");

  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 0);
}

TEST_F(Nrf52DebugOutputTest, LongLinesAreBoundedAndVisiblyTruncated) {
  const std::string payload(600, 'x');

  MESH_DEBUG_PRINTLN("%s", payload.c_str());

  ASSERT_EQ(logging_port.output_.size(), 255U);
  EXPECT_EQ(logging_port.output_.substr(0, 7), "DEBUG: ");
  EXPECT_EQ(logging_port.output_.substr(logging_port.output_.size() - 4),
            "...\n");
  EXPECT_EQ(logging_port.write_calls_, 1);
  EXPECT_FALSE(logging_port.oversized_write_);
}

TEST_F(Nrf52DebugOutputTest, DisabledLoggingDoesNotTouchThePort) {
  logging_enabled = false;

  MESH_DEBUG_PRINTLN("disabled");

  EXPECT_EQ(logging_port.available_calls_, 0);
  EXPECT_EQ(logging_port.write_calls_, 0);
}

TEST_F(Nrf52DebugOutputTest, ReentrantDebugOutputIsDroppedWithoutWaiting) {
  logging_port.reenter_on_write_ = true;

  MESH_DEBUG_PRINTLN("outer record");

  EXPECT_EQ(logging_port.output_, "DEBUG: outer record\n");
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 1);
}

TEST_F(Nrf52DebugOutputTest, BridgeAndPowerDebugUseTheSameBoundedPath) {
  BRIDGE_DEBUG_PRINTLN("started\n");
  POWERSAVING_DEBUG_PRINTLN("sleeping");

  EXPECT_EQ(logging_port.output_,
            "12:34 BRIDGE: started\nPOWERSAVING: sleeping\n");
  EXPECT_EQ(logging_port.write_calls_, 2);
  EXPECT_FALSE(logging_port.oversized_write_);
}

TEST_F(Nrf52DebugOutputTest, DedicatedFacadeRejectsAStalePrintfLength) {
  mesh::WholeRecordNonBlockingStream<> nonblocking_port(logging_port);
  const std::string oversized(600, 'x');

  const size_t written = nonblocking_port.write(
      reinterpret_cast<const uint8_t*>(oversized.data()), oversized.size());

  EXPECT_EQ(written, 0U);
  EXPECT_EQ(logging_port.available_calls_, 0);
  EXPECT_EQ(logging_port.write_calls_, 0);
}

TEST_F(Nrf52DebugOutputTest, DedicatedFacadeChecksThenWritesOnce) {
  mesh::WholeRecordNonBlockingStream<> nonblocking_port(logging_port);
  const uint8_t record[] = {'o', 'k', '\n'};
  logging_port.reset(sizeof(record));

  EXPECT_EQ(nonblocking_port.write(record, sizeof(record)), sizeof(record));
  EXPECT_EQ(logging_port.output_, "ok\n");
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 1);
}

TEST_F(Nrf52DebugOutputTest, DedicatedFacadeDropsAReentrantWriter) {
  mesh::WholeRecordNonBlockingStream<> nonblocking_port(logging_port);
  const uint8_t outer[] = {'o', 'u', 't', 'e', 'r'};
  logging_port.reenter_on_write_ = true;
  logging_port.reentrant_stream_ = &nonblocking_port;

  EXPECT_EQ(nonblocking_port.write(outer, sizeof(outer)), sizeof(outer));
  EXPECT_EQ(logging_port.output_, "outer");
  EXPECT_EQ(logging_port.reentrant_result_, 0U);
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 1);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
