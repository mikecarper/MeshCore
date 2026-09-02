#include <gtest/gtest.h>

#include <cstdarg>
#include <cstdio>
#include <string>

#define ARDUINO 1
#define NRF52_PLATFORM 1
#define MESH_DEBUG 1
#define BRIDGE_DEBUG 1
#define POWERSAVING_DEBUG 1
#include <MeshCore.h>

namespace {

// Match Adafruit nRF52 Print::printf(): it forwards vsnprintf()'s required
// length even when the fixed scratch buffer truncated the formatted output.
size_t nrf52CorePrintf(Print& target, const char* format, ...) {
  char output[256];
  va_list args;
  va_start(args, format);
  const int required = vsnprintf(output, sizeof(output), format, args);
  va_end(args);

  if (required <= 0) return 0;
  target.write(reinterpret_cast<const uint8_t*>(output),
               static_cast<size_t>(required));
  return static_cast<size_t>(required);
}

class CapacityStream : public Stream {
 public:
  void reset(int capacity) {
    capacity_ = capacity;
    available_calls_ = 0;
    write_calls_ = 0;
    flush_calls_ = 0;
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

  void flush() override { flush_calls_++; }

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
  int flush_calls_ = 0;
  bool oversized_write_ = false;
  bool reenter_on_write_ = false;
  Stream* reentrant_stream_ = nullptr;
  size_t reentrant_result_ = 0;
  std::string output_;
};

struct SingleAttemptContext {
  mesh::SingleAttemptNonBlockingStream* stream = nullptr;
  size_t result = 0;
  size_t calls = 0;
  size_t requested = 0;
  size_t nested_result = 99;
  bool reenter = false;
  bool accessible = true;
  bool try_exclusive_during_write = false;
  bool nested_exclusive_result = true;
};

void noOpExclusive(void*) {}

size_t tryWriteOnce(void* opaque, const uint8_t*, size_t size) {
  SingleAttemptContext* context =
      static_cast<SingleAttemptContext*>(opaque);
  context->calls++;
  context->requested = size;
  if (context->reenter && context->stream != nullptr) {
    context->reenter = false;
    const uint8_t nested = 'n';
    context->nested_result = context->stream->write(&nested, 1);
  }
  if (context->try_exclusive_during_write && context->stream != nullptr) {
    context->nested_exclusive_result =
        context->stream->tryRunExclusive(noOpExclusive);
  }
  return context->result;
}

bool canAccess(void* opaque) {
  return static_cast<SingleAttemptContext*>(opaque)->accessible;
}

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

TEST_F(Nrf52DebugOutputTest,
       DedicatedFacadeDropsExactScratchSizePrintfRecord) {
  mesh::WholeRecordNonBlockingStream<> nonblocking_port(logging_port);
  const std::string exact_scratch_size(256, 'x');

  EXPECT_EQ(nrf52CorePrintf(nonblocking_port, "%s",
                            exact_scratch_size.c_str()),
            exact_scratch_size.size());
  EXPECT_TRUE(logging_port.output_.empty());
  EXPECT_EQ(logging_port.available_calls_, 0);
  EXPECT_EQ(logging_port.write_calls_, 0);
}

TEST_F(Nrf52DebugOutputTest,
       DedicatedFacadeAcceptsFullSizeNonNulDirectRecord) {
  mesh::WholeRecordNonBlockingStream<> nonblocking_port(logging_port);
  const std::string full_size_record(256, 'x');

  EXPECT_EQ(nonblocking_port.write(
                reinterpret_cast<const uint8_t*>(full_size_record.data()),
                full_size_record.size()),
            full_size_record.size());
  EXPECT_EQ(logging_port.output_, full_size_record);
  EXPECT_EQ(logging_port.available_calls_, 1);
  EXPECT_EQ(logging_port.write_calls_, 1);
}

TEST_F(Nrf52DebugOutputTest,
       PartialIdentityWriteRestartsFromActualUsbTaskResult) {
  constexpr size_t marker_size = 80;
  size_t offset = 0;

  // The USB owner task observed that TinyUSB accepted only a prefix before the
  // first host disconnected.
  offset = mesh::detail::nextUsbLoggingIdentityOffset(
      offset, marker_size, 19);
  EXPECT_EQ(offset, 0U);

  // The next host must still be offered the complete marker.
  const size_t next_remaining = marker_size - offset;
  EXPECT_EQ(next_remaining, marker_size);
  offset = mesh::detail::nextUsbLoggingIdentityOffset(
      offset, next_remaining, next_remaining);
  EXPECT_EQ(offset, marker_size);
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

TEST_F(Nrf52DebugOutputTest, SingleAttemptFacadeNeverRetriesAShortWrite) {
  SingleAttemptContext context;
  context.result = 3;
  mesh::SingleAttemptNonBlockingStream port(logging_port, tryWriteOnce,
                                             &context);
  const uint8_t record[] = {'p', 'a', 'r', 't', 'i', 'a', 'l'};

  EXPECT_EQ(port.write(record, sizeof(record)), 3U);
  EXPECT_EQ(context.calls, 1U);
  EXPECT_EQ(context.requested, sizeof(record));
}

TEST_F(Nrf52DebugOutputTest,
       MotaFacadeDoesNotEmitAPrefixWhenTheWholeRequestCannotFit) {
  SingleAttemptContext context;
  mesh::SingleAttemptNonBlockingStream single_attempt(
      logging_port, tryWriteOnce, &context);
  mesh::AtomicWholeRecordNonBlockingStream<11> mota_port(single_attempt);
  const uint8_t request[] = {'M', 'S', 3, 1, 2, 3, 4, 5, 6, 7, 0x42};
  context.result = sizeof(request);

  logging_port.reset(sizeof(request) - 1);
  EXPECT_EQ(mota_port.write(request, sizeof(request)), 0U);
  EXPECT_EQ(context.calls, 0U);

  logging_port.reset(sizeof(request));
  context.reenter = true;
  context.stream = &single_attempt;
  EXPECT_EQ(mota_port.write(request, sizeof(request)), sizeof(request));
  EXPECT_EQ(context.calls, 1U);
  EXPECT_EQ(context.requested, sizeof(request));
  EXPECT_EQ(context.nested_result, 0U);
}

TEST_F(Nrf52DebugOutputTest, SingleAttemptFacadeNeverForwardsBlockingFlush) {
  SingleAttemptContext context;
  mesh::SingleAttemptNonBlockingStream port(logging_port, tryWriteOnce,
                                             &context);

  port.flush();

  EXPECT_EQ(logging_port.flush_calls_, 0);
  EXPECT_EQ(context.calls, 0U);
}

TEST_F(Nrf52DebugOutputTest, SingleAttemptFacadeHonorsTheSharedSessionGate) {
  SingleAttemptContext context;
  context.result = 3;
  context.accessible = false;
  mesh::SingleAttemptNonBlockingStream port(
      logging_port, tryWriteOnce, &context, canAccess);
  const uint8_t record[] = {'n', 'e', 'w'};

  EXPECT_EQ(port.availableForWrite(), 0);
  EXPECT_EQ(port.write(record, sizeof(record)), 0U);
  EXPECT_EQ(context.calls, 0U);

  context.accessible = true;
  EXPECT_EQ(port.write(record, sizeof(record)), sizeof(record));
  EXPECT_EQ(context.calls, 1U);
}

TEST_F(Nrf52DebugOutputTest, SingleAttemptFacadeDropsAReentrantWriter) {
  SingleAttemptContext context;
  context.result = 5;
  context.reenter = true;
  mesh::SingleAttemptNonBlockingStream port(logging_port, tryWriteOnce,
                                             &context);
  context.stream = &port;
  const uint8_t record[] = {'o', 'u', 't', 'e', 'r'};

  EXPECT_EQ(port.write(record, sizeof(record)), sizeof(record));
  EXPECT_EQ(context.calls, 1U);
  EXPECT_EQ(context.nested_result, 0U);
}

TEST_F(Nrf52DebugOutputTest,
       SingleAttemptFacadeExcludesAPurgeWhileAWriterIsActive) {
  SingleAttemptContext context;
  context.result = 1;
  context.try_exclusive_during_write = true;
  mesh::SingleAttemptNonBlockingStream port(logging_port, tryWriteOnce,
                                             &context, canAccess);
  context.stream = &port;
  const uint8_t value = 'x';

  EXPECT_EQ(port.write(&value, 1), 1U);
  EXPECT_FALSE(context.nested_exclusive_result);
  EXPECT_TRUE(port.tryRunExclusive(noOpExclusive));
}

TEST_F(Nrf52DebugOutputTest,
       BufferedFacadeRetainsACompleteMultiLineReplyAcrossShortDrains) {
  logging_port.reset(0);
  mesh::BufferedNonBlockingWriteStream<4096> port(logging_port);
  std::string expected;

  for (int i = 0; i < 80; ++i) {
    char line[48];
    const int length = snprintf(line, sizeof(line),
                                "  command-%02d --with-argument\r\n", i);
    ASSERT_GT(length, 0);
    expected.append(line, static_cast<size_t>(length));
    EXPECT_EQ(port.write(reinterpret_cast<const uint8_t*>(line),
                         static_cast<size_t>(length)),
              static_cast<size_t>(length));
  }
  ASSERT_GT(expected.size(), 256U);
  EXPECT_EQ(port.queuedByteCount(), expected.size());

  int drains = 0;
  while (port.queuedByteCount() != 0 && drains++ < 100) {
    logging_port.capacity_ = 37;
    EXPECT_LE(port.service(), 37U);
  }

  EXPECT_LT(drains, 100);
  EXPECT_EQ(port.queuedByteCount(), 0U);
  EXPECT_EQ(logging_port.output_, expected);
}

TEST_F(Nrf52DebugOutputTest,
       BufferedFacadeDropsAWholeRecordWhenItsBoundIsFull) {
  logging_port.reset(0);
  mesh::BufferedNonBlockingWriteStream<8> port(logging_port);
  const uint8_t first[] = {'1', '2', '3', '4', '5', '6'};
  const uint8_t dropped[] = {'7', '8', '9'};

  EXPECT_EQ(port.write(first, sizeof(first)), sizeof(first));
  EXPECT_EQ(port.write(dropped, sizeof(dropped)), 0U);
  EXPECT_EQ(port.queuedByteCount(), sizeof(first));

  logging_port.capacity_ = 8;
  EXPECT_EQ(port.service(), sizeof(first));
  EXPECT_EQ(logging_port.output_, "123456");
}

TEST_F(Nrf52DebugOutputTest,
       BufferedFacadeRejectsNrf52PrintfStaleLengthSafely) {
  logging_port.reset(0);
  mesh::BufferedNonBlockingWriteStream<4096> port(logging_port);
  const std::string oversized(600, 'x');

  EXPECT_EQ(nrf52CorePrintf(port, "%s", oversized.c_str()),
            oversized.size());
  EXPECT_EQ(port.queuedByteCount(), 0U);
  EXPECT_TRUE(logging_port.output_.empty());
}

TEST_F(Nrf52DebugOutputTest, BufferedFacadeDisconnectResetDropsStaleText) {
  logging_port.reset(0);
  mesh::BufferedNonBlockingWriteStream<64> port(logging_port);
  const uint8_t stale[] = {'o', 'l', 'd'};
  const uint8_t fresh[] = {'n', 'e', 'w'};

  EXPECT_EQ(port.write(stale, sizeof(stale)), sizeof(stale));
  port.discardPending();
  EXPECT_EQ(port.queuedByteCount(), 0U);
  EXPECT_EQ(port.write(fresh, sizeof(fresh)), sizeof(fresh));

  logging_port.capacity_ = 64;
  EXPECT_EQ(port.service(), sizeof(fresh));
  EXPECT_EQ(logging_port.output_, "new");
}

TEST_F(Nrf52DebugOutputTest,
       TaskOwnedFacadeNeverTouchesDelegateFromProducer) {
  mesh::TaskOwnedWriteStream<3, 16> port(logging_port);
  const uint8_t first[] = {'f', 'i', 'r', 's', 't'};
  const uint8_t second[] = {'n', 'e', 'x', 't'};

  EXPECT_EQ(port.write(first, sizeof(first)), sizeof(first));
  EXPECT_EQ(port.write(second, sizeof(second)), sizeof(second));
  EXPECT_EQ(port.queuedRecordCount(), 2U);
  EXPECT_TRUE(logging_port.output_.empty());
  EXPECT_EQ(logging_port.available_calls_, 0);
  EXPECT_EQ(logging_port.write_calls_, 0);

  EXPECT_EQ(port.drainOne(), sizeof(first));
  EXPECT_EQ(logging_port.output_, "first");
  EXPECT_EQ(port.queuedRecordCount(), 1U);
  EXPECT_EQ(port.drainOne(), sizeof(second));
  EXPECT_EQ(logging_port.output_, "firstnext");
  EXPECT_EQ(port.queuedRecordCount(), 0U);
}

TEST_F(Nrf52DebugOutputTest,
       TaskOwnedFacadeDropsWhenQueueIsFullAndRetriesDrainLater) {
  mesh::TaskOwnedWriteStream<3, 16> port(logging_port);
  const uint8_t first[] = {'1'};
  const uint8_t second[] = {'2'};
  const uint8_t dropped[] = {'3'};

  EXPECT_EQ(port.write(first, sizeof(first)), sizeof(first));
  EXPECT_EQ(port.write(second, sizeof(second)), sizeof(second));
  EXPECT_EQ(port.availableForWrite(), 0);
  EXPECT_EQ(port.write(dropped, sizeof(dropped)), 0U);

  logging_port.reset(0);
  EXPECT_EQ(port.drainOne(), 0U);
  EXPECT_EQ(port.queuedRecordCount(), 2U);
  EXPECT_EQ(logging_port.write_calls_, 0);

  logging_port.reset(16);
  EXPECT_EQ(port.drainOne(), sizeof(first));
  EXPECT_EQ(port.queuedRecordCount(), 1U);
  EXPECT_EQ(port.write(dropped, sizeof(dropped)), sizeof(dropped));
  EXPECT_EQ(port.queuedRecordCount(), 2U);
}

TEST_F(Nrf52DebugOutputTest, TaskOwnedFacadeDiscardsPendingFromOwnerTask) {
  mesh::TaskOwnedWriteStream<4, 16> port(logging_port);
  const uint8_t record[] = {'o', 'l', 'd'};

  EXPECT_EQ(port.write(record, sizeof(record)), sizeof(record));
  EXPECT_EQ(port.queuedRecordCount(), 1U);
  port.discardPending();
  EXPECT_EQ(port.queuedRecordCount(), 0U);
  EXPECT_EQ(port.drainOne(), 0U);
  EXPECT_TRUE(logging_port.output_.empty());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
