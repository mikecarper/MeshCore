#include <gtest/gtest.h>

#include <deque>
#include <vector>

#include "helpers/ArduinoSerialInterface.h"

class BufferStream : public Stream {
public:
  std::deque<uint8_t> input;
  std::vector<uint8_t> output;

  void push(const char* data) {
    while (*data) input.push_back((uint8_t)*data++);
  }

  void push(const uint8_t* data, size_t len) {
    for (size_t i = 0; i < len; i++) input.push_back(data[i]);
  }

  int available() override { return (int)input.size(); }

  int read() override {
    if (input.empty()) return -1;
    uint8_t value = input.front();
    input.pop_front();
    return value;
  }

  size_t write(uint8_t value) override {
    output.push_back(value);
    return 1;
  }

  size_t write(const uint8_t* data, size_t len) override {
    output.insert(output.end(), data, data + len);
    return len;
  }
};

static const char START_TOKEN[] = "+++MESHCORE-TERM-START";
static const char SEEDER_TOKEN[] = "ota folder on";

TEST(SerialModeSwitch, RecognizesControlSequenceAcrossReads) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("+++MESHCORE-");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());

  stream.push("TERM-START\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.takeControlSequence());
  EXPECT_FALSE(interface.takeControlSequence());
  EXPECT_EQ(stream.available(), 1); // trailing CR belongs to the terminal
}

TEST(SerialModeSwitch, DoesNotScanInsideBinaryFrame) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const size_t token_len = strlen(START_TOKEN);
  uint8_t header[] = {'<', (uint8_t)token_len, 0};
  stream.push(header, sizeof(header));
  stream.push(START_TOKEN);

  EXPECT_EQ(interface.checkRecvFrame(frame), token_len);
  EXPECT_EQ(memcmp(frame, START_TOKEN, token_len), 0);
  EXPECT_FALSE(interface.takeControlSequence());
}

TEST(SerialModeSwitch, RecognizesSecondaryControlSequenceSeparately) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN, SEEDER_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("ota folder ");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());
  EXPECT_FALSE(interface.takeSecondaryControlSequence());

  stream.push("on\r\n");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());
  EXPECT_TRUE(interface.takeSecondaryControlSequence());
  EXPECT_FALSE(interface.takeSecondaryControlSequence());
  EXPECT_EQ(stream.available(), 2); // trailing CRLF belongs to the seeder
}

TEST(SerialModeSwitch, DoesNotScanSecondarySequenceInsideBinaryFrame) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN, SEEDER_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const size_t token_len = strlen(SEEDER_TOKEN);
  uint8_t header[] = {'<', (uint8_t)token_len, 0};
  stream.push(header, sizeof(header));
  stream.push(SEEDER_TOKEN);

  EXPECT_EQ(interface.checkRecvFrame(frame), token_len);
  EXPECT_EQ(memcmp(frame, SEEDER_TOKEN, token_len), 0);
  EXPECT_FALSE(interface.takeControlSequence());
  EXPECT_FALSE(interface.takeSecondaryControlSequence());
}

TEST(SerialModeSwitch, RecognizesControlSequenceAfterBinaryFrame) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const uint8_t input[] = {'<', 2, 0, 0xA5, 0x5A};
  stream.push(input, sizeof(input));
  stream.push(START_TOKEN);

  EXPECT_EQ(interface.checkRecvFrame(frame), 2u);
  EXPECT_EQ(frame[0], 0xA5);
  EXPECT_EQ(frame[1], 0x5A);
  EXPECT_FALSE(interface.takeControlSequence());

  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.takeControlSequence());
}

TEST(SerialModeSwitch, MismatchedPrefixDoesNotTrigger) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("+++MESHCORE-TERM-ST0P");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());

  stream.push(START_TOKEN);
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.takeControlSequence());
}

TEST(SerialModeSwitch, PassthroughLeavesInputAndSuppressesBinaryOutput) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  interface.setPassthroughMode(true);
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("help\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_EQ(stream.available(), 5);

  const uint8_t payload[] = {1, 2, 3};
  EXPECT_EQ(interface.writeFrame(payload, sizeof(payload)), sizeof(payload));
  EXPECT_TRUE(stream.output.empty());

  interface.setPassthroughMode(false);
  EXPECT_EQ(interface.writeFrame(payload, sizeof(payload)), sizeof(payload));
  ASSERT_EQ(stream.output.size(), 6u);
  EXPECT_EQ(stream.output[0], '>');
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
