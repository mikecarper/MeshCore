#!/usr/bin/env python3
"""Exercise legacy/current status reads and the mode probe on pinned SPI transport."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <RadioLib.h>
#include <helpers/radiolib/SX126xReceiveMode.h>
struct Hal : RadioLibHal {
  Hal() : RadioLibHal(0,1,0,1,1,0) {}
  bool busy = false;
  uint8_t status = 0x54;
  unsigned transfers = 0, gpio_reads = 0;
  Module* module = nullptr;
  Module::BitWidth_t transferred_status_width = Module::BITS_8;
  uint32_t time = 0;
  void pinMode(uint32_t,uint32_t) override {}
  void digitalWrite(uint32_t,uint32_t) override {}
  uint32_t digitalRead(uint32_t) override { ++gpio_reads; return busy; }
  void attachInterrupt(uint32_t,void(*)(),uint32_t) override {}
  void detachInterrupt(uint32_t) override {}
  void delay(RadioLibTime_t ms) override { time += ms; }
  void delayMicroseconds(RadioLibTime_t) override {}
  RadioLibTime_t millis() override { return time; }
  RadioLibTime_t micros() override { return time * 1000; }
  void yield() override { ++time; }
  long pulseIn(uint32_t,uint32_t,RadioLibTime_t) override { return 0; }
  void spiBegin() override {}
  void spiBeginTransaction() override {}
  void spiTransfer(uint8_t* out, size_t size, uint8_t* in) override {
    assert(size == 2 && out[0] == 0xC0);
    assert(out[1] == RADIOLIB_SX126X_CMD_NOP);
    transferred_status_width = module->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
    memset(in, 0, size);
    in[1] = status; // GetStatus has one response byte, no payload after it.
    ++transfers;
  }
  void spiEndTransaction() override {}
  void spiEnd() override {}
};
struct Radio {
  Module* mod;
  Module* getMod() { return mod; }
  // Explicit historical bug: zero payload leaves data untouched even though
  // the real Module transport clocks the status-prefix byte from the chip.
  uint8_t getLegacyStatus() {
    uint8_t data = 0;
    this->mod->SPIreadStream(RADIOLIB_SX126X_CMD_GET_STATUS, &data, 0);
    return data;
  }
  @CURRENT_GET_STATUS@
  static @STATUS_PARSER@
};

static void assertLayout(const Module& module, const Module::SPIConfig_t& before) {
  for (unsigned i = 0; i < 3; ++i) assert(module.spiConfig.widths[i] == before.widths[i]);
  for (unsigned i = 0; i < 4; ++i) assert(module.spiConfig.cmds[i] == before.cmds[i]);
  assert(module.spiConfig.statusPos == before.statusPos);
  assert(module.spiConfig.parseStatusCb == before.parseStatusCb);
  assert(module.spiConfig.checkStatusCb == before.checkStatusCb);
  assert(module.spiConfig.timeout == before.timeout);
  assert(module.spiConfig.stream == before.stream);
}

int main() {
  Hal hal;
  Module module(&hal,1,2,3,4);
  hal.module = &module;
  module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_ADDR] = Module::BITS_16;
  module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_CMD] = Module::BITS_8;
  module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_8;
  module.spiConfig.cmds[RADIOLIB_MODULE_SPI_COMMAND_NOP] = RADIOLIB_SX126X_CMD_NOP;
  module.spiConfig.statusPos = 1;
  module.spiConfig.parseStatusCb = Radio::SPIparseStatus;
  module.spiConfig.checkStatusCb = nullptr;
  Radio radio{&module};
  assert(radio.getLegacyStatus() == 0); // reproduce the old false non-RX observation
  assert(hal.transfers == 1 && hal.transferred_status_width == Module::BITS_8);
  assert(radio.getStatus() == 0x54); // the pinned implementation already fixes that bug
  assert(hal.transfers == 2 && hal.transferred_status_width == Module::BITS_0);
  assert(module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] == Module::BITS_8);
  assert(sx126xReceiveMode(&radio) == 1);
  assert(hal.transfers == 3 && hal.transferred_status_width == Module::BITS_0);
  struct Observation { uint8_t status; int8_t mode; };
  for (Observation observation : {
      Observation{0x24, 0}, {0x34, 0}, {0x44, 0}, {0x54, 1}, {0x64, 0},
      {0x00, -1}, {0xFF, -1}, {0x04, -1}, {0x14, -1}, {0x74, -1},
      {0x56, -1}, {0x58, -1}, {0x5A, -1}}) {
    hal.status = observation.status;
    // The current upstream method returns raw status, including command-error
    // responses. The helper must reject those through the real status parser.
    assert(radio.getStatus() == observation.status);
    assert(hal.transferred_status_width == Module::BITS_0);
    assert(module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] == Module::BITS_8);
    for (Module::BitWidth_t width : {
        Module::BITS_0, Module::BITS_8, Module::BITS_16, Module::BITS_24, Module::BITS_32}) {
      module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = width;
      const auto layout = module.spiConfig;
      const unsigned before = hal.transfers;
      assert(sx126xReceiveMode(&radio) == observation.mode);
      assert(hal.transfers == before + 1 && hal.transferred_status_width == Module::BITS_0);
      assertLayout(module, layout);
    }
    module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_8;
  }
  const unsigned before = hal.transfers;
  const auto layout = module.spiConfig;
  const uint32_t time = hal.time;
  hal.busy = true;
  assert(sx126xReceiveMode(&radio) == -1);
  assert(hal.transfers == before); // no SPI while the chip is asleep/busy
  assert(hal.time == time); // do not wait for the transport's BUSY timeout
  assertLayout(module, layout);

  // Unlike the helper, the pinned getStatus() waits for BUSY to time out.
  assert(radio.getStatus() == 0);
  assert(hal.transfers == before && hal.time >= time + module.spiConfig.timeout);
  assertLayout(module, layout);

  // A board with no BUSY pin must still be able to observe the response.
  Module no_busy(&hal,1,2,3,RADIOLIB_NC);
  no_busy.spiConfig = module.spiConfig;
  hal.module = &no_busy;
  hal.status = 0x54;
  const unsigned reads = hal.gpio_reads;
  Radio no_busy_radio{&no_busy};
  assert(sx126xReceiveMode(&no_busy_radio) == 1);
  assert(hal.transfers == before + 1 && hal.gpio_reads == reads);
  assertLayout(no_busy, layout);
}
'''


class ReceiveModeTest(unittest.TestCase):
    def test_real_spi_transport_legacy_current_and_mode_probe(self):
        candidates = sorted((ROOT / '.pio/libdeps').glob('*/RadioLib/src/Module.cpp'))
        pin = re.search(r'jgromes/RadioLib.git#([0-9a-f]{40})',
                        (ROOT / 'platformio.ini').read_text()).group(1)
        lib = None
        for candidate in candidates:
            installed = subprocess.run(
                ['git', '-C', str(candidate.parent.parent), 'rev-parse', 'HEAD'],
                capture_output=True, text=True)
            if installed.returncode == 0 and installed.stdout.strip() == pin:
                lib = candidate.parent
                break
        if lib is None:
            self.skipTest('Build a firmware environment to install the pinned RadioLib dependency')
        commands = (lib / 'modules/SX126x/SX126x_commands.cpp').read_text()
        start = commands.index('uint8_t SX126x::getStatus()')
        end = commands.index('\n}', start) + 2
        current = commands[start:end].replace('SX126x::', '')
        radio_source = (lib / 'modules/SX126x/SX126x.cpp').read_text()
        start = radio_source.index('int16_t SX126x::SPIparseStatus(')
        end = radio_source.index('\n}', start) + 2
        parser = radio_source[start:end].replace('SX126x::', '')
        with tempfile.TemporaryDirectory(prefix='meshcore-status-') as tmp:
            source = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / 'test'
            source.write_text(HARNESS.replace('@CURRENT_GET_STATUS@', current)
                              .replace('@STATUS_PARSER@', parser), encoding='ascii')
            command = [os.environ.get('CXX', 'c++'), '-std=c++17', '-O1',
                       '-DRADIOLIB_GODMODE=1', '-DRADIOLIB_STATIC_ONLY=1',
                       '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                       '-I', str(lib), '-I', str(ROOT / 'src'), str(source),
                       str(lib / 'Module.cpp'), str(lib / 'Hal.cpp'), '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
