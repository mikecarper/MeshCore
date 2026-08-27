#include <gtest/gtest.h>

#include <algorithm>
#include <deque>
#include <limits>
#include <vector>

#include "helpers/ArduinoSerialInterface.h"
#include "helpers/MultiSerialInterface.h"
#include "helpers/UsbAsciiBinarySwitch.h"

class BufferStream : public Stream {
public:
  std::deque<uint8_t> input;
  std::vector<uint8_t> output;
  int write_capacity = 4096;
  size_t max_write = std::numeric_limits<size_t>::max();

  void push(const char* data) {
    while (*data) input.push_back((uint8_t)*data++);
  }

  void push(const uint8_t* data, size_t len) {
    for (size_t i = 0; i < len; i++) input.push_back(data[i]);
  }

  int available() override { return (int)input.size(); }
  int availableForWrite() override { return write_capacity; }

  int read() override {
    if (input.empty()) return -1;
    uint8_t value = input.front();
    input.pop_front();
    return value;
  }

  int peek() override { return input.empty() ? -1 : input.front(); }

  size_t write(uint8_t value) override {
    return write(&value, 1);
  }

  size_t write(const uint8_t* data, size_t len) override {
    size_t accepted = std::min(len, max_write);
    accepted = std::min(accepted, (size_t)std::max(write_capacity, 0));
    output.insert(output.end(), data, data + accepted);
    write_capacity -= (int)accepted;
    return accepted;
  }
};

static const char START_TOKEN[] = "+++MESHCORE-TERM-START";
static const char SEEDER_TOKEN[] = "ota folder on";

class FakeSerialInterface : public BaseSerialInterface {
public:
  bool enabled = false;
  bool connected = false;
  bool pairing_request = false;
  bool write_busy = false;
  std::deque<std::vector<uint8_t>> received_frames;
  std::vector<std::vector<uint8_t>> sent_frames;

  void enable() override { enabled = true; }
  void disable() override { enabled = false; }
  bool isEnabled() const override { return enabled; }
  bool isConnected() const override { return connected; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return write_busy; }
  bool takePairingRequest() override {
    bool pending = pairing_request;
    pairing_request = false;
    return pending;
  }
  size_t writeFrame(const uint8_t src[], size_t len) override {
    sent_frames.emplace_back(src, src + len);
    return len;
  }
  size_t checkRecvFrame(uint8_t dest[]) override {
    if (received_frames.empty()) return 0;
    const std::vector<uint8_t> frame = received_frames.front();
    received_frames.pop_front();
    memcpy(dest, frame.data(), frame.size());
    return frame.size();
  }
};

TEST(MultiSerialInterface, TracksBluetoothConnectionSeparately) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface bluetooth;
  usb.connected = true;

  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::Bluetooth, &bluetooth));
  manager.enable();

  EXPECT_TRUE(manager.isConnected());
  EXPECT_FALSE(manager.isBluetoothConnected());

  bluetooth.connected = true;
  EXPECT_TRUE(manager.isBluetoothConnected());

  manager.disableBluetooth();
  EXPECT_FALSE(manager.isBluetoothConnected());
}

TEST(MultiSerialInterface, PairingRequestsComeOnlyFromBluetooth) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface bluetooth;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::Bluetooth, &bluetooth));
  manager.enable();

  usb.pairing_request = true;
  EXPECT_FALSE(manager.takePairingRequest());

  bluetooth.pairing_request = true;
  EXPECT_TRUE(manager.takePairingRequest());
  EXPECT_FALSE(manager.takePairingRequest());
}

TEST(MultiSerialInterface, RoutesRequiredRepliesToTheirRequestingInterface) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface wifi;
  usb.connected = true;
  wifi.connected = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::WiFi, &wifi));
  manager.enable();

  usb.received_frames.push_back({0x01});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);

  const uint8_t response[] = {0x05, 0xAA};
  EXPECT_EQ(manager.writeFrame(response, sizeof(response)), sizeof(response));
  ASSERT_EQ(usb.sent_frames.size(), 1u);
  EXPECT_TRUE(wifi.sent_frames.empty());

  // Login/status-style pushes complete a client operation and follow the same
  // requester route rather than exposing the result on another transport.
  const uint8_t required_push[] = {0x85, 0xBB};
  EXPECT_EQ(manager.writeFrame(required_push, sizeof(required_push)),
            sizeof(required_push));
  ASSERT_EQ(usb.sent_frames.size(), 2u);
  EXPECT_TRUE(wifi.sent_frames.empty());

  // Passive observations remain visible to every enabled client.
  const uint8_t best_effort_push[] = {0x80, 0xCC};
  EXPECT_EQ(manager.writeFrame(best_effort_push, sizeof(best_effort_push)),
            sizeof(best_effort_push));
  ASSERT_EQ(usb.sent_frames.size(), 3u);
  ASSERT_EQ(wifi.sent_frames.size(), 1u);
}

TEST(MultiSerialInterface, LocksMultiFrameRepliesToOneRequester) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface wifi;
  usb.connected = true;
  wifi.connected = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::WiFi, &wifi));
  manager.enable();

  usb.received_frames.push_back({0x04});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  manager.lockReplyRoute();

  wifi.received_frames.push_back({0x16});
  EXPECT_EQ(manager.checkRecvFrame(command), 0u);
  EXPECT_EQ(wifi.received_frames.size(), 1u);

  const uint8_t contact[] = {0x03, 0x42};
  EXPECT_EQ(manager.writeFrame(contact, sizeof(contact)), sizeof(contact));
  ASSERT_EQ(usb.sent_frames.size(), 1u);
  EXPECT_TRUE(wifi.sent_frames.empty());

  manager.unlockReplyRoute();
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  EXPECT_EQ(command[0], 0x16);
  const uint8_t device_info[] = {0x0D, 0x43};
  EXPECT_EQ(manager.writeFrame(device_info, sizeof(device_info)),
            sizeof(device_info));
  ASSERT_EQ(wifi.sent_frames.size(), 1u);
}

TEST(MultiSerialInterface, LosingLockedRequesterCannotFallBackToBroadcast) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface bluetooth;
  usb.connected = true;
  bluetooth.connected = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::Bluetooth, &bluetooth));
  manager.enable();

  bluetooth.received_frames.push_back({0x04});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  manager.lockReplyRoute();
  manager.disableBluetooth();

  EXPECT_FALSE(manager.isConnected());
  const uint8_t contact[] = {0x03, 0x42};
  EXPECT_EQ(manager.writeFrame(contact, sizeof(contact)), 0u);
  EXPECT_TRUE(usb.sent_frames.empty());

  manager.unlockReplyRoute();
  EXPECT_TRUE(manager.isConnected());
}

TEST(MultiSerialInterface, PacesOnlyTheActiveReplyTransport) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface wifi;
  usb.connected = true;
  wifi.connected = true;
  wifi.write_busy = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::WiFi, &wifi));
  manager.enable();

  usb.received_frames.push_back({0x04});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  EXPECT_FALSE(manager.isWriteBusy());

  wifi.received_frames.push_back({0x04});
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  EXPECT_TRUE(manager.isWriteBusy());
}

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
  EXPECT_EQ(stream.available(), 0); // delimiter is part of the control line
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
  EXPECT_EQ(stream.available(), 1); // CR is consumed; LF stays for the seeder
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

  stream.push("\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.takeControlSequence());
}

TEST(SerialModeSwitch, ControlSequenceRequiresAnExactBoundedLine) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("prefix+++MESHCORE-TERM-START\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());

  stream.push("+++MESHCORE-TERM-STARTsuffix\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());

  stream.push("+++MESHCORE-TERM-ST0P\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());

  stream.push("+++MESHCORE-TERM-START");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_FALSE(interface.takeControlSequence());
  stream.push("\r");
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.takeControlSequence());
}

TEST(SerialModeSwitch, NewlineStartsAFreshControlLine) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  stream.push("noise\r+++MESHCORE-TERM-START\n");
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

TEST(SerialModeSwitch, AsciiStartupHandsUntouchedFrameToBinaryParser) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  interface.setPassthroughMode(true);
  mesh::UsbBinaryStartupProbe probe;
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const uint8_t input[] = {'<', 2, 0, 0x16, 0x03};
  stream.push(input, sizeof(input));
  ASSERT_TRUE(probe.shouldStart(true, false, stream.peek()));
  const uint32_t before = interface.getCompletedFrameCount();
  interface.setPassthroughMode(false);
  probe.start(50, before);

  ASSERT_EQ(interface.checkRecvFrame(frame), 2u);
  EXPECT_EQ(frame[0], 0x16); // CMD_DEVICE_QUERY
  EXPECT_EQ(frame[1], 0x03);
  EXPECT_EQ(probe.poll(50, interface.getCompletedFrameCount(), 50),
            mesh::UsbBinaryStartupProbe::Result::BINARY_CONFIRMED);
}

TEST(SerialModeSwitch, AsciiStartupProbeRequiresAnEmptyPrompt) {
  mesh::UsbBinaryStartupProbe probe;
  EXPECT_TRUE(probe.shouldStart(true, false, '<'));
  EXPECT_FALSE(probe.shouldStart(false, false, '<'));
  EXPECT_FALSE(probe.shouldStart(true, true, '<'));
  EXPECT_FALSE(probe.shouldStart(true, false, 'h'));
  EXPECT_FALSE(probe.shouldStart(true, false, -1));
}

TEST(SerialModeSwitch, IncompleteBinaryProbeReturnsToAsciiAfterTimeout) {
  mesh::UsbBinaryStartupProbe probe;
  probe.start(0xFFFFFFF0u, 7);
  EXPECT_EQ(probe.poll(0xFFFFFFF0u + 999u, 7),
            mesh::UsbBinaryStartupProbe::Result::WAITING);
  EXPECT_EQ(probe.poll(0xFFFFFFF0u + 1000u, 7),
            mesh::UsbBinaryStartupProbe::Result::RETURN_TO_ASCII);
  EXPECT_FALSE(probe.isActive());
}

TEST(SerialModeSwitch, BinaryProbeUsesTheFrameCompletionDeadline) {
  mesh::UsbBinaryStartupProbe probe;
  probe.start(100, 4);
  EXPECT_EQ(probe.poll(1200, 5, 1099),
            mesh::UsbBinaryStartupProbe::Result::BINARY_CONFIRMED);

  probe.start(100, 5);
  EXPECT_EQ(probe.poll(1100, 6, 1100),
            mesh::UsbBinaryStartupProbe::Result::RETURN_TO_ASCII);
}

TEST(SerialModeSwitch, BinaryProbeTimeoutCheckHandlesMillisRollover) {
  mesh::UsbBinaryStartupProbe probe;
  probe.start(0xFFFFFFF0u, 1);
  EXPECT_FALSE(probe.hasTimedOut(0xFFFFFFF0u + 999u));
  EXPECT_TRUE(probe.hasTimedOut(0xFFFFFFF0u + 1000u));
}

TEST(SerialModeSwitch, TcpCanBorrowOnlyAnIdleUnopenedAsciiTerminal) {
  mesh::UsbTcpTerminalHandoff handoff;
  EXPECT_FALSE(handoff.begin(true, true, true, 10));
  EXPECT_FALSE(handoff.begin(true, false, false, 10));
  EXPECT_TRUE(handoff.begin(true, false, true, 10));
  EXPECT_TRUE(handoff.isBorrowingAscii());
  EXPECT_TRUE(handoff.shouldRestoreAscii(10));
  EXPECT_FALSE(handoff.shouldRestoreAscii(10));
}

TEST(SerialModeSwitch, UsbBinaryActivityWinsDuringTcpBorrow) {
  mesh::UsbTcpTerminalHandoff handoff;
  ASSERT_TRUE(handoff.begin(true, false, true, 20));
  EXPECT_FALSE(handoff.shouldRestoreAscii(21));

  ASSERT_TRUE(handoff.begin(false, true, false, 21));
  EXPECT_FALSE(handoff.isBorrowingAscii());
  EXPECT_FALSE(handoff.shouldRestoreAscii(21));
}

TEST(SerialModeSwitch, FailedMotaRestoresOnlyItsAsciiOrigin) {
  EXPECT_TRUE(mesh::shouldRestoreAsciiAfterMotaFailure(
      mesh::UsbMotaEntryOrigin::ASCII));
  EXPECT_FALSE(mesh::shouldRestoreAsciiAfterMotaFailure(
      mesh::UsbMotaEntryOrigin::BINARY));
}

TEST(SerialFlowControl, KeepsAFrameQueuedUntilUsbHasSpace) {
  BufferStream stream;
  stream.write_capacity = 0;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enableFlowControl(true);
  interface.enable();

  const uint8_t payload[] = {0x05, 0xA5, 0x5A};
  EXPECT_EQ(interface.writeFrame(payload, sizeof(payload)), sizeof(payload));
  EXPECT_TRUE(stream.output.empty());
  EXPECT_TRUE(interface.hasPendingIO());
  EXPECT_TRUE(interface.isWriteBusy());

  stream.write_capacity = MAX_FRAME_SIZE + 3;
  interface.loop();
  const std::vector<uint8_t> expected = {'>', 3, 0, 0x05, 0xA5, 0x5A};
  EXPECT_EQ(stream.output, expected);
  EXPECT_FALSE(interface.hasPendingIO());
}

TEST(SerialFlowControl, FinishesAnUnexpectedShortWriteBeforeNextFrame) {
  BufferStream stream;
  stream.write_capacity = MAX_FRAME_SIZE + 3;
  stream.max_write = 2;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enableFlowControl(true);
  interface.enable();

  const uint8_t first[] = {0x05, 0x11, 0x22};
  const uint8_t second[] = {0x00};
  EXPECT_EQ(interface.writeFrame(first, sizeof(first)), sizeof(first));
  ASSERT_EQ(stream.output.size(), 2u);
  EXPECT_EQ(interface.writeFrame(second, sizeof(second)), sizeof(second));

  stream.max_write = std::numeric_limits<size_t>::max();
  interface.loop();
  const std::vector<uint8_t> expected = {
      '>', 3, 0, 0x05, 0x11, 0x22,
      '>', 1, 0, 0x00};
  EXPECT_EQ(stream.output, expected);
  EXPECT_FALSE(interface.hasPendingIO());
}

TEST(SerialFlowControl, DrainsFramesThroughAFifoSmallerThanTheFrame) {
  BufferStream stream;
  stream.write_capacity = 4;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enableFlowControl(true);
  interface.enable();

  const uint8_t payload[] = {0x05, 1, 2, 3, 4, 5};
  EXPECT_EQ(interface.writeFrame(payload, sizeof(payload)), sizeof(payload));
  ASSERT_EQ(stream.output.size(), 4u);
  EXPECT_TRUE(interface.hasPendingIO());

  stream.write_capacity = 4;
  interface.loop();
  ASSERT_EQ(stream.output.size(), 8u);
  EXPECT_TRUE(interface.hasPendingIO());

  stream.write_capacity = 4;
  interface.loop();
  const std::vector<uint8_t> expected = {'>', 6, 0, 0x05, 1, 2, 3, 4, 5};
  EXPECT_EQ(stream.output, expected);
  EXPECT_FALSE(interface.hasPendingIO());
}

TEST(SerialFlowControl, PartialInboundFrameKeepsTransportBusy) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const uint8_t partial[] = {'<', 2};
  stream.push(partial, sizeof(partial));
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.isReadBusy());
  EXPECT_TRUE(interface.hasPendingIO());

  const uint8_t remainder[] = {0, 0xA5, 0x5A};
  stream.push(remainder, sizeof(remainder));
  EXPECT_EQ(interface.checkRecvFrame(frame), 2u);
  EXPECT_FALSE(interface.isReadBusy());
  EXPECT_EQ(frame[0], 0xA5);
  EXPECT_EQ(frame[1], 0x5A);
}

TEST(SerialFlowControl, AbandonsATruncatedInboundFrameAfterTimeout) {
  resetArduinoMock();
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enable();
  uint8_t frame[MAX_FRAME_SIZE] = {};

  const uint8_t partial[] = {'<', 2, 0, 0xA5};
  stream.push(partial, sizeof(partial));
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  EXPECT_TRUE(interface.isReadBusy());

  delay(999);
  interface.loop();
  EXPECT_TRUE(interface.isReadBusy());
  delay(1);
  interface.loop();
  EXPECT_FALSE(interface.isReadBusy());

  const uint8_t complete[] = {'<', 1, 0, 0x5A};
  stream.push(complete, sizeof(complete));
  EXPECT_EQ(interface.checkRecvFrame(frame), 1u);
  EXPECT_EQ(frame[0], 0x5A);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
