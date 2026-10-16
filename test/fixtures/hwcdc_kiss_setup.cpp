// The runner inserts the production KISS setup and backend selection.
#include <Arduino.h>
#include <helpers/UsbLogging.h>
#include <cassert>
#include <string>
#include <vector>

static std::vector<std::string> calls;
static bool usb_logging = true, usb_debug = true;
static bool serial_live = false;
static bool reset_event = false, cleanup_ready = false;
static bool simulate_no_radio = false;
static unsigned identity_loads = 0;
struct TestSerial : Stream {
  int rx = -1, tx = -1;
  unsigned baud = 0;
  explicit operator bool() const { return true; }
  void begin(unsigned speed) {
    baud = speed;
    if (this == primary) { serial_live = true; calls.push_back("serial.begin"); }
    else calls.push_back("uart.begin");
  }
  void setPins(int receive, int transmit) { rx = receive; tx = transmit; }
  static TestSerial* primary;
} Serial, Serial1;
TestSerial* TestSerial::primary = &Serial;
static Stream guarded_port;
namespace mesh {
void setUsbLoggingEnabled(bool value) { usb_logging = value; calls.push_back("logging.gate"); }
void setUsbDebugEnabled(bool value) { usb_debug = value; calls.push_back("debug.gate"); }
void prepareUsbLoggingPort() {
  assert(!serial_live && "HWCDC ring resize must precede enabling the ISR");
  calls.push_back("prepare");
}
void beginUsbLoggingPort() {
  assert(serial_live);
  calls.push_back("begin.logging");
}
Stream& usbCompanionPort() { return guarded_port; }
void serviceUsbLoggingPort() { calls.push_back("usb.service"); }
bool takeUsbTerminalSessionReset() {
  calls.push_back("usb.boundary");
  const bool event = reset_event;
  reset_event = false;
  return event;
}
bool tryCompleteUsbTerminalSessionReset() {
  calls.push_back("usb.cleanup");
  return cleanup_ready;
}
}
struct Board {
  void begin() {
#if EXPECT_HWCDC
    assert(!usb_logging && !usb_debug);
#endif
    calls.push_back("board.begin");
  }
  void reboot() { assert(false); }
  void onBootComplete() { calls.push_back("boot.complete"); }
  void loop() { calls.push_back("board.loop"); }
} board;
struct Radio {
  void begin() { calls.push_back("radio.begin"); }
  unsigned getRngSeed() const { return 0; }
  void resetAGC() { calls.push_back("radio.agc"); }
  int recvRaw(uint8_t*, size_t) { calls.push_back("radio.recv"); return 0; }
  uint8_t receiveProfile() const { return 0; }
  float getLastSNR() const { return 0; }
  float getLastRSSI() const { return 0; }
  void onReceiveProcessed() {}
  void triggerNoiseFloorCalibrate(unsigned) { calls.push_back("radio.noise"); }
  void loop() { calls.push_back("radio.loop"); }
} radio_driver;
struct Rng { void begin(unsigned) {} } rng;
static int identity;
struct Sensors {
  void begin() {}
  void loop() { calls.push_back("sensors.loop"); }
} sensors;
struct RecoveryReached {};
static bool radio_init() {
#if defined(STATION_G3_ESP32)
  assert(serial_live && "empty-slot recovery must have USB before radio init");
  if (simulate_no_radio) {
    calls.push_back("radio.recovery");
    throw RecoveryReached();
  }
#endif
  return true;
}
static void loadOrCreateIdentity() { ++identity_loads; }
struct Filesystem { bool begin(bool format_if_empty) { assert(format_if_empty);return true; } } SPIFFS;
static void halt() { assert(false); }
static void onSetRadio() {}
static void onSetTxPower() {}
static void onGetCurrentRssi() {}
static void onGetStats() {}
class KissModem {
public:
  Stream* port;
  KissModem(Stream& stream, int&, Rng&, Radio&, Board&, Sensors&) : port(&stream) {
    calls.push_back("modem.construct");
  }
  template <typename T> void setRadioCallback(T) {}
  template <typename T> void setTxPowerCallback(T) {}
  template <typename T> void setGetCurrentRssiCallback(T) {}
  template <typename T> void setGetStatsCallback(T) {}
  void begin() { calls.push_back("modem.begin"); }
  void loop() { calls.push_back("modem.loop"); }
  void resetHostSession() { calls.push_back("modem.reset"); }
  bool isActuallyTransmitting() const { return true; }
  bool isHostOutputBackedUp() const { return false; }
  bool isTxBusy() const { return true; }
  void onPacketReceived(int8_t, int8_t, const uint8_t*, int, uint8_t) {}
};
static KissModem* modem = nullptr;
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define KISS_BOOT_STAGE(stage) ((void)0)
@TRANSPORT_SELECTION@
@LOOP_STATE@
@SETUP@
@LOOP@

int main() {
  setup();
  assert(modem != nullptr);
#if EXPECT_HWCDC
  assert(modem->port == &guarded_port);
#if defined(STATION_G3_ESP32)
  const std::vector<std::string> expected = {
      "logging.gate", "debug.gate", "prepare", "serial.begin", "begin.logging",
      "board.begin", "radio.begin", "modem.construct", "modem.begin", "boot.complete"};
#else
  const std::vector<std::string> expected = {
      "logging.gate", "debug.gate", "board.begin", "radio.begin", "prepare",
      "serial.begin", "begin.logging", "modem.construct", "modem.begin", "boot.complete"};
#endif
  assert(calls == expected);
#elif EXPECT_UART
  assert(modem->port == &Serial1);
  assert(Serial1.rx == KISS_UART_RX && Serial1.tx == KISS_UART_TX);
  assert(Serial1.baud == 115200);
#if defined(STATION_G3_ESP32)
  assert(serial_live);
  const std::vector<std::string> expected = {
      "prepare", "serial.begin", "begin.logging", "board.begin", "radio.begin",
      "uart.begin", "modem.construct", "modem.begin", "boot.complete"};
#else
  assert(!serial_live);
  const std::vector<std::string> expected = {
      "board.begin", "radio.begin", "uart.begin", "modem.construct", "modem.begin", "boot.complete"};
#endif
  assert(calls == expected);
#else
  assert(modem->port == &Serial);
  const std::vector<std::string> expected = {
      "board.begin", "radio.begin", "serial.begin", "modem.construct", "modem.begin", "boot.complete"};
  assert(calls == expected);
#endif
  // An in-flight radio send and the outer sketch services must continue while
  // native USB queue purging retries. The boundary is consumed only once.
  calls.clear();
  loop();
#if EXPECT_HWCDC
  assert(!usb_host_reset_pending);
  const std::vector<std::string> normal = {
      "usb.service", "usb.boundary", "modem.loop", "sensors.loop", "board.loop", "radio.loop"};
  assert(calls == normal);
  reset_event = true;
  calls.clear();
  loop();
  assert(usb_host_reset_pending);
  const std::vector<std::string> boundary = {
      "usb.service", "usb.boundary", "modem.reset", "usb.cleanup",
      "modem.loop", "sensors.loop", "board.loop", "radio.loop"};
  assert(calls == boundary);
  calls.clear();
  loop();
  assert(usb_host_reset_pending);
  const std::vector<std::string> retry = {
      "usb.service", "usb.boundary", "usb.cleanup", "modem.loop",
      "sensors.loop", "board.loop", "radio.loop"};
  assert(calls == retry);
  cleanup_ready = true;
  calls.clear();
  loop();
  assert(!usb_host_reset_pending && calls == retry);
  calls.clear();
  loop();
  assert(calls == normal);
#else
  const std::vector<std::string> normal = {
      "modem.loop", "sensors.loop", "board.loop", "radio.loop"};
  assert(calls == normal);
#endif
  delete modem;
#if defined(STATION_G3_ESP32)
  // Execute the same production setup with radio discovery suspended at its
  // recovery loop. Neither a working modem nor identity creation is required
  // to make the primary USB console reachable, even for UART-hosted KISS.
  modem = nullptr;
  const unsigned old_identity_loads = identity_loads;
  serial_live = false;
  calls.clear();
  simulate_no_radio = true;
  try { setup(); assert(false); } catch (const RecoveryReached&) {}
  assert(serial_live && modem == nullptr && identity_loads == old_identity_loads);
#if EXPECT_HWCDC
  const std::vector<std::string> recovery = {
      "logging.gate", "debug.gate", "prepare", "serial.begin", "begin.logging",
      "board.begin", "radio.recovery"};
#else
  const std::vector<std::string> recovery = {
      "prepare", "serial.begin", "begin.logging", "board.begin", "radio.recovery"};
#endif
  assert(calls == recovery);
#endif
}
