#!/usr/bin/env python3
"""Execute production RTC discovery with register, bus and initialization faults."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <vector>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
namespace mesh {
struct RTCClock {
  virtual ~RTCClock() = default;
  virtual uint32_t getCurrentTime() = 0;
  virtual void setCurrentTime(uint32_t) = 0;
  virtual void tick() {}
};
}
struct TwoWire {
  struct Device {
    std::array<uint8_t, 256> registers{};
    unsigned failed_reads = 0, short_reads = 0, empty_reads = 0;
    bool initialize_ok = true;
    std::deque<std::vector<uint8_t>> scripted_reads;
    std::vector<uint8_t> read_registers;
  };
  std::map<uint8_t, Device> devices;
  std::vector<uint8_t> initialized, configured;
  uint8_t address = 0, reg = 0;
  std::vector<uint8_t> transmit, receive;
  size_t read_index = 0;
  bool all_reads_repeated_start = true;
  void beginTransmission(uint8_t addr) { address = addr; transmit.clear(); }
  size_t write(uint8_t value) { transmit.push_back(value); return 1; }
  uint8_t endTransmission(bool stop = true) {
    if (!devices.count(address)) return 2;
    if (!transmit.empty()) {
      reg = transmit[0];
      all_reads_repeated_start = all_reads_repeated_start && !stop;
    }
    return 0;
  }
  uint8_t requestFrom(uint8_t addr, uint8_t n) {
    auto& dev = devices.at(addr);
    dev.read_registers.push_back(reg);
    receive.clear(); read_index = 0;
    if (dev.failed_reads) { --dev.failed_reads; return 0; }
    if (dev.short_reads) { --dev.short_reads; return n - 1; }
    if (dev.empty_reads) { --dev.empty_reads; return n; }
    if (!dev.scripted_reads.empty()) {
      receive = dev.scripted_reads.front(); dev.scripted_reads.pop_front();
      return receive.size();
    }
    for (unsigned i = 0; i < n; ++i) receive.push_back(dev.registers[reg + i]);
    return n;
  }
  int read() { return read_index < receive.size() ? receive[read_index++] : -1; }
};
struct DateTime {
  uint32_t epoch;
  explicit DateTime(uint32_t value = 0) : epoch(value) {}
  DateTime(int, int, int, int, int, int) : epoch(5200) {}
  uint32_t unixtime() const { return epoch; }
  int year() const { return 2026; }
  int month() const { return 10; }
  int dayOfTheWeek() const { return 5; }
  int day() const { return 9; }
  int hour() const { return 12; }
  int minute() const { return 30; }
  int second() const { return 45; }
};
template<uint8_t Addr, uint32_t Epoch> struct FakeRTC {
  TwoWire* wire = nullptr;
  bool begin(TwoWire* value) {
    wire = value; wire->initialized.push_back(Addr);
    return wire->devices.at(Addr).initialize_ok;
  }
  DateTime now() { return DateTime(Epoch); }
  void adjust(DateTime) { wire->configured.push_back(Addr); }
};
using RTC_DS3231 = FakeRTC<0x68, 6800>;
using RTC_PCF8563 = FakeRTC<0x51, 5100>;
using RTC_RX8130CE = FakeRTC<0x32, 3200>;
struct Melopero_RV3028 {
  TwoWire* wire = nullptr;
  void initI2C(TwoWire& value) { wire = &value; wire->initialized.push_back(0x52); }
  void writeToRegister(int, int) { wire->configured.push_back(0x52); }
  void set24HourMode() { wire->configured.push_back(0x52); }
  void setTime(int, int, int, int, int, int, int) { wire->configured.push_back(0x52); }
  int getYear() const { return 2026; }
  int getMonth() const { return 10; }
  int getDate() const { return 9; }
  int getHour() const { return 12; }
  int getMinute() const { return 30; }
  int getSecond() const { return 45; }
};
struct Fallback : mesh::RTCClock {
  uint32_t epoch = 1234;
  uint32_t getCurrentTime() override { return epoch; }
  void setCurrentTime(uint32_t value) override { epoch = value; }
};
@HEADER@
@SOURCE@
struct Kind { uint8_t addr; const RtcId* id; uint32_t epoch; };
static void makeValid(TwoWire& wire, const Kind& kind) {
  auto& regs = wire.devices[kind.addr].registers;
  regs[kind.id->time_reg] = 0x45;
  regs[kind.id->time_reg + 1] = 0x30;
  regs[kind.id->time_reg + 2] = 0x12;
  regs[kind.id->time_reg + 3] = 0x01;
  regs[kind.id->time_reg + kind.id->date_idx] = 0x09;
  regs[kind.id->time_reg + 5] = 0x10;
  regs[kind.id->time_reg + 6] = 0x26;
}
static void expectRejected(TwoWire& wire, Fallback& fallback) {
  AutoDiscoverRTCClock clock(fallback);
  clock.begin(wire);
  assert(wire.initialized.empty() && wire.configured.empty());
  assert(clock.getCurrentTime() == fallback.epoch);
  clock.setCurrentTime(9876);
  assert(fallback.epoch == 9876 && wire.configured.empty());
  fallback.epoch = 1234;
}
int main() {
  Fallback fallback;
  const Kind kinds[] = {
#if !defined(DISABLE_DS3231_PROBE)
    {0x68, &DS3231_ID, 6800},
#endif
    {0x52, &RV3028_ID, 5200}, {0x51, &PCF8563_ID, 5100}, {0x32, &RX8130CE_ID, 3200}
  };
  unsigned checks = 0;
  for (auto kind : kinds) {
    TwoWire valid; makeValid(valid, kind);
    AutoDiscoverRTCClock clock(fallback); clock.begin(valid);
    assert(valid.initialized == std::vector<uint8_t>{kind.addr});
    assert(clock.getCurrentTime() == kind.epoch);
    assert(valid.all_reads_repeated_start);
    clock.setCurrentTime(77);
    assert(valid.configured.back() == kind.addr && fallback.epoch == 1234);
    ++checks;
    // A second begin must discard a previous successful discovery.
    TwoWire absent; clock.begin(absent);
    assert(clock.getCurrentTime() == fallback.epoch); ++checks;
    TwoWire erased; erased.devices[kind.addr].registers.fill(0xFF);
    expectRejected(erased, fallback); ++checks;
    for (unsigned field = 0; field < 7; ++field) for (unsigned bit = 0; bit < 8; ++bit) {
      if (!(kind.id->zero[field] & (1U << bit))) continue;
      TwoWire wrong; makeValid(wrong, kind);
      wrong.devices[kind.addr].registers[kind.id->time_reg + field] |= 1U << bit;
      expectRejected(wrong, fallback); ++checks;
    }
    for (unsigned fault = 0; fault < 3; ++fault) {
      TwoWire failed; makeValid(failed, kind);
      auto& dev = failed.devices[kind.addr];
      if (fault == 0) dev.failed_reads = 2;
      if (fault == 1) dev.short_reads = 2;
      if (fault == 2) dev.empty_reads = 2;
      expectRejected(failed, fallback); ++checks;
      TwoWire recovered; makeValid(recovered, kind);
      recovered.devices[kind.addr].failed_reads = 1;
      clock.begin(recovered);
      assert(clock.getCurrentTime() == kind.epoch); ++checks;
    }
    if (kind.id->flag_bit) {
      const std::pair<unsigned, uint8_t> bad_fields[] = {
        {0, 0x60}, {0, 0x1A}, {1, 0x60}, {kind.id->date_idx, 0},
        {kind.id->date_idx, 0x32}, {5, 0}, {5, 0x13}
      };
      for (auto bad : bad_fields) {
        TwoWire invalid; makeValid(invalid, kind);
        invalid.devices[kind.addr].registers[kind.id->time_reg + bad.first] = bad.second;
        expectRejected(invalid, fallback); ++checks;
      }
      // First power-up permits undefined BCD fields when power loss is flagged.
      TwoWire lost; makeValid(lost, kind);
      lost.devices[kind.addr].registers[kind.id->time_reg] = 0;
      lost.devices[kind.addr].registers[kind.id->time_reg + kind.id->date_idx] = 0;
      lost.devices[kind.addr].registers[kind.id->flag_reg] |= kind.id->flag_bit;
      clock.begin(lost);
      assert(clock.getCurrentTime() == kind.epoch); ++checks;
    }
    if (kind.addr != 0x52) {
      TwoWire init_failed; makeValid(init_failed, kind);
      init_failed.devices[kind.addr].initialize_ok = false;
      clock.begin(init_failed);
      assert(clock.getCurrentTime() == fallback.epoch); ++checks;
    }
  }
#if !defined(DISABLE_DS3231_PROBE)
  // DS1307 0Fh is ordinary RAM, unlike DS3231's OSF status register. Startup
  // identification and later initialization must not depend on that RAM byte.
  for (uint8_t ram : {0, 0x80, 0xFF}) {
    const uint8_t typical_time[] = {0x80, 0, 0, 1, 1, 1, 0};
    TwoWire startup;
    auto& dev = startup.devices[0x68];
    std::copy(std::begin(typical_time), std::end(typical_time), dev.registers.begin());
    dev.registers[0x0F] = ram;
    AutoDiscoverRTCClock clock(fallback); clock.begin(startup);
    assert(startup.initialized == std::vector<uint8_t>{0x68});
    assert(dev.read_registers == std::vector<uint8_t>{0});
    clock.setCurrentTime(9876);
    assert(startup.configured == std::vector<uint8_t>{0x68}); ++checks;

    TwoWire halted;
    auto& stopped = halted.devices[0x68];
    stopped.registers[0] = 0x80; stopped.registers[3] = 1;
    stopped.registers[0x0F] = ram; // Date/month intentionally remain uninitialized.
    clock.begin(halted);
    assert(halted.initialized == std::vector<uint8_t>{0x68});
    assert(stopped.read_registers == std::vector<uint8_t>{0});
    clock.setCurrentTime(9876);
    assert(halted.configured == std::vector<uint8_t>{0x68}); ++checks;
  }
  // With CH clear and no OSF-shaped RAM bit, the invalid fields still reject.
  TwoWire running_invalid;
  running_invalid.devices[0x68].registers[3] = 1;
  expectRejected(running_invalid, fallback); ++checks;
  // The clock-halt exception never overrides reserved-bit or all-FF checks.
  for (unsigned field = 0; field < 7; ++field) for (unsigned bit = 0; bit < 8; ++bit) {
    if (!(DS3231_ID.zero[field] & (1U << bit))) continue;
    TwoWire reserved;
    reserved.devices[0x68].registers[0] = 0x80;
    reserved.devices[0x68].registers[field] |= 1U << bit;
    expectRejected(reserved, fallback); ++checks;
  }
  TwoWire erased_halted; erased_halted.devices[0x68].registers.fill(0xFF);
  expectRejected(erased_halted, fallback); ++checks;
#endif
#ifdef DISABLE_DS3231_PROBE
  TwoWire ignored; ignored.devices[0x68].registers.fill(0xFF);
  expectRejected(ignored, fallback); ++checks;
#endif
  printf("%u RTC discovery and fault checks passed\n", checks);
}
'''


class RtcDiscoveryTests(unittest.TestCase):
    def test_production_discovery_and_disabled_ds3231(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = (ROOT / "src/helpers/AutoDiscoverRTCClock.cpp").read_text()
        header = (ROOT / "src/helpers/AutoDiscoverRTCClock.h").read_text()
        # Compile every production method, replacing only platform dependencies.
        source = re.sub(r"^#include[^\n]*\n", "", source, flags=re.MULTILINE)
        header = re.sub(r"^#(?:include|pragma)[^\n]*\n", "", header, flags=re.MULTILINE)
        generated = HARNESS.replace("@HEADER@", header).replace("@SOURCE@", source)
        with tempfile.TemporaryDirectory(prefix="rtc-discovery-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(generated)
            for disabled in (False, True):
                binary = work / ("rtc-disabled" if disabled else "rtc")
                cmd = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       str(work / "test.cpp"), "-o", str(binary)]
                if disabled: cmd.insert(1, "-DDISABLE_DS3231_PROBE=1")
                if sys.platform.startswith("linux"):
                    cmd[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-fno-pie", "-no-pie"]
                built = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                self.assertRegex(tested.stdout, r"\d+ RTC discovery and fault checks passed")


if __name__ == "__main__":
    unittest.main()
