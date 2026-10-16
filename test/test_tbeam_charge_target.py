#!/usr/bin/env python3
"""Run the real T-Beam charge driver against fault-injected I2C and NVS."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HELPER_TEST = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "helpers/esp32/TBeamChargeTarget.h"
namespace charge = mesh::tbeam_charge;
struct IO {
  uint8_t reg[256] = {};
  unsigned reads = 0, writes = 0, failRead = 0, failWrite = 0;
  unsigned corruptRead = 0;
  bool read(uint8_t address, uint8_t& value) {
    if (++reads == failRead) return false;
    value = reg[address];
    if (reads == corruptRead) value ^= 1;
    return true;
  }
  bool write(uint8_t address, uint8_t value) {
    if (++writes == failWrite) return false;
    reg[address] = value;
    return true;
  }
};
struct Store {
  uint16_t value = 0;
  bool present = false;
  unsigned reads = 0, writes = 0, erases = 0;
  unsigned failRead = 0, failWrite = 0, failErase = 0;
  bool failedWriteCommits = false;
  bool read(uint16_t& out, bool& exists) {
    if (++reads == failRead) return false;
    out = value;
    exists = present;
    return true;
  }
  bool write(uint16_t requested) {
    const bool success = ++writes != failWrite;
    if (success || failedWriteCommits) { value = requested; present = true; }
    return success;
  }
  bool erase() {
    if (++erases == failErase) return false;
    present = false;
    return true;
  }
};
static IO initialized(uint8_t model) {
  IO io;
  io.reg[0x03] = model;
  io.reg[0x33] = 0xD9; // Charging enabled, 4.20 V, unrelated current/end bits.
  io.reg[0x64] = 0xBB; // 4.20 V, unrelated reserved/configuration bits.
  return io;
}
int main() {
  const uint16_t axp192[] = {4100,4150,4200,4360};
  const uint16_t axp2101[] = {4000,4100,4200,4350,4400};
  for (uint8_t model : {charge::AXP192, charge::AXP2101}) {
    const uint16_t* targets = model == charge::AXP192 ? axp192 : axp2101;
    const unsigned count = model == charge::AXP192 ? 4 : 5;
    for (unsigned index = 0; index < count; ++index) {
      IO io = initialized(model);
      const uint8_t address = model == charge::AXP192 ? 0x33 : 0x64;
      const uint8_t mask = model == charge::AXP192 ? 0x60 : 0x07;
      const uint8_t unrelated = io.reg[address] & ~mask;
      assert(charge::write(io, model, targets[index]));
      assert((io.reg[address] & ~mask) == unrelated);
      assert((io.reg[address] & mask) ==
             (model == charge::AXP192 ? index << 5 : index + 1));
      uint16_t actual = 7;
      assert(charge::read(io, model, actual) && actual == targets[index]);
    }
    for (uint16_t invalid : {uint16_t(0),uint16_t(3650),uint16_t(4099),
                            uint16_t(4199),uint16_t(4201),uint16_t(65535)}) {
      IO io = initialized(model);
      Store store;
      assert(!charge::setPersistent(io, store, model, invalid));
      assert(io.reads == 0 && io.writes == 0 && store.reads == 0);
    }
    const uint16_t target = targets[0];
    for (unsigned fail = 1; fail <= 5; ++fail) {
      IO io = initialized(model);
      io.failRead = fail;
      Store store;
      assert(!charge::setPersistent(io, store, model, target));
      assert(!store.present && store.writes == 0);
      uint16_t actual = 0;
      assert(charge::read(io, model, actual) && actual == 4200);
    }
    {
      IO io = initialized(model);
      io.failWrite = 1;
      Store store;
      assert(!charge::setPersistent(io, store, model, target));
      assert(!store.present && store.writes == 0);
    }
    {
      IO io = initialized(model);
      io.corruptRead = 5; // Setter's register readback, after physical write.
      Store store;
      assert(!charge::setPersistent(io, store, model, target));
      uint16_t actual = 0;
      assert(charge::read(io, model, actual) && actual == 4200);
      assert(!store.present && store.writes == 0);
    }
    {
      IO io = initialized(model);
      Store store;
      store.failRead = 1;
      assert(!charge::setPersistent(io, store, model, target));
      assert(io.writes == 0 && store.writes == 0);
    }
    for (unsigned fail = 1; fail <= 3; ++fail) {
      IO io = initialized(model);
      assert(charge::write(io, model, 4100));
      io.reads = io.writes = 0;
      io.failRead = fail;
      Store store;
      store.present = true;
      store.value = target;
      assert(!charge::configureBoot(io, store, model, true));
      assert(store.present && store.value == target && store.writes == 0 && store.erases == 0);
      uint16_t actual = 0;
      assert(charge::read(io, model, actual));
      assert(actual == (fail < 3 ? 4100 : target));
      assert(actual != 4200); // A failed boot restore cannot use the default.
    }
    for (bool existed : {false, true}) {
      for (bool failedCommit : {false, true}) {
        IO io = initialized(model);
        Store store;
        store.present = existed;
        store.value = 4200;
        store.failWrite = 1;
        store.failedWriteCommits = failedCommit;
        assert(!charge::setPersistent(io, store, model, target));
        assert(store.present == existed && (!existed || store.value == 4200));
        uint16_t actual = 0;
        assert(charge::read(io, model, actual) && actual == 4200);
      }
    }
    {
      IO io = initialized(model);
      Store store;
      store.failWrite = 1;
      store.failedWriteCommits = true;
      store.failErase = 1; // Persistent rollback fails after a partial commit.
      assert(!charge::setPersistent(io, store, model, target));
      assert(store.present && store.value == target);
      uint16_t actual = 0;
      assert(charge::read(io, model, actual) && actual == target);
    }
    {
      IO io = initialized(model);
      Store store;
      assert(charge::setPersistent(io, store, model, target));
      assert(store.present && store.value == target && store.writes == 1);
      const unsigned writes = io.writes;
      assert(charge::setPersistent(io, store, model, target));
      assert(store.writes == 1 && io.writes == writes);
      IO rebooted = initialized(model);
      assert(charge::configureBoot(rebooted, store, model, false));
      uint16_t actual = 0;
      assert(charge::read(rebooted, model, actual) && actual == target);
    }
    {
      IO io = initialized(model);
      Store store;
      assert(charge::configureBoot(io, store, model, false));
      store.present = true;
      store.value = 3650;
      assert(!charge::configureBoot(io, store, model, true));
      assert(io.reads == 0 && io.writes == 0);
    }
    {
      IO io = initialized(model);
      Store store;
      assert(charge::write(io, model, targets[0]));
      store.failRead = 1;
      const unsigned previousWrites = io.writes;
      assert(!charge::configureBoot(io, store, model, true));
      assert(io.writes == previousWrites);
      uint16_t actual = 0;
      assert(charge::read(io, model, actual) && actual == targets[0]);
    }
    {
      IO io = initialized(model);
      Store store;
      assert(charge::write(io, model, targets[0]));
      const unsigned previousWrites = io.writes;
      assert(charge::configureBoot(io, store, model, false));
      assert(io.writes == previousWrites);
      assert(charge::configureBoot(io, store, model, true));
      uint16_t actual = 0;
      assert(charge::read(io, model, actual) && actual == 4200);
    }
  }
  assert(std::strcmp(charge::storeNamespace(charge::AXP192),
                     charge::storeNamespace(charge::AXP2101)) != 0);
  for (uint8_t code : {uint8_t(0),uint8_t(6),uint8_t(7)}) {
    IO io = initialized(charge::AXP2101);
    io.reg[0x64] = code;
    uint16_t target = 17;
    assert(!charge::read(io, charge::AXP2101, target) && target == 17);
  }
  {
    IO io = initialized(0xFF);
    Store store;
    assert(!charge::setPersistent(io, store, 0xFF, 4200));
    assert(io.reads == 0 && io.writes == 0 && store.reads == 0);
    assert(charge::options(0xFF) == nullptr);
  }
  {
    IO io = initialized(charge::AXP2101);
    uint16_t target = 17;
    assert(!charge::read(io, charge::AXP192, target) && target == 17);
    assert(!charge::write(io, charge::AXP192, 4100) && io.writes == 0);
  }
}
'''

MOCK_SDK = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
constexpr int LOW = 0, HIGH = 1, INPUT = 2, INPUT_PULLUP = 3, FALLING = 4;
constexpr int ESP_RST_DEEPSLEEP = 5, ESP_SLEEP_WAKEUP_UNDEFINED = 0;
constexpr int BD_STARTUP_RX_PACKET = 1;
using esp_reset_reason_t = int;
using gpio_num_t = int;
extern int reset_reason;
extern uint64_t wakeup_mask;
inline int esp_reset_reason() { return reset_reason; }
inline int esp_sleep_get_wakeup_cause() { return 0; }
inline uint64_t esp_sleep_get_ext1_wakeup_status() { return wakeup_mask; }
inline bool rtc_gpio_is_valid_gpio(int pin) {
  return pin == 0 || pin == 2 || pin == 4 || (pin >= 12 && pin <= 15)
      || (pin >= 25 && pin <= 27) || (pin >= 32 && pin <= 39);
}
inline int rtc_gpio_hold_dis(int) { return 0; }
inline int rtc_gpio_hold_en(int) { return 0; }
inline int rtc_gpio_deinit(int) { return 0; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline void attachInterrupt(int, void(*)(), int) {}
inline void delay(int) {}
#define _BV(x) (1U << (x))
#define MESH_DEBUG_PRINTLN(...) do {} while (0)
struct TwoWire {
  uint8_t reg[256] = {};
  std::vector<uint8_t> tx;
  uint8_t address = 0;
  unsigned writes = 0;
  bool negative_read_once = false;
  bool target_write_fails_once = false;
  void begin(int, int) {}
  void beginTransmission(int) { tx.clear(); }
  void write(uint8_t value) { tx.push_back(value); }
  int endTransmission(bool = true) {
    if (tx.size() == 1) address = tx[0];
    if (tx.size() == 2) {
      if (target_write_fails_once && (tx[0] == 0x33 || tx[0] == 0x64)) {
        target_write_fails_once = false;
        return 1;
      }
      reg[tx[0]] = tx[1];
      ++writes;
    }
    return 0;
  }
  int requestFrom(int, int count) { return count; }
  int read() {
    if (negative_read_once) { negative_read_once = false; return -1; }
    return reg[address++];
  }
};
extern TwoWire Wire, Wire1;
class ESP32Board {
public:
  int startup_reason = 0;
  virtual ~ESP32Board() {}
  void begin() {}
  virtual void onBeforeTransmit() {}
  virtual void onAfterTransmit() {}
  virtual bool getBatteryChargeTarget(uint16_t&) { return false; }
  virtual bool supportsBatteryChargeTarget(uint16_t) { return false; }
  virtual bool setBatteryChargeTarget(uint16_t) { return false; }
  virtual const char* getBatteryChargeTargetOptions() const { return nullptr; }
  virtual bool batteryChargeTargetRestoreFailed() const { return false; }
};
constexpr int XPOWERS_AXP192 = 192, XPOWERS_AXP2101 = 2101;
constexpr int XPOWERS_LDO2 = 0, XPOWERS_LDO3 = 0, XPOWERS_DCDC1 = 0;
constexpr int XPOWERS_DCDC2 = 0, XPOWERS_DCDC3 = 0, XPOWERS_DCDC4 = 0;
constexpr int XPOWERS_DCDC5 = 0, XPOWERS_ALDO1 = 0, XPOWERS_ALDO2 = 0;
constexpr int XPOWERS_ALDO3 = 0, XPOWERS_ALDO4 = 0, XPOWERS_BLDO1 = 0;
constexpr int XPOWERS_BLDO2 = 0, XPOWERS_VBACKUP = 0, XPOWERS_CHG_LED_CTRL_CHG = 0;
constexpr int XPOWERS_DLDO1 = 0, XPOWERS_DLDO2 = 0;
constexpr int XPOWERS_AXP2101_DCDC4_VOL2_MAX = 0, XPOWERS_AXP192_ALL_IRQ = 0;
constexpr int XPOWERS_AXP2101_ALL_IRQ = 0, XPOWERS_AXP192_CHG_CUR_450MA = 0;
constexpr int XPOWERS_AXP2101_CHG_CUR_500MA = 0, XPOWERS_POWEROFF_4S = 0;
constexpr int XPOWERS_AXP192_CHG_VOL_4V2 = 2, XPOWERS_AXP2101_CHG_VOL_4V2 = 3;
class XPowersLibInterface {
  TwoWire& bus;
  int model;
public:
  XPowersLibInterface(TwoWire& wire, int type) : bus(wire), model(type) {}
  virtual ~XPowersLibInterface() {}
  bool init() { return bus.reg[0x03] == (model == XPOWERS_AXP192 ? 0x03 : 0x4A); }
  int getChipModel() const { return model; }
  uint16_t getBattVoltage() { return 4200; }
  void setChargingLedMode(int) {}
  void setPowerChannelVoltage(int, int) {}
  void enablePowerOutput(int) {}
  void disablePowerOutput(int) {}
  void setProtectedChannel(int) {}
  void disableIRQ(int) {}
  void setChargerConstantCurr(int) {}
  void setChargeTargetVoltage(uint8_t code) {
    if (model == XPOWERS_AXP192) bus.reg[0x33] = (bus.reg[0x33] & 0x9F) | (code << 5);
    else bus.reg[0x64] = (bus.reg[0x64] & 0xF8) | code;
  }
  void clearIrqStatus() {}
  void disableTSPinMeasure() {}
  void enableSystemVoltageMeasure() {}
  void enableVbusVoltageMeasure() {}
  void enableBattVoltageMeasure() {}
  void setPowerKeyPressOffTime(int) {}
};
struct XPowersAXP192 : XPowersLibInterface {
  XPowersAXP192(TwoWire& bus, int, int, int) : XPowersLibInterface(bus, XPOWERS_AXP192) {}
};
struct XPowersAXP2101 : XPowersLibInterface {
  XPowersAXP2101(TwoWire& bus, int, int, int) : XPowersLibInterface(bus, XPOWERS_AXP2101) {}
};
using esp_err_t = int;
using nvs_handle_t = uint32_t;
constexpr int ESP_OK = 0, ESP_FAIL = 1, ESP_ERR_NVS_NOT_FOUND = 2;
constexpr int ESP_ERR_INVALID_ARG=258;
constexpr int GPIO_MODE_OUTPUT=2,GPIO_PULLUP_DISABLE=0,GPIO_PULLDOWN_DISABLE=0,GPIO_INTR_DISABLE=0;
#define GPIO_IS_VALID_OUTPUT_GPIO(pin) ((pin)>=0 && (pin)<49)
struct gpio_config_t { uint64_t pin_bit_mask;int mode,pull_up_en,pull_down_en,intr_type; };
inline int gpio_set_level(int,int) { return 0; }
inline int gpio_config(const gpio_config_t*) { return 0; }
inline int gpio_hold_en(int) { return 0; }
inline int gpio_hold_dis(int) { return 0; }
inline void gpio_deep_sleep_hold_en() {}
inline void gpio_deep_sleep_hold_dis() {}
constexpr int NVS_READONLY = 0, NVS_READWRITE = 1;
extern std::map<std::string,uint16_t> saved;
extern std::string opened_namespace;
extern bool nvs_open_fails, nvs_commit_fails_once;
extern bool nvs_set_fails_once, nvs_erase_fails_once;
extern unsigned nvs_reads, nvs_fail_read_at;
inline int nvs_open(const char* name, int mode, nvs_handle_t* handle) {
  if (nvs_open_fails) return ESP_FAIL;
  if (mode == NVS_READONLY && saved.find(name) == saved.end()) return ESP_ERR_NVS_NOT_FOUND;
  opened_namespace = name;
  *handle = 1;
  return ESP_OK;
}
inline void nvs_close(nvs_handle_t) {}
inline int nvs_get_u16(nvs_handle_t, const char*, uint16_t* value) {
  if (++nvs_reads == nvs_fail_read_at) return ESP_FAIL;
  auto found = saved.find(opened_namespace);
  if (found == saved.end()) return ESP_ERR_NVS_NOT_FOUND;
  *value = found->second;
  return ESP_OK;
}
inline int nvs_set_u16(nvs_handle_t, const char*, uint16_t value) {
  if (nvs_set_fails_once) { nvs_set_fails_once = false; return ESP_FAIL; }
  saved[opened_namespace] = value;
  return ESP_OK;
}
inline int nvs_commit(nvs_handle_t) {
  if (nvs_commit_fails_once) { nvs_commit_fails_once = false; return ESP_FAIL; }
  return ESP_OK;
}
inline int nvs_erase_key(nvs_handle_t, const char*) {
  if (nvs_erase_fails_once) { nvs_erase_fails_once = false; return ESP_FAIL; }
  return saved.erase(opened_namespace) ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}
'''

BOARD_TEST = r'''
#include "helpers/esp32/TBeamBoard.h"
#include <cstring>
TwoWire Wire, Wire1;
int reset_reason = 0;
uint64_t wakeup_mask = 0;
std::map<std::string,uint16_t> saved;
std::string opened_namespace;
bool nvs_open_fails = false, nvs_commit_fails_once = false;
bool nvs_set_fails_once = false, nvs_erase_fails_once = false;
unsigned nvs_reads = 0, nvs_fail_read_at = 0;
#ifdef TBEAM_SUPREME_SX1262
#define BUS Wire1
#else
#define BUS Wire
#endif
int main() {
  for (uint8_t model : {uint8_t(0x03),uint8_t(0x4A)}) {
    saved.clear();
    BUS.reg[0x03] = model;
    BUS.reg[0x33] = 0x99;
    BUS.reg[0x64] = 0xBA;
    const char* own_namespace = model == 0x03 ? "tbeam192_chg" : "tbeam2101_chg";
    const char* other_namespace = model == 0x03 ? "tbeam2101_chg" : "tbeam192_chg";
    TBeamBoard board;
    assert(board.getBattMilliVolts() == 0);
    board.begin();
    uint16_t actual = 0;
    assert(board.getBatteryChargeTarget(actual));
#if defined(PORTABLE_MQTT_OBSERVER) && !defined(TBEAM_SUPREME_SX1262)
    assert(actual == 4100); // Fresh compact boot leaves charger target alone.
#else
    assert(actual == 4200); // Existing full-library default is preserved.
#endif
    assert(saved.empty());
    assert(!board.batteryChargeTargetRestoreFailed());
    const unsigned previous_writes = BUS.writes;
    assert(!board.setBatteryChargeTarget(3650));
    assert(BUS.writes == previous_writes && saved.empty());
    assert(board.supportsBatteryChargeTarget(4100));
    assert(!board.supportsBatteryChargeTarget(3650));
    assert(std::strcmp(board.getBatteryChargeTargetOptions(), model == 0x03 ?
        "4.10,4.15,4.20,4.36" : "4.00,4.10,4.20,4.35,4.40") == 0);
    assert(board.setBatteryChargeTarget(4100));
    assert(saved.at(own_namespace) == 4100);
    assert(saved.find(other_namespace) == saved.end());
    assert(board.getBatteryChargeTarget(actual) && actual == 4100);
    reset_reason = ESP_RST_DEEPSLEEP;
    wakeup_mask = 1ULL << P_LORA_DIO_1;
    TBeamBoard radio_wake;
    radio_wake.begin();
    assert(radio_wake.startup_reason == BD_STARTUP_RX_PACKET);
    reset_reason = 0;
    wakeup_mask = 0;
    nvs_commit_fails_once = true;
    assert(!board.setBatteryChargeTarget(4200));
    assert(saved.at(own_namespace) == 4100);
    assert(board.getBatteryChargeTarget(actual) && actual == 4100);
    const unsigned saved_writes = BUS.writes;
    nvs_fail_read_at = nvs_reads + 1; // Initial saved-state snapshot fails.
    assert(!board.setBatteryChargeTarget(4200));
    assert(BUS.writes == saved_writes && saved.at(own_namespace) == 4100);
    nvs_fail_read_at = nvs_reads + 2; // Save commits, but verification read fails.
    assert(!board.setBatteryChargeTarget(4200));
    assert(saved.at(own_namespace) == 4100);
    assert(board.getBatteryChargeTarget(actual) && actual == 4100);
    nvs_fail_read_at = 0;
    nvs_set_fails_once = true;
    assert(!board.setBatteryChargeTarget(4200));
    assert(saved.at(own_namespace) == 4100);
    assert(board.getBatteryChargeTarget(actual) && actual == 4100);
    BUS.negative_read_once = true;
    actual = 17;
    assert(!board.getBatteryChargeTarget(actual) && actual == 17);
    saved.erase(own_namespace);
    nvs_commit_fails_once = true;
    nvs_erase_fails_once = true; // Save and rollback both have uncertain results.
    assert(!board.setBatteryChargeTarget(4200));
    assert(saved.at(own_namespace) == 4200);
    assert(board.getBatteryChargeTarget(actual) && actual == 4200);
    assert(board.setBatteryChargeTarget(4100));
    nvs_open_fails = true;
    assert(!board.setBatteryChargeTarget(4200));
    nvs_open_fails = false;
    assert(board.getBatteryChargeTarget(actual) && actual == 4100);
    TBeamBoard rebooted;
    rebooted.begin();
    assert(rebooted.getBatteryChargeTarget(actual) && actual == 4100);
    assert(!rebooted.batteryChargeTargetRestoreFailed());
    saved[own_namespace] = 3650;
    BUS.reg[0x33] = 0x99;
    BUS.reg[0x64] = 0xBA;
    TBeamBoard invalid_saved;
    invalid_saved.begin();
    assert(invalid_saved.getBatteryChargeTarget(actual) && actual == 4100);
    assert(invalid_saved.batteryChargeTargetRestoreFailed());
    assert(!invalid_saved.setBatteryChargeTarget(3650));
    assert(invalid_saved.batteryChargeTargetRestoreFailed());
    assert(invalid_saved.setBatteryChargeTarget(4100));
    assert(!invalid_saved.batteryChargeTargetRestoreFailed());
    saved[own_namespace] = 4100;
    nvs_open_fails = true;
    TBeamBoard unreadable_namespace;
    unreadable_namespace.begin();
    nvs_open_fails = false;
    assert(unreadable_namespace.getBatteryChargeTarget(actual) && actual == 4100);
    assert(unreadable_namespace.batteryChargeTargetRestoreFailed());
    nvs_fail_read_at = nvs_reads + 1;
    TBeamBoard unreadable_entry;
    unreadable_entry.begin();
    nvs_fail_read_at = 0;
    assert(unreadable_entry.getBatteryChargeTarget(actual) && actual == 4100);
    assert(unreadable_entry.batteryChargeTargetRestoreFailed());
    // A validated lower target fails to write; preserve the existing lower
    // PMU target, never invoke the historical full-library 4.20 V setter.
    saved[own_namespace] = model == 0x03 ? 4100 : 4000;
    BUS.reg[0x33] = 0xB9; // 4.15 V.
    BUS.reg[0x64] = 0xBA; // 4.10 V.
    BUS.target_write_fails_once = true;
    TBeamBoard failed_restore;
    failed_restore.begin();
    assert(!BUS.target_write_fails_once);
    assert(failed_restore.getBatteryChargeTarget(actual));
    assert(actual == (model == 0x03 ? 4150 : 4100));
    assert(failed_restore.batteryChargeTargetRestoreFailed());
    assert(failed_restore.setBatteryChargeTarget(4100));
    assert(!failed_restore.batteryChargeTargetRestoreFailed());
    saved.erase(own_namespace);
    saved[other_namespace] = 4150;
    TBeamBoard wrong_model_saved;
    wrong_model_saved.begin();
    assert(wrong_model_saved.getBatteryChargeTarget(actual));
#if defined(PORTABLE_MQTT_OBSERVER) && !defined(TBEAM_SUPREME_SX1262)
    assert(actual == 4100);
#else
    assert(actual == 4200);
#endif
    assert(!wrong_model_saved.batteryChargeTargetRestoreFailed());
  }
  saved.clear();
  BUS.reg[0x03] = 0xFF;
  const unsigned writes = BUS.writes;
  TBeamBoard unknown;
  unknown.begin();
  assert(unknown.getBattMilliVolts() == 0);
  uint16_t actual = 17;
  assert(!unknown.getBatteryChargeTarget(actual) && actual == 17);
  assert(!unknown.supportsBatteryChargeTarget(4200));
  assert(!unknown.setBatteryChargeTarget(4200));
  assert(unknown.getBatteryChargeTargetOptions() == nullptr);
  assert(BUS.writes == writes && saved.empty());
}
'''


class TBeamChargeTargetTest(unittest.TestCase):
    def setUp(self):
        self.compiler = shutil.which("g++")
        if not self.compiler:
            self.skipTest("g++ is required")

    def build_and_run(self, directory, source, extra=()):
        harness = directory / "harness.cpp"
        harness.write_text(source, encoding="ascii")
        binary = directory / "harness"
        command = [self.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                   # Production retains editor folding pragmas that GCC does
                   # not implement. Keep real compiler warnings fatal.
                   "-Wno-unknown-pragmas",
                   "-I", str(directory), "-I", str(ROOT / "src"), str(harness),
                   *extra, "-o", str(binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_discrete_targets_and_persistent_transaction_faults(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-tbeam-charge-") as temporary:
            self.build_and_run(Path(temporary), "#include <initializer_list>\n" + HELPER_TEST)

    def test_real_driver_full_compact_and_supreme_paths(self):
        for board, compact in (("TBEAM_SX1262", False), ("TBEAM_SX1276", False),
                               ("TBEAM_SUPREME_SX1262", False),
                               ("TBEAM_SX1262", True), ("TBEAM_SX1276", True)):
            with self.subTest(board=board, compact=compact):
                with tempfile.TemporaryDirectory(prefix="meshcore-tbeam-driver-") as temporary:
                    directory = Path(temporary)
                    (directory / "MockSdk.h").write_text(MOCK_SDK, encoding="ascii")
                    for name in ("Arduino.h", "Wire.h", "XPowersLib.h", "nvs.h",
                                 "helpers/ESP32Board.h", "driver/rtc_io.h", "driver/gpio.h"):
                        stub = directory / name
                        stub.parent.mkdir(parents=True, exist_ok=True)
                        stub.write_text('#include "MockSdk.h"\n', encoding="ascii")
                    definitions = ["-D" + board, "-DPIN_BOARD_SDA=21", "-DPIN_BOARD_SCL=22",
                                   "-DPIN_USER_BTN=38", "-DP_LORA_TX_LED=4",
                                   "-DP_LORA_DIO_1=33", "-DP_LORA_NSS=18"]
                    if board == "TBEAM_SUPREME_SX1262":
                        definitions = definitions[:-2]  # The real header supplies these pins.
                    if compact:
                        definitions.append("-DPORTABLE_MQTT_OBSERVER")
                    self.build_and_run(directory, BOARD_TEST, [*definitions,
                        str(ROOT / "src/helpers/esp32/TBeamBoard.cpp")])


if __name__ == "__main__":
    unittest.main()
