#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define RADIOLIB_SX126X_CALIBRATE_ALL 0x7f
#define RADIOLIB_SX126X_CMD_CALIBRATE 0x89
#define RADIOLIB_SX126X_STANDBY_RC 0
inline uint32_t millis() { return 0; }
struct Hal { void delay(unsigned) {} int digitalRead(int) { return 0; } void yield() {} };
struct Module {
  Hal storage; Hal* hal = &storage;
  int getGpio() { return 13; }
  int16_t SPIwriteStream(int, uint8_t*, int, bool, bool) { return 0; }
};
struct SX126x {
  uint8_t regs[4096] = {};
  Module storage; Module* mod = &storage; float freqMHz = 909.95f;
  unsigned operations = 0, writes = 0, fail_at = 0;
  uint16_t drop_write = 0;
  bool last_gain_persist = true;
  SX126x() { regs[0x08B5] = 0x04; regs[0x08AC] = 0x96; }
  int16_t readRegister(uint16_t address, uint8_t* out, size_t length) {
    assert(address + length <= sizeof(regs));
    if (++operations == fail_at) { memset(out, 0xff, length); return -77; }
    memcpy(out, regs + address, length); return 0;
  }
  int16_t writeRegister(uint16_t address, uint8_t* data, size_t length) {
    assert(address + length <= sizeof(regs)); ++writes;
    if (++operations == fail_at) return -77;
    if (address != drop_write) memcpy(regs + address, data, length);
    return 0;
  }
  int16_t setRxBoostedGainMode(bool gain, bool persist = true) {
    last_gain_persist = persist;
    uint8_t value = gain ? 0x96 : 0x94;
    int16_t status = writeRegister(0x08AC, &value, 1);
    if (status || !persist) return status;
    uint8_t list[] = {1, 8, 0xAC};
    return writeRegister(0x029F, list, sizeof(list));
  }
  bool contains(uint16_t address) const {
    for (unsigned i = 0; i < regs[0x029F]; ++i)
      if ((unsigned(regs[0x02A0 + 2*i]) << 8 | regs[0x02A1 + 2*i]) == address) return true;
    return false;
  }
  void retain(uint16_t address) {
    unsigned n = regs[0x029F]; assert(n < 4);
    regs[0x02A0 + 2*n] = uint8_t(address >> 8); regs[0x02A1 + 2*n] = uint8_t(address);
    ++regs[0x029F];
  }
  int16_t sleep(bool warm) {
    // Model the bench-observed loss of unretained receiver registers.
    if (!warm || !contains(0x08B5)) regs[0x08B5] = 4;
    if (!warm || !contains(0x08AC)) regs[0x08AC] = 0x94;
    if (!warm || !contains(0x0736)) regs[0x0736] = 0;
    return 0;
  }
  int16_t standby(uint8_t, bool) { return 0; }
  int16_t calibrateImage(float) { return 0; }
  int16_t setDio2AsRfSwitch(bool) { return 0; }
};
using SX1262 = SX126x;
