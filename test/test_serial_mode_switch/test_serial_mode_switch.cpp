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

TEST(UsbHwcdcSession, IgnoresWholeExpectedSelfResetBurstUntilActivity) {
  mesh::UsbSelfResetBurstGuard guard;
  EXPECT_FALSE(guard.shouldIgnoreBusReset());

  guard.expectSelfResetBurst();
  EXPECT_TRUE(guard.shouldIgnoreBusReset());
  EXPECT_TRUE(guard.shouldIgnoreBusReset());

  guard.notePostCleanActivity();
  EXPECT_FALSE(guard.shouldIgnoreBusReset());
}

TEST(UsbHwcdcSession, CompletedFrameGetsImmediateReplyLease) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.isClientConnected(
      false, 100, true, 100, 1, 2000, 600000, 100, 2000, false));
  EXPECT_TRUE(tracker.isClientConnected(
      false, 2099, true, 100, 1, 2000, 600000, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      false, 2100, true, 100, 1, 2000, 600000, 100, 2000, false));
  EXPECT_TRUE(tracker.takeSustainedHostLoss());
}

TEST(UsbHwcdcSession, DebouncesTransientHostLossAndReportsSustainedLossOnce) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 100, 100, 2000));
  // A long interval without sampling is not itself evidence of continuous
  // loss. The debounce window starts at the first observed false sample.
  EXPECT_TRUE(tracker.observeHost(false, 5000, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, 6999, 100, 2000));
  EXPECT_FALSE(tracker.takeSustainedHostLoss());
  EXPECT_FALSE(tracker.observeHost(false, 7000, 100, 2000));
  EXPECT_TRUE(tracker.takeSustainedHostLoss());
  EXPECT_FALSE(tracker.takeSustainedHostLoss());
}

TEST(UsbHwcdcSession, DebouncesAndReportsPhysicalLossEdgeOnce) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 100, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, 110, 100, 2000));
  EXPECT_FALSE(tracker.takeHostLossEdge());
  EXPECT_TRUE(tracker.observeHost(false, 209, 100, 2000));
  EXPECT_FALSE(tracker.takeHostLossEdge());
  EXPECT_TRUE(tracker.observeHost(false, 210, 100, 2000));
  EXPECT_TRUE(tracker.takeHostLossEdge());
  EXPECT_FALSE(tracker.takeHostLossEdge());

  // Repeated false samples belong to the same edge.
  EXPECT_TRUE(tracker.observeHost(false, 220, 100, 2000));
  EXPECT_FALSE(tracker.takeHostLossEdge());
}

TEST(UsbHwcdcSession, IgnoresTransientPhysicalLossBeforeEdgeDebounce) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 100, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, 110, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(true, 150, 100, 2000));
  EXPECT_FALSE(tracker.takeHostLossEdge());
}

TEST(UsbHwcdcSession, FrameProofAloneDoesNotInventPhysicalLossEdge) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.isClientConnected(
      false, 100, true, 100, 1, 2000, 600000, 100, 2000, false));
  EXPECT_FALSE(tracker.takeHostLossEdge());
}

TEST(UsbHwcdcSession, DebounceHandlesMillisRollover) {
  mesh::UsbHostPresenceDebouncer tracker;
  const uint32_t started = 0xFFFFFFF0u;
  EXPECT_TRUE(tracker.observeHost(true, started, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, started + 100u, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, started + 2099u, 100, 2000));
  EXPECT_FALSE(tracker.observeHost(false, started + 2100u, 100, 2000));
  EXPECT_TRUE(tracker.takeSustainedHostLoss());
}

TEST(UsbHwcdcSession, IdleLeaseIsAbsoluteWhileHostRemains) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_FALSE(tracker.isClientConnected(
      true, 700100, true, 100, 1, 2000, 600000, 100, 2000, false));
}

TEST(UsbHwcdcSession, ReplugDoesNotErasePendingOldSessionLoss) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 10, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, 20, 100, 2000));
  EXPECT_FALSE(tracker.observeHost(false, 2020, 100, 2000));
  EXPECT_FALSE(tracker.observeHost(true, 2030, 100, 2000));
  EXPECT_TRUE(tracker.takeSustainedHostLoss());
  EXPECT_FALSE(tracker.takeSustainedHostLoss());
}

TEST(UsbHwcdcSession, SustainedLossOverridesFreshFrameUntilReset) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 10, 100, 2000));
  EXPECT_TRUE(tracker.observeHost(false, 20, 100, 2000));
  EXPECT_FALSE(tracker.observeHost(false, 2020, 100, 2000));
  EXPECT_FALSE(tracker.isClientConnected(
      false, 2030, true, 2030, 1, 2000, 600000, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      true, 2040, true, 2040, 2, 2000, 600000, 100, 2000, false));
  EXPECT_TRUE(tracker.takeSustainedHostLoss());
  tracker.reset();
  EXPECT_TRUE(tracker.isClientConnected(
      true, 2050, true, 2050, 3, 2000, 600000, 100, 2000, false));
}

TEST(UsbHwcdcSession, FiniteReplyExtendsIdleAndGetsOneDrainWindow) {
  mesh::UsbHostPresenceDebouncer tracker;
  const uint32_t last_frame = 100;
  const uint32_t idle_limit = 600000;
  const uint32_t now = last_frame + idle_limit;

  EXPECT_TRUE(tracker.isClientConnected(
      true, now, true, last_frame, 1, 2000, idle_limit, 100, 2000, true));
  EXPECT_TRUE(tracker.isClientConnected(
      true, now + 1, true, last_frame, 1, 2000, idle_limit, 100, 2000, false));
  EXPECT_TRUE(tracker.isClientConnected(
      true, now + 2000, true, last_frame, 1, 2000, idle_limit, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      true, now + 2001, true, last_frame, 1, 2000, idle_limit, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      true, now + 3000, true, last_frame, 1, 2000, idle_limit, 100, 2000, false));
}

TEST(UsbHwcdcSession, NewFiniteReplyRearmsAfterDrain) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.isClientConnected(
      true, 700000, true, 1, 1, 2000, 600000, 100, 2000, true));
  EXPECT_TRUE(tracker.isClientConnected(
      true, 700001, true, 1, 1, 2000, 600000, 100, 2000, false));
  EXPECT_TRUE(tracker.isClientConnected(
      true, 701000, true, 1, 1, 2000, 600000, 100, 2000, true));
  EXPECT_TRUE(tracker.isClientConnected(
      true, 701001, true, 1, 1, 2000, 600000, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      true, 703001, true, 1, 1, 2000, 600000, 100, 2000, false));
}

TEST(UsbHwcdcSession, SustainedLossOverridesFiniteReplyAndResetClearsIt) {
  mesh::UsbHostPresenceDebouncer tracker;
  EXPECT_TRUE(tracker.observeHost(true, 10, 100, 2000));
  EXPECT_TRUE(tracker.isClientConnected(
      true, 700000, true, 1, 1, 2000, 600000, 100, 2000, true));
  EXPECT_TRUE(tracker.observeHost(false, 700010, 100, 2000));
  EXPECT_FALSE(tracker.observeHost(false, 702010, 100, 2000));
  EXPECT_FALSE(tracker.isClientConnected(
      false, 702011, true, 1, 1, 2000, 600000, 100, 2000, true));
  tracker.reset();
  EXPECT_FALSE(tracker.isClientConnected(
      true, 702012, true, 1, 1, 2000, 600000, 100, 2000, false));
}

TEST(UsbHwcdcSession, FiniteReplyDrainHandlesMillisRollover) {
  mesh::UsbHostPresenceDebouncer tracker;
  const uint32_t started = 0xFFFFFFF0u;
  EXPECT_TRUE(tracker.isClientConnected(
      true, started, true, started - 600001u, 1,
      2000, 600000, 100, 2000, true));
  EXPECT_TRUE(tracker.isClientConnected(
      true, started + 1u, true, started - 600001u, 1,
      2000, 600000, 100, 2000, false));
  EXPECT_TRUE(tracker.isClientConnected(
      true, started + 2000u, true, started - 600001u, 1,
      2000, 600000, 100, 2000, false));
  EXPECT_FALSE(tracker.isClientConnected(
      true, started + 2001u, true, started - 600001u, 1,
      2000, 600000, 100, 2000, false));
}

TEST(UsbHwcdcSession, SessionResetClearsFrameProofAndPartialIo) {
  BufferStream stream;
  ArduinoSerialInterface interface;
  interface.begin(stream, START_TOKEN);
  interface.enable();
  interface.enableFlowControl(true);

  const uint8_t complete[] = {'<', 1, 0, 0x05};
  stream.push(complete, sizeof(complete));
  uint8_t frame[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(interface.checkRecvFrame(frame), 1u);
  ASSERT_TRUE(interface.hasReceivedFrame());
  const uint32_t completed = interface.getCompletedFrameCount();

  stream.write_capacity = 0;
  const uint8_t response[] = {0x06, 0x42};
  ASSERT_EQ(interface.writeFrame(response, sizeof(response)),
            sizeof(response));
  const uint8_t partial[] = {'<', 2, 0, 0x16};
  stream.push(partial, sizeof(partial));
  EXPECT_EQ(interface.checkRecvFrame(frame), 0u);
  ASSERT_TRUE(interface.hasPendingIO());

  interface.resetSessionState();
  EXPECT_FALSE(interface.hasReceivedFrame());
  EXPECT_FALSE(interface.hasPendingIO());
  EXPECT_EQ(interface.getCompletedFrameCount(), completed);
}

TEST(UsbMotaOwnerPolicy, MatchesEveryOtaCliAttachDetachSpelling) {
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("ota folder on"));
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("ota folder off"));
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("ota fold on"));
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("ota fold off"));
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("ota  folder   on"));
  EXPECT_TRUE(mesh::isUsbMotaOwnerTransitionCommand("  ota fold  off"));

  EXPECT_FALSE(mesh::isUsbMotaOwnerTransitionCommand("ota folder"));
  EXPECT_FALSE(mesh::isUsbMotaOwnerTransitionCommand("ota folder status"));
  EXPECT_FALSE(mesh::isUsbMotaOwnerTransitionCommand("ota folder on extra"));
  EXPECT_FALSE(mesh::isUsbMotaOwnerTransitionCommand("ota pull 1234 flash"));
}

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

  // MSG_WAITING is delivery-required for queue admission, but it is an
  // unsolicited state notification and therefore remains broadcast.
  const uint8_t message_waiting[] = {0x83};
  EXPECT_EQ(manager.writeFrame(message_waiting, sizeof(message_waiting)),
            sizeof(message_waiting));
  ASSERT_EQ(usb.sent_frames.size(), 4u);
  ASSERT_EQ(wifi.sent_frames.size(), 2u);
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

TEST(MultiSerialInterface, ForgetsOnlyTheDisconnectedHostReplyRoute) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface bluetooth;
  usb.connected = true;
  bluetooth.connected = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::Bluetooth, &bluetooth));
  manager.enable();

  usb.received_frames.push_back({0x04});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  manager.lockReplyRoute();
  EXPECT_TRUE(manager.isReplyRouteFor(&usb));
  EXPECT_FALSE(manager.isReplyRouteFor(&bluetooth));

  manager.forgetReplyRouteForDisconnected(&bluetooth);
  EXPECT_TRUE(manager.isReplyRouteFor(&usb));

  // The application stops the USB response producer before forgetting this
  // route. A new host then starts without inheriting either routing pointer.
  manager.unlockReplyRoute();
  manager.forgetReplyRouteForDisconnected(&usb);
  EXPECT_FALSE(manager.isReplyRouteFor(&usb));

  bluetooth.received_frames.push_back({0x16});
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  EXPECT_EQ(command[0], 0x16);
  EXPECT_TRUE(manager.isReplyRouteFor(&bluetooth));
}

TEST(MultiSerialInterface, CapturedAsyncReplySurvivesLaterRouteChanges) {
  MultiSerialInterface manager;
  FakeSerialInterface usb;
  FakeSerialInterface bluetooth;
  usb.connected = true;
  bluetooth.connected = true;
  ASSERT_TRUE(manager.addInterface(InterfaceType::USB, &usb));
  ASSERT_TRUE(manager.addInterface(InterfaceType::Bluetooth, &bluetooth));
  manager.enable();

  usb.received_frames.push_back({0x04});
  uint8_t command[MAX_FRAME_SIZE] = {};
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  BaseSerialInterface* captured = manager.captureReplyRoute();
  ASSERT_EQ(captured, &usb);

  // Capturing while a contact stream owns the route is valid even after that
  // producer releases its independent lock.
  manager.lockReplyRoute();
  manager.unlockReplyRoute();

  bluetooth.received_frames.push_back({0x16});
  ASSERT_EQ(manager.checkRecvFrame(command), 1u);
  EXPECT_EQ(command[0], 0x16);
  EXPECT_TRUE(manager.isReplyRouteFor(&bluetooth));

  const uint8_t delayed_response[] = {0x85, 0x42};
  EXPECT_FALSE(manager.isReplyRouteWriteBusy(captured));
  EXPECT_EQ(manager.writeFrameToRoute(
                captured, delayed_response, sizeof(delayed_response)),
            sizeof(delayed_response));
  ASSERT_EQ(usb.sent_frames.size(), 1u);
  EXPECT_TRUE(bluetooth.sent_frames.empty());

  usb.connected = false;
  EXPECT_FALSE(manager.isReplyRouteAvailable(captured));
  EXPECT_EQ(manager.writeFrameToRoute(
                captured, delayed_response, sizeof(delayed_response)), 0u);
  EXPECT_TRUE(bluetooth.sent_frames.empty());
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

TEST(SerialModeSwitch, SingleTtyLoggingCannotStealNetworkTerminal) {
  using Action = mesh::UsbLoggingTerminalAction;
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, true, false, false, true, true),
            Action::NO_ACTION);
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, true, false, false, false, true),
            Action::NO_ACTION);
}

TEST(SerialModeSwitch, LoggingClaimsOnlySingleTtyUsb) {
  using Action = mesh::UsbLoggingTerminalAction;
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, true, false, false, true, false),
            Action::CLAIM_USB);
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                true, true, false, false, true, false),
            Action::NO_ACTION);
}

TEST(SerialModeSwitch, LoggingOffRestoresTheBuildDefaultUsbMode) {
  using Action = mesh::UsbLoggingTerminalAction;
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, false, true, true, false, false),
            Action::RETURN_TO_BINARY);
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, false, true, true, true, false),
            Action::KEEP_ASCII);
  EXPECT_EQ(mesh::selectUsbLoggingTerminalAction(
                false, false, true, false, false, false),
            Action::NO_ACTION);
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

TEST(SerialFlowControl, RetriesAZeroLengthWriteWithoutLosingTheFrame) {
  BufferStream stream;
  stream.write_capacity = MAX_FRAME_SIZE + 3;
  stream.max_write = 0;
  ArduinoSerialInterface interface;
  interface.begin(stream);
  interface.enableFlowControl(true);
  interface.enable();

  const uint8_t payload[] = {0x88, 0x11, 0x22};
  EXPECT_EQ(interface.writeFrame(payload, sizeof(payload)), sizeof(payload));
  EXPECT_TRUE(stream.output.empty());
  EXPECT_TRUE(interface.hasPendingIO());

  stream.max_write = std::numeric_limits<size_t>::max();
  interface.loop();
  const std::vector<uint8_t> expected = {'>', 3, 0, 0x88, 0x11, 0x22};
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
