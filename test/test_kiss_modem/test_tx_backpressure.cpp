#include <gtest/gtest.h>

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <future>
#include <mutex>
#include <queue>
#include <vector>

#include "KissModem.h"

static constexpr int TEST_TX_AVAILABLE_BYTES = 4096;
static constexpr size_t TEST_DEFAULT_MAX_WRITE_CHUNK = SIZE_MAX;
static constexpr size_t TEST_PARTIAL_WRITE_CHUNK = 2;
static constexpr int TEST_PARTIAL_WRITE_FLUSH_LOOPS = 3;
static constexpr uint8_t TEST_SNR = 8;
static constexpr uint8_t TEST_RSSI = 200;

class BlockingStream : public Stream {
public:
  void pushRx(const std::vector<uint8_t>& bytes) {
    std::lock_guard<std::mutex> lock(_mutex);
    for (uint8_t b : bytes) {
      _rx.push(b);
    }
  }

  void setBlockWrites(bool blocked) {
    {
      std::lock_guard<std::mutex> lock(_mutex);
      _block_writes = blocked;
    }
    _cv.notify_all();
  }

  bool isWriteBlocked() const {
    return _entered_block.load();
  }

  size_t writesCount() const {
    std::lock_guard<std::mutex> lock(_mutex);
    return _writes.size();
  }

  size_t rxAvailable() const {
    std::lock_guard<std::mutex> lock(_mutex);
    return _rx.size();
  }

  std::vector<uint8_t> writesSnapshot() const {
    std::lock_guard<std::mutex> lock(_mutex);
    return _writes;
  }

  void clearWrites() {
    std::lock_guard<std::mutex> lock(_mutex);
    _writes.clear();
  }

  int availableForWrite() override {
    std::lock_guard<std::mutex> lock(_mutex);
    return _block_writes ? 0 : TEST_TX_AVAILABLE_BYTES;
  }

  void setMaxWriteChunk(size_t chunk) {
    std::lock_guard<std::mutex> lock(_mutex);
    _max_write_chunk = chunk;
  }

  size_t write(const uint8_t* buffer, size_t size) override {
    std::unique_lock<std::mutex> lock(_mutex);
    while (_block_writes) {
      _entered_block.store(true);
      _cv.wait(lock);
    }
    const size_t chunk = (size < _max_write_chunk) ? size : _max_write_chunk;
    for (size_t i = 0; i < chunk; i++) {
      _writes.push_back(buffer[i]);
    }
    return chunk;
  }

  size_t write(uint8_t b) override {
    return write(&b, 1);
  }

  int available() override {
    std::lock_guard<std::mutex> lock(_mutex);
    return static_cast<int>(_rx.size());
  }

  int read() override {
    std::lock_guard<std::mutex> lock(_mutex);
    if (_rx.empty()) {
      return -1;
    }
    int b = _rx.front();
    _rx.pop();
    return b;
  }

private:
  mutable std::mutex _mutex;
  std::condition_variable _cv;
  std::queue<uint8_t> _rx;
  std::vector<uint8_t> _writes;
  bool _block_writes = false;
  std::atomic<bool> _entered_block = false;
  size_t _max_write_chunk = TEST_DEFAULT_MAX_WRITE_CHUNK;
};

class FakeRNG : public mesh::RNG {
public:
  void random(uint8_t* dest, size_t sz) override {
    for (size_t i = 0; i < sz; i++) {
      dest[i] = 0;
    }
  }
};

class FakeRadio : public mesh::Radio {
public:
  FakeRadio() {
    _profiles.primary.freq = 909.5f;
    _profiles.primary.bw = 62.5f;
    _profiles.primary.sf = 7;
    _profiles.primary.cr = 5;
  }

  mesh::RadioProfiles* profiles() override { return &_profiles; }
  const mesh::RadioProfiles* profiles() const override { return &_profiles; }
  bool validateProfile(const mesh::RadioProfileParams& params) const override {
    return mesh::RadioProfiles::valid(params);
  }
  uint8_t receiveProfile() const override { return _active_profile; }
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t profile) override {
    _prepared_profiles.push_back(profile);
    if (_prepare_result != mesh::RadioParamApplyResult::APPLIED) return _prepare_result;
    if (!_profiles.canTransmit(profile)) return mesh::RadioParamApplyResult::FAILED;
    _active_profile = profile;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  mesh::RadioParamApplyResult trySetPrimaryParams(const mesh::RadioProfileParams& params,
      bool temporary, const uint32_t* = nullptr) override {
    mesh::RadioProfiles preview = _profiles;
    preview.primary = params;
    if (!validateProfile(params) || !preview.automaticPreambleFits()) {
      return mesh::RadioParamApplyResult::FAILED;
    }
    _profiles.setPrimary(params, temporary);
    _active_profile = 0;
    return mesh::RadioParamApplyResult::APPLIED;
  }
  bool isReceiving() override { return false; }
  uint32_t getEstAirtimeFor(uint16_t) override { return 10; }
  uint32_t getProfileAirtime(uint8_t profile, int, uint8_t = 0) override {
    _airtime_profiles.push_back(profile);
    return 10;
  }
  bool startSendRaw(const uint8_t*, uint16_t) override {
    _start_send_count++;
    _sent_profiles.push_back(_active_profile);
    return _start_send_result;
  }
  bool isSendComplete() override { return _send_complete; }
  void onSendFinished() override { _send_finished_count++; }
  int16_t getNoiseFloor() override { return -120; }

  void setStartSendResult(bool result) { _start_send_result = result; }
  void setSendComplete(bool complete) { _send_complete = complete; }
  void setPrepareResult(mesh::RadioParamApplyResult result) { _prepare_result = result; }
  void setReceiveProfile(uint8_t profile) { _active_profile = profile; }
  int startSendCount() const { return _start_send_count; }
  int sendFinishedCount() const { return _send_finished_count; }
  uint8_t lastSentProfile() const { return _sent_profiles.empty() ? 0xff : _sent_profiles.back(); }
  int prepareCount() const { return (int)_prepared_profiles.size(); }
  uint8_t lastPreparedProfile() const {
    return _prepared_profiles.empty() ? 0xff : _prepared_profiles.back();
  }
  mesh::RadioProfiles& profileConfig() { return _profiles; }

private:
  mesh::RadioProfiles _profiles;
  uint8_t _active_profile = 0;
  bool _start_send_result = true;
  bool _send_complete = true;
  int _start_send_count = 0;
  int _send_finished_count = 0;
  mesh::RadioParamApplyResult _prepare_result = mesh::RadioParamApplyResult::APPLIED;
  std::vector<uint8_t> _prepared_profiles;
  std::vector<uint8_t> _airtime_profiles;
  std::vector<uint8_t> _sent_profiles;
};

class FakeBoard : public mesh::MainBoard {
public:
  uint16_t getBattMilliVolts() override { return 4200; }
  float getMCUTemperature() override { return 24.0f; }
  const char* getManufacturerName() override { return "test-board"; }
  void reboot() override {}
};

class FakeSensors : public SensorManager {
public:
  bool querySensors(uint8_t, CayenneLPP&) override { return false; }
};

class KissModemFixture : public ::testing::Test {
protected:
  BlockingStream serial;
  mesh::LocalIdentity identity;
  FakeRNG rng;
  FakeRadio radio;
  FakeBoard board;
  FakeSensors sensors;
  KissModem modem;

  KissModemFixture()
    : modem(serial, identity, rng, radio, board, sensors) {
    modem.begin();
  }

  void SetUp() override {
    resetArduinoMock();
  }

  static std::vector<uint8_t> dataFrame(const std::vector<uint8_t>& packet, uint8_t profile = 0) {
    std::vector<uint8_t> frame = {KISS_FEND, (uint8_t)(profile << 4)};
    frame.insert(frame.end(), packet.begin(), packet.end());
    frame.push_back(KISS_FEND);
    return frame;
  }

  static std::vector<uint8_t> hardwareFrame(uint8_t command, const std::vector<uint8_t>& payload = {}) {
    std::vector<uint8_t> frame = {KISS_FEND, KISS_CMD_SETHARDWARE, command};
    frame.insert(frame.end(), payload.begin(), payload.end());
    frame.push_back(KISS_FEND);
    return frame;
  }

  static std::vector<uint8_t> radio2Payload(uint32_t freq_hz, uint32_t bw_hz,
      uint8_t sf, uint8_t cr, uint8_t mode, uint16_t preamble) {
    return {
      (uint8_t)freq_hz, (uint8_t)(freq_hz >> 8), (uint8_t)(freq_hz >> 16), (uint8_t)(freq_hz >> 24),
      (uint8_t)bw_hz, (uint8_t)(bw_hz >> 8), (uint8_t)(bw_hz >> 16), (uint8_t)(bw_hz >> 24),
      sf, cr, mode, (uint8_t)preamble, (uint8_t)(preamble >> 8)};
  }

  static void appendU16(std::vector<uint8_t>& bytes, uint16_t value) {
    bytes.push_back((uint8_t)value);
    bytes.push_back((uint8_t)(value >> 8));
  }

  void sendHardware(uint8_t command, const std::vector<uint8_t>& payload = {}) {
    serial.pushRx(hardwareFrame(command, payload));
    modem.loop();
  }

  void advanceToTxSending() {
    modem.loop();
    modem.loop();
    delay((uint32_t)KISS_DEFAULT_TXDELAY * 10);
    modem.loop();
  }
};

TEST_F(KissModemFixture, PingResponseShouldNotStallLoopUnderTxBackpressure) {
  serial.setBlockWrites(true);
  serial.pushRx({KISS_FEND, KISS_CMD_SETHARDWARE, HW_CMD_PING, KISS_FEND});

  auto future = std::async(std::launch::async, [this]() {
    modem.loop();
  });

  auto status = future.wait_for(std::chrono::milliseconds(100));
  EXPECT_EQ(status, std::future_status::ready) << "KissModem::loop blocked in serial write under TX backpressure";
  EXPECT_FALSE(serial.isWriteBlocked()) << "KissModem entered blocking write path";

  serial.setBlockWrites(false);
  future.wait();
  modem.loop();
  EXPECT_GT(serial.writesCount(), 0U) << "KissModem did not flush queued response after backpressure cleared";
}

TEST_F(KissModemFixture, PingResponseKeepsStandardKissFraming) {
  serial.pushRx({KISS_FEND, KISS_CMD_SETHARDWARE, HW_CMD_PING, KISS_FEND});
  modem.loop();

  const std::vector<uint8_t> expected = {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_PING), KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, PingResponseKeepsFramingWithPartialBulkWrites) {
  serial.setMaxWriteChunk(TEST_PARTIAL_WRITE_CHUNK);
  serial.pushRx({KISS_FEND, KISS_CMD_SETHARDWARE, HW_CMD_PING, KISS_FEND});
  for (int i = 0; i < TEST_PARTIAL_WRITE_FLUSH_LOOPS; i++) {
    modem.loop();
  }

  const std::vector<uint8_t> expected = {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_PING), KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, PacketAndMetaAreQueuedTogetherUnderBackpressure) {
  static constexpr uint8_t TEST_PACKET[] = {0x01, 0x02, 0x03};

  serial.setBlockWrites(true);
  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, TEST_PACKET, sizeof(TEST_PACKET));
  serial.setBlockWrites(false);
  modem.loop();
  modem.loop();

  const std::vector<uint8_t> expected = {
      KISS_FEND, KISS_CMD_DATA, TEST_PACKET[0], TEST_PACKET[1], TEST_PACKET[2], KISS_FEND,
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_RX_META, TEST_SNR, TEST_RSSI, KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, RadioTxCompletionAdvancesWhileHostOutputIsBackedUp) {
  serial.pushRx(dataFrame({0x42}));
  advanceToTxSending();
  ASSERT_EQ(radio.startSendCount(), 1);

  serial.setBlockWrites(true);
  modem.loop();
  EXPECT_EQ(radio.sendFinishedCount(), 1);
  EXPECT_TRUE(modem.isTxBusy());

  serial.setBlockWrites(false);
  modem.loop();

  const std::vector<uint8_t> expected = {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_TX_DONE, 0x01, KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
  EXPECT_FALSE(modem.isTxBusy());
}

TEST_F(KissModemFixture, QueueFullReportsBusyWithoutDroppingQueuedFrames) {
  static constexpr uint8_t TEST_PACKET_ONE[] = {0x11, 0x12};
  static constexpr uint8_t TEST_PACKET_TWO[] = {0x21, 0x22};

  serial.setBlockWrites(true);
  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, TEST_PACKET_ONE, sizeof(TEST_PACKET_ONE));
  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, TEST_PACKET_TWO, sizeof(TEST_PACKET_TWO));
  serial.setBlockWrites(false);
  modem.loop();

  const std::vector<uint8_t> expected = {
      KISS_FEND, KISS_CMD_DATA, TEST_PACKET_ONE[0], TEST_PACKET_ONE[1], KISS_FEND,
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_RX_META, TEST_SNR, TEST_RSSI, KISS_FEND,
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_ERROR, HW_ERR_TX_BUSY, KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, QueuedEncoderEscapesKissSpecialBytes) {
  static constexpr uint8_t TEST_PACKET[] = {KISS_FEND, KISS_FESC, 0x01};

  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, TEST_PACKET, sizeof(TEST_PACKET));

  const std::vector<uint8_t> expected = {
      KISS_FEND, KISS_CMD_DATA, KISS_FESC, KISS_TFEND, KISS_FESC, KISS_TFESC, 0x01, KISS_FEND,
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_RX_META, TEST_SNR, TEST_RSSI, KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, MaxPacketWorstCaseEscapingFitsQueuedFrame) {
  std::vector<uint8_t> packet(KISS_MAX_PACKET_SIZE, KISS_FEND);
  std::vector<uint8_t> expected = {KISS_FEND, KISS_CMD_DATA};
  for (size_t i = 0; i < packet.size(); i++) {
    expected.push_back(KISS_FESC);
    expected.push_back(KISS_TFEND);
  }
  expected.push_back(KISS_FEND);
  expected.insert(expected.end(), {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_RX_META, TEST_SNR, TEST_RSSI, KISS_FEND});

  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, packet.data(), packet.size());
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, LegacyPrimaryRadioSetAndGetStayTenBytes) {
  const std::vector<uint8_t> primary = {
      0x60, 0xDE, 0x35, 0x36,  // 909500000 Hz
      0x24, 0xF4, 0x00, 0x00,  // 62500 Hz
      7, 5};

  sendHardware(HW_CMD_SET_RADIO, primary);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_OK, KISS_FEND}));

  serial.clearWrites();
  sendHardware(HW_CMD_GET_RADIO);
  const std::vector<uint8_t> expected = {
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_GET_RADIO),
      primary[0], primary[1], primary[2], primary[3], primary[4], primary[5], primary[6], primary[7],
      primary[8], primary[9], KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, Radio2WireRoundTripAndValidation) {
  const std::vector<uint8_t> radio2 = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);

  sendHardware(HW_CMD_SET_RADIO2, radio2);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_OK, KISS_FEND}));
  EXPECT_EQ(radio.profileConfig().secondary.mode, mesh::RadioProfileMode::RxTx);
  EXPECT_FLOAT_EQ(radio.profileConfig().secondary.params.freq, 910.5f);

  serial.clearWrites();
  sendHardware(HW_CMD_GET_RADIO2);
  std::vector<uint8_t> expected = {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_GET_RADIO2)};
  expected.insert(expected.end(), radio2.begin(), radio2.end());
  expected.push_back(KISS_FEND);
  EXPECT_EQ(serial.writesSnapshot(), expected);

  serial.clearWrites();
  std::vector<uint8_t> short_payload(radio2.begin(), radio2.end() - 1);
  sendHardware(HW_CMD_SET_RADIO2, short_payload);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_ERROR, HW_ERR_INVALID_LENGTH, KISS_FEND}));
  EXPECT_FLOAT_EQ(radio.profileConfig().secondary.params.freq, 910.5f);

  serial.clearWrites();
  std::vector<uint8_t> invalid_mode = radio2;
  invalid_mode[10] = 3;
  sendHardware(HW_CMD_SET_RADIO2, invalid_mode);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_ERROR, HW_ERR_INVALID_PARAM, KISS_FEND}));
  EXPECT_EQ(radio.profileConfig().secondary.mode, mesh::RadioProfileMode::RxTx);
}

TEST_F(KissModemFixture, TempRadio2RestoresSavedProfileAndLeavesSavedUpdatesDeferred) {
  const std::vector<uint8_t> saved = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, saved);
  serial.clearWrites();

  std::vector<uint8_t> temporary = radio2Payload(868000000, 125000, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 32);
  appendU16(temporary, 1);
  sendHardware(HW_CMD_SET_TEMPRADIO2, temporary);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_OK, KISS_FEND}));
  EXPECT_TRUE(radio.profileConfig().secondary_temporary);
  EXPECT_FLOAT_EQ(radio.profileConfig().secondary.params.freq, 868.0f);

  serial.clearWrites();
  const std::vector<uint8_t> replacement_saved = radio2Payload(433920000, 125000, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, replacement_saved);
  EXPECT_FALSE(radio.profileConfig().secondary.params.freq == 433.92f)
      << "saved radio2 must not interrupt the active temporary lease";
  EXPECT_FLOAT_EQ(radio.profileConfig().secondary.params.freq, 868.0f);

  serial.clearWrites();
  sendHardware(HW_CMD_GET_RADIO2);
  std::vector<uint8_t> saved_expected = {KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_GET_RADIO2)};
  saved_expected.insert(saved_expected.end(), replacement_saved.begin(), replacement_saved.end());
  saved_expected.push_back(KISS_FEND);
  EXPECT_EQ(serial.writesSnapshot(), saved_expected);

  delay(60000);
  modem.loop();
  EXPECT_FALSE(radio.profileConfig().secondary_temporary);
  EXPECT_FLOAT_EQ(radio.profileConfig().secondary.params.freq, 433.92f);

  serial.clearWrites();
  sendHardware(HW_CMD_GET_TEMPRADIO2);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP(HW_CMD_GET_TEMPRADIO2),
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, KISS_FEND}));
}

TEST_F(KissModemFixture, SecondaryReceiveUsesPortOneWithoutChangingPacketOrRxMeta) {
  const std::vector<uint8_t> radio2 = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, radio2);
  serial.clearWrites();

  static constexpr uint8_t packet[] = {0x01, 0x02, 0x03};
  serial.setBlockWrites(true);
  modem.onPacketReceived((int8_t)TEST_SNR, (int8_t)TEST_RSSI, packet, sizeof(packet), 1);
  serial.setBlockWrites(false);
  modem.loop();
  modem.loop();

  const std::vector<uint8_t> expected = {
      KISS_FEND, 0x10, packet[0], packet[1], packet[2], KISS_FEND,
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_RX_META, TEST_SNR, TEST_RSSI, KISS_FEND};
  EXPECT_EQ(serial.writesSnapshot(), expected);
}

TEST_F(KissModemFixture, SecondaryTransmitUsesPortOneAndRejectsReceiveOnlyProfile) {
  const std::vector<uint8_t> rxtx = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, rxtx);
  serial.clearWrites();

  serial.pushRx(dataFrame({0x42}, 1));
  advanceToTxSending();
  EXPECT_EQ(radio.startSendCount(), 1);
  EXPECT_EQ(radio.lastSentProfile(), 1);
  EXPECT_EQ(radio.lastPreparedProfile(), 1);

  modem.loop();
  modem.loop();
  serial.clearWrites();
  const std::vector<uint8_t> receive_only = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::Rx, 0);
  sendHardware(HW_CMD_SET_RADIO2, receive_only);
  serial.clearWrites();
  serial.pushRx(dataFrame({0x43}, 1));
  modem.loop();
  modem.loop();
  modem.loop();
  EXPECT_EQ(radio.startSendCount(), 1);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_TX_DONE, 0x00, KISS_FEND}));
}

TEST_F(KissModemFixture, PendingSecondaryTransmitFailsWhenItsProfileChanges) {
  const std::vector<uint8_t> radio2 = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, radio2);
  serial.clearWrites();

  serial.pushRx(dataFrame({0x42}, 1));
  modem.loop();  // Accept data and bind its profile generation.
  EXPECT_EQ(radio.startSendCount(), 0);

  std::vector<uint8_t> temporary = radio2Payload(868000000, 125000, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 32);
  appendU16(temporary, 1);
  sendHardware(HW_CMD_SET_TEMPRADIO2, temporary);
  EXPECT_EQ(radio.startSendCount(), 0);

  serial.clearWrites();
  modem.loop();
  EXPECT_EQ(radio.startSendCount(), 0);
  EXPECT_EQ(serial.writesSnapshot(), (std::vector<uint8_t>{
      KISS_FEND, KISS_CMD_SETHARDWARE, HW_RESP_TX_DONE, 0x00, KISS_FEND}));
}

TEST_F(KissModemFixture, BusyProfilePreparationDefersSecondaryTransmit) {
  const std::vector<uint8_t> radio2 = radio2Payload(910500000, 62500, 7, 5,
      (uint8_t)mesh::RadioProfileMode::RxTx, 0);
  sendHardware(HW_CMD_SET_RADIO2, radio2);
  serial.clearWrites();
  radio.setPrepareResult(mesh::RadioParamApplyResult::BUSY);

  serial.pushRx(dataFrame({0x42}, 1));
  modem.loop();
  modem.loop();
  delay(KISS_DEFAULT_TXDELAY * 10);
  modem.loop();
  EXPECT_EQ(radio.startSendCount(), 0);

  radio.setPrepareResult(mesh::RadioParamApplyResult::APPLIED);
  modem.loop();
  delay(KISS_DEFAULT_TXDELAY * 10);
  modem.loop();
  EXPECT_EQ(radio.startSendCount(), 1);
  EXPECT_EQ(radio.lastSentProfile(), 1);
}

TEST_F(KissModemFixture, BusyHostInputCannotStarveTheProfileScannerLoop) {
  std::vector<uint8_t> host_bytes(KISS_RX_SERVICE_BYTE_BUDGET * 2, 0x01);
  serial.pushRx(host_bytes);

  modem.loop();

  // A full KISS frame may span calls; bounded parsing leaves the outer sketch
  // loop free to run radio_driver.loop() and make the next ~0.6 ms profile hop.
  EXPECT_EQ(serial.rxAvailable(), KISS_RX_SERVICE_BYTE_BUDGET);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
