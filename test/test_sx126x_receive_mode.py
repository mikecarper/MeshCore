#!/usr/bin/env python3
"""Exercise the pinned RadioLib SPI transport with a fake Semtech status response."""
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
  unsigned transfers = 0;
  uint32_t time = 0;
  void pinMode(uint32_t,uint32_t) override {}
  void digitalWrite(uint32_t,uint32_t) override {}
  uint32_t digitalRead(uint32_t) override { return busy; }
  void attachInterrupt(uint32_t,void(*)(),uint32_t) override {}
  void detachInterrupt(uint32_t) override {}
  void delay(RadioLibTime_t ms) override { time += ms; }
  void delayMicroseconds(RadioLibTime_t) override {}
  RadioLibTime_t millis() override { return time; }
  RadioLibTime_t micros() override { return time * 1000; }
  long pulseIn(uint32_t,uint32_t,RadioLibTime_t) override { return 0; }
  void spiBegin() override {}
  void spiBeginTransaction() override {}
  void spiTransfer(uint8_t* out, size_t size, uint8_t* in) override {
    assert(size == 2 && out[0] == 0xC0);
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
  @LEGACY@
};
int main() {
  Hal hal;
  Module module(&hal,1,2,3,4);
  module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_CMD] = Module::BITS_8;
  module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_8;
  module.spiConfig.statusPos = 1;
  module.spiConfig.parseStatusCb = nullptr;
  module.spiConfig.checkStatusCb = nullptr;
  Radio radio{&module};
  assert(radio.getStatus() == 0); // reproduces the PR's false non-RX observation
  assert(sx126xReceiveMode(&radio) == 1);
  assert(module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] == 8);
  for (uint8_t mode : {0x24, 0x34, 0x44, 0x64}) {
    hal.status = mode;
    assert(sx126xReceiveMode(&radio) == 0);
  }
  for (uint8_t invalid : {0x00, 0xFF, 0x14, 0x74}) {
    hal.status = invalid;
    assert(sx126xReceiveMode(&radio) == -1);
    assert(module.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] == 8);
  }
  const unsigned before = hal.transfers;
  hal.busy = true;
  assert(sx126xReceiveMode(&radio) == -1);
  assert(hal.transfers == before); // no SPI while the chip is asleep/busy
}
'''


class ReceiveModeTest(unittest.TestCase):
    def test_real_spi_transport_and_legacy_status(self):
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
        legacy = commands[start:end].replace('SX126x::', '')
        with tempfile.TemporaryDirectory(prefix='meshcore-status-') as tmp:
            source = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / 'test'
            source.write_text(HARNESS.replace('@LEGACY@', legacy))
            command = [os.environ.get('CXX', 'c++'), '-std=c++17', '-O1',
                       '-DRADIOLIB_GODMODE=1', '-DRADIOLIB_STATIC_ONLY=1',
                       '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                       '-I', str(lib), '-I', str(ROOT / 'src'), str(source),
                       str(lib / 'Module.cpp'), str(lib / 'Hal.cpp'), '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
