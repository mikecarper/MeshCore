#include <gtest/gtest.h>

#include "helpers/BleMotaStream.h"
#include "helpers/CompanionMotaControl.h"

#include <cstring>
#include <vector>

namespace {

bool allowed(const char* text) {
  return mesh::companion::isBleOtaControlCommandAllowed(
      reinterpret_cast<const uint8_t*>(text), std::strlen(text));
}

TEST(BleMotaControl, AllowsOnlyBoundedOtaSessionCommands) {
  EXPECT_TRUE(allowed("tempradio"));
  EXPECT_TRUE(allowed("tempradio 915,250,5,5,10"));
  EXPECT_TRUE(allowed("normalradio"));
  EXPECT_TRUE(allowed("ota"));
  EXPECT_TRUE(allowed("ota status"));
  EXPECT_TRUE(allowed("ota neighbors"));

  EXPECT_FALSE(allowed("tempradio "));
  EXPECT_FALSE(allowed("normalradio now"));
  EXPECT_FALSE(allowed("otafolder status"));
  EXPECT_FALSE(allowed("ota folder on"));
  EXPECT_FALSE(allowed("ota folder off"));
  EXPECT_FALSE(allowed("reboot"));
}

TEST(BleMotaControl, RejectsCommandInjectionBytes) {
  const uint8_t embedded_nul[] = {'o', 't', 'a', ' ', 's', 't', 0, 'a'};
  const uint8_t newline[] = {'o', 't', 'a', ' ', 's', 't', '\n', 'x'};
  const uint8_t carriage_return[] = {'n', 'o', 'r', 'm', 'a', 'l', 'r',
                                     'a', 'd', 'i', 'o', '\r'};
  const uint8_t shell_chain[] = {'o', 't', 'a', ' ', 's', 't', 'a', 't',
                                 'u', 's', ';', 'r', 'e', 'b', 'o', 'o', 't'};
  EXPECT_FALSE(mesh::companion::isBleOtaControlCommandAllowed(
      embedded_nul, sizeof(embedded_nul)));
  EXPECT_FALSE(mesh::companion::isBleOtaControlCommandAllowed(
      newline, sizeof(newline)));
  EXPECT_FALSE(mesh::companion::isBleOtaControlCommandAllowed(
      carriage_return, sizeof(carriage_return)));
  EXPECT_FALSE(mesh::companion::isBleOtaControlCommandAllowed(
      shell_chain, sizeof(shell_chain)));
}

struct SendCapture {
  std::vector<uint8_t> bytes;
};

size_t captureSend(void* context, const uint8_t* data, size_t length) {
  auto* capture = static_cast<SendCapture*>(context);
  capture->bytes.assign(data, data + length);
  return length;
}

TEST(BleMotaStream, IsInactiveAndEmptyByDefault) {
  mesh::ota::BleMotaStream stream;
  const uint8_t byte = 7;
  EXPECT_FALSE(stream.isActive());
  EXPECT_FALSE(stream.pushRx(&byte, 1));
  EXPECT_EQ(stream.available(), 0);
  EXPECT_EQ(stream.read(), -1);
  EXPECT_EQ(stream.write(&byte, 1), 0u);
}

TEST(BleMotaStream, CarriesFragmentedResponseAcrossRingWrap) {
  mesh::ota::BleMotaStream stream;
  stream.setActive(true);

  std::vector<uint8_t> first(200);
  for (size_t i = 0; i < first.size(); ++i) first[i] = i;
  ASSERT_TRUE(stream.pushRx(first.data(), first.size()));
  for (size_t i = 0; i < 190; ++i) {
    ASSERT_EQ(stream.read(), first[i]);
  }

  std::vector<uint8_t> second(100);
  for (size_t i = 0; i < second.size(); ++i) second[i] = 200 + i;
  ASSERT_TRUE(stream.pushRx(second.data(), second.size()));
  EXPECT_EQ(stream.available(), 110);
  for (size_t i = 190; i < first.size(); ++i) {
    ASSERT_EQ(stream.read(), first[i]);
  }
  for (uint8_t value : second) ASSERT_EQ(stream.read(), value);
  EXPECT_EQ(stream.read(), -1);
}

TEST(BleMotaStream, RejectsOverflowWithoutQueuingPartialFragment) {
  mesh::ota::BleMotaStream stream;
  stream.setActive(true);
  std::vector<uint8_t> first(240, 0x11);
  std::vector<uint8_t> overflow(20, 0x22);
  ASSERT_TRUE(stream.pushRx(first.data(), first.size()));
  EXPECT_FALSE(stream.pushRx(overflow.data(), overflow.size()));
  EXPECT_TRUE(stream.overflowed());
  EXPECT_EQ(stream.available(), 240);
  while (stream.available()) EXPECT_EQ(stream.read(), 0x11);
}

TEST(BleMotaStream, SendsRequestsOnlyWhileActiveAndClearsOnStop) {
  mesh::ota::BleMotaStream stream;
  SendCapture capture;
  stream.setSender(captureSend, &capture);
  stream.setActive(true);
  const uint8_t request[] = {'M', 'S', 1, 1};
  EXPECT_EQ(stream.write(request, sizeof(request)), sizeof(request));
  EXPECT_EQ(capture.bytes,
            std::vector<uint8_t>(request, request + sizeof(request)));

  const uint8_t response[] = {'m', 's', 1, 0, 1, 1};
  ASSERT_TRUE(stream.pushRx(response, sizeof(response)));
  stream.setActive(false);
  EXPECT_EQ(stream.available(), 0);
  EXPECT_EQ(stream.write(request, sizeof(request)), 0u);
}

TEST(BleMotaStream, DisconnectAndRestartDiscardEveryPartialOldResponse) {
  mesh::ota::BleMotaStream stream;
  stream.setActive(true);

  const uint8_t partial_old_response[] = {'m', 's', 3, 0, 0xAA, 0xBB};
  ASSERT_TRUE(stream.pushRx(partial_old_response,
                            sizeof(partial_old_response)));
  ASSERT_EQ(stream.available(), (int)sizeof(partial_old_response));

  // A BLE loss can happen between response fragments.  Starting the next
  // source session must never splice those stale bytes into its first frame.
  stream.setActive(false);
  EXPECT_EQ(stream.available(), 0);
  stream.setActive(true);
  EXPECT_EQ(stream.available(), 0);

  const uint8_t fresh_response[] = {'m', 's', 1, 0, 1, 1};
  ASSERT_TRUE(stream.pushRx(fresh_response, sizeof(fresh_response)));
  for (uint8_t expected : fresh_response) {
    EXPECT_EQ(stream.read(), expected);
  }
  EXPECT_EQ(stream.read(), -1);
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
