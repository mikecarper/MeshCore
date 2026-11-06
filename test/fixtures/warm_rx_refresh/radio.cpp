// Host command adapter. Production CustomSX1262 methods are inserted below.
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <string>
#include <helpers/RadioSwitchTuning.h>
#include <helpers/radiolib/RadioInterruptEvents.h>
#include <helpers/radiolib/SX1262ProfileSwitchState.h>
#include <helpers/radiolib/WarmRxRefresh.h>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_SPI_CMD_FAILED -200
#define RADIOLIB_SX126X_STANDBY_XOSC 1
#define RADIOLIB_SX126X_STANDBY_RC 0
#define RADIOLIB_SX126X_XOSC_START_ERR 32
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_RADIO_MODE_NONE 0
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 1
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 2
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 3
#define MESH_DEBUG_PRINTLN(...) ((void)0)

using RadioModeType_t = int;
using RadioLibIrqFlags_t = uint32_t;
struct RadioModeConfig_t {};
static uint32_t now_ms = 100;
uint32_t millis() { return now_ms; }
uint32_t micros() { return now_ms * 1000U; }
uint32_t profileTimestamp(bool) { return micros(); }

struct Module {
  enum { MODE_IDLE = 0, MODE_RX = 1 };
  std::string* trace;
  void setRfSwitchState(int mode) {
    assert(mode == MODE_IDLE || mode == MODE_RX);
    *trace += mode == MODE_RX ? 'W' : 'I';
  }
};

struct SX1262 {
  std::string trace;
  char fail = 0;
  int modem = RADIOLIB_SX126X_PACKET_TYPE_LORA;
  bool standbyXOSC = true, pending_fifo = false, lost_fifo = false;
  bool inject_standby_irq = false, inject_rx_irq = false;
  RadioInterruptEvents* events = nullptr;
  unsigned actual_rx_commands = 0;
  size_t preambleLengthLoRa = 32;
  uint8_t crcTypeLoRa = 1, implicitLen = 255, headerType = 0, invertIQEnabled = 0;
  uint32_t rxTimeout = 0;
  int stagedMode = 3;
  float tcxoVoltage = 1.8f;
  uint32_t tcxoDelay = 1600;
  unsigned errors = 32;
  Module module{&trace};
  Module* mod = &module;
  virtual ~SX1262() = default;
  int16_t call(char command) {
    trace += command;
    if (inject_standby_irq && (command == 'f' || command == 's' || command == 'S' || command == 'U')) {
      assert(events);
      events->record();
      pending_fifo = true;
    }
    return fail == command ? -100 : 0;
  }
  Module* getMod() { return mod; }
  int16_t setFs() { return call('f'); }
  virtual int16_t standby() { return call('S'); }
  virtual int16_t standby(uint8_t) { return call('U'); }
  int16_t standby(uint8_t mode, bool wake) {
    assert(mode == RADIOLIB_SX126X_STANDBY_XOSC);
    return call(wake ? 'S' : 's');
  }
  virtual int16_t sleep() { return call('L'); }
  int16_t sleep(bool) { return call('l'); }
  int16_t reset(bool) { return call('Z'); }
  int16_t calibrate(uint8_t) { return call('A'); }
  int16_t calibrateImage(float) { return call('a'); }
  int16_t setFrequency(float) { return call('T'); }
  unsigned getDeviceErrors() { call('E'); return errors; }
  int16_t setTCXO(float voltage, uint32_t delay) {
    assert(voltage == tcxoVoltage && delay == 1600);
    return call('V');
  }
  virtual int16_t stageMode(RadioModeType_t mode, RadioModeConfig_t*) {
    return call(mode == 1 ? 'G' : 't');
  }
  virtual int16_t setPreambleLength(size_t length) {
    preambleLengthLoRa = length;
    return call('M');
  }
  int16_t setCRC(uint8_t, uint16_t, uint16_t, bool) { return call('c'); }
  int16_t implicitHeader(size_t) { return call('h'); }
  int16_t explicitHeader() { return call('e'); }
  virtual int16_t invertIQ(bool) { return call('i'); }
  virtual int16_t startReceive() {
    return startReceive(0xffffff, 9, 2, 0);
  }
  int16_t startReceive(uint32_t timeout, uint32_t flags, uint32_t mask, size_t length) {
    assert(timeout == 0xffffff && flags == 9 && mask == 2 && length == 0);
    int16_t result = stageMode(1, nullptr);
    if (result == 0) result = standby();
    if (result == 0) result = call('F');
    return result;
  }
  int16_t startReceiveDutyCycle(uint32_t, uint32_t, uint32_t, uint32_t) { return call('D'); }
  uint8_t getPacketType() { call('Q'); return modem; }
  int16_t clearIrqStatus() {
    if (pending_fifo) lost_fifo = true;
    pending_fifo = false;
    return call('C');
  }
  int16_t setPacketParams(size_t preamble, int crc, int length, int header, int iq) {
    assert(preamble == preambleLengthLoRa && crc == 1 && length == 255 && header == 0 && iq == 0);
    return call('P');
  }
  int16_t setRx(uint32_t timeout) {
    assert(timeout == RADIOLIB_SX126X_RX_TIMEOUT_INF);
    ++actual_rx_commands;
    if (inject_rx_irq) {
      assert(events);
      events->record();
      pending_fifo = true;
    }
    return call('R');
  }
  int getPacketLength() const { return pending_fifo ? 5 : 0; }
  float getSNR() const { return 0; }
  float getRSSI() const { return -100; }
  int16_t readData(uint8_t* bytes, size_t length) {
    assert(pending_fifo && length == 5);
    trace += 'd';
    for (size_t index = 0; index < length; ++index) bytes[index] = uint8_t('a' + index);
    pending_fifo = false;
    return 0;
  }
};

struct CustomSX1262 : SX1262 {
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  SX1262ProfileSwitchState _profileSwitch{MC_SX1262_FAST_PROFILE_SWITCH != 0};
  uint8_t _profileSwitchFeatures = defaultProfileSwitchFeatures();
  bool _rx_ps_rf_rx_disabled = false;
  uint32_t _profileFastResumes = 0;
  bool _coldStandby = true, _tcxoWakePending = false;
  @DRIVER_MEMBERS@
  @DRIVER_METHODS@
};

@WRAPPER@
@SCENARIOS@
